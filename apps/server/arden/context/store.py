import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import aiosqlite
from pydantic import BaseModel

from arden.areas.paths import normalize_area_page_path
from arden.constants import (
    RAW_TOOL_RESULT_INLINE_MAX_BYTES,
    SESSION_EVENT_DURABLE_RETENTION,
    SESSION_EVENT_PRUNE_INTERVAL,
    SESSION_HANDOFF_MARKER,
)
from arden.context.models import SessionData, SessionState
from arden.core.raw_tool_results import (
    RawToolResultBlob,
    internal_blob_from_data,
    persist_raw_tool_result,
    preview_text,
    read_raw_tool_result,
    strip_internal_raw_tool_result_data,
)
from arden.events.internal import RunCompleted, RunFailed
from arden.events.sse import event_from_payload
from arden.logging import get_logger
from arden.outbox.store import OutboxStore
from arden.server.bus import StreamRecord

_logger = get_logger(__name__)

LATEST_VISIBLE_ANCHOR_ROW_LIMIT = 1000
# Hard bound on the string handed to the FTS5 MATCH parser. A very long query
# makes the parser run super-linearly and peg a core for minutes WITHOUT raising
# (so the except-fallback below never fires). Real search queries are short; an
# oversized one (e.g. a whole document passed as a query) is truncated here.
MAX_FTS_QUERY_CHARS = 500
_DURABLE_TOOL_RESULT_DATA_KEYS = (
    "child_agent",
    "workflow",
    "workflow_id",
    "usage",
    "cost",
    "html",
    "title",
    "mode",
    "provenance",
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS areas (
    area_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL,
    default_cwds TEXT NOT NULL DEFAULT '[]',
    instructions TEXT,
    knowledge_scope TEXT,
    page_path TEXT,
    page_id TEXT,
    autonomy TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_areas_archived_updated
    ON areas(archived_at, updated_at DESC);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    last_activity TEXT NOT NULL,
    messages TEXT,
    metadata TEXT,
    name TEXT,
    archived_at TEXT,
    session_type TEXT NOT NULL DEFAULT 'chat',
    origin_automation_id TEXT,
    parent_session_id TEXT,
    parent_tool_call_id TEXT,
    agent_type TEXT,
    agent_status TEXT,
    area_id TEXT REFERENCES areas(area_id) ON DELETE SET NULL,
    chat_model TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_activity ON sessions(last_activity);
CREATE INDEX IF NOT EXISTS idx_sessions_archived ON sessions(archived_at);
CREATE TABLE IF NOT EXISTS session_messages (
    session_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    role TEXT NOT NULL,
    message_json TEXT NOT NULL,
    client_id TEXT,
    created_at TEXT NOT NULL,
    search_text TEXT,
    PRIMARY KEY (session_id, message_id),
    UNIQUE (session_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_session_messages_session_seq
    ON session_messages(session_id, seq);
CREATE INDEX IF NOT EXISTS idx_session_messages_client
    ON session_messages(session_id, client_id);

CREATE TABLE IF NOT EXISTS session_turns (
    session_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    turn_index INTEGER NOT NULL,
    user_message_id TEXT NOT NULL,
    message_start_id TEXT NOT NULL,
    message_end_id TEXT NOT NULL,
    message_start_seq INTEGER NOT NULL,
    message_end_seq INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL,
    PRIMARY KEY (session_id, turn_id),
    UNIQUE (session_id, turn_index)
);

CREATE INDEX IF NOT EXISTS idx_session_turns_session_turn
    ON session_turns(session_id, turn_index);

CREATE TABLE IF NOT EXISTS chat_runs (
    run_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    status TEXT NOT NULL,
    stop_reason TEXT,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    ended_at TEXT,
    last_seq INTEGER,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    error_code TEXT,
    error_message TEXT,
    client_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_chat_runs_session_status
    ON chat_runs(session_id, status);

CREATE TABLE IF NOT EXISTS chat_queued_messages (
    client_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    status TEXT NOT NULL,
    message_json TEXT NOT NULL,
    enqueued_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    ingested_at TEXT,
    enqueued_seq INTEGER,
    ingested_seq INTEGER
);

CREATE INDEX IF NOT EXISTS idx_chat_queued_messages_session_status
    ON chat_queued_messages(session_id, status);
CREATE INDEX IF NOT EXISTS idx_chat_queued_messages_run_status
    ON chat_queued_messages(run_id, status);

CREATE TABLE IF NOT EXISTS chat_idempotency_keys (
    session_id TEXT NOT NULL,
    client_id TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    run_id TEXT,
    message_id TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    expires_at TEXT,
    PRIMARY KEY (session_id, client_id)
);

CREATE INDEX IF NOT EXISTS idx_chat_idempotency_run
    ON chat_idempotency_keys(run_id);
CREATE INDEX IF NOT EXISTS idx_chat_idempotency_expires
    ON chat_idempotency_keys(expires_at);

CREATE TABLE IF NOT EXISTS tool_calls (
    run_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    tool_call_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    action TEXT NOT NULL,
    scope TEXT NOT NULL,
    args_hash TEXT,
    status TEXT NOT NULL,
    result_preview TEXT,
    result_ref TEXT,
    outcome_json TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    PRIMARY KEY (run_id, tool_call_id)
);

CREATE INDEX IF NOT EXISTS idx_tool_calls_run
    ON tool_calls(run_id);
CREATE INDEX IF NOT EXISTS idx_tool_calls_session_started
    ON tool_calls(session_id, started_at);

CREATE TABLE IF NOT EXISTS tool_results (
    tool_result_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    run_id TEXT,
    tool_call_id TEXT NOT NULL,
    tool_name TEXT,
    content_sha256 TEXT NOT NULL,
    content_bytes INTEGER NOT NULL,
    stored_bytes INTEGER NOT NULL,
    compression TEXT NOT NULL,
    blob_ref TEXT NOT NULL,
    blob_path TEXT NOT NULL,
    preview TEXT,
    retention_class TEXT NOT NULL DEFAULT 'session',
    expires_at TEXT,
    source_event_seq INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE(session_id, tool_call_id, content_sha256)
);

CREATE INDEX IF NOT EXISTS idx_tool_results_session_created
    ON tool_results(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_tool_results_tool_call
    ON tool_results(tool_call_id);

CREATE TABLE IF NOT EXISTS tool_approvals (
    run_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    tool_call_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    action TEXT NOT NULL,
    scope TEXT NOT NULL,
    preview TEXT,
    diff TEXT,
    status TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    resolved_at TEXT,
    expires_at TEXT,
    result_feedback TEXT,
    kind TEXT NOT NULL DEFAULT 'tool_approval',
    payload_json TEXT,
    resolution_json TEXT,
    PRIMARY KEY (run_id, tool_call_id)
);

CREATE INDEX IF NOT EXISTS idx_tool_approvals_run
    ON tool_approvals(run_id);
CREATE INDEX IF NOT EXISTS idx_tool_approvals_session_status
    ON tool_approvals(session_id, status);

CREATE TABLE IF NOT EXISTS run_sidecars (
    run_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    context_manifest_json TEXT NOT NULL DEFAULT '[]',
    source_refs_json TEXT NOT NULL DEFAULT '[]',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session_events (
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    event_json TEXT NOT NULL,
    run_id TEXT,
    created_at TEXT NOT NULL,
    PRIMARY KEY (session_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_session_events_session_seq
    ON session_events(session_id, seq);
CREATE INDEX IF NOT EXISTS idx_session_events_run
    ON session_events(run_id);

CREATE TABLE IF NOT EXISTS chat_compactions (
    compaction_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    boundary_seq INTEGER NOT NULL,
    messages_before INTEGER NOT NULL,
    messages_after INTEGER NOT NULL,
    rehydration_state TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_chat_compactions_session_boundary
    ON chat_compactions(session_id, boundary_seq);

CREATE TABLE IF NOT EXISTS background_agent_runs (
    task_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    parent_run_id TEXT,
    parent_tool_call_id TEXT,
    child_session_id TEXT,
    agent_type TEXT NOT NULL DEFAULT 'background_research',
    wait INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    command TEXT NOT NULL,
    detail TEXT,
    result_ref TEXT,
    result_text TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    updated_at TEXT NOT NULL,
    ended_at TEXT,
    cancel_requested_at TEXT,
    notified_at TEXT,
    completion_id TEXT,
    spawn_spec TEXT,
    PRIMARY KEY (session_id, task_id)
);

CREATE INDEX IF NOT EXISTS idx_background_agent_runs_session_status
    ON background_agent_runs(session_id, status);

CREATE TABLE IF NOT EXISTS background_agent_events (
    session_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    task_id TEXT NOT NULL,
    status TEXT NOT NULL,
    detail TEXT,
    result_ref TEXT,
    terminal INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    event_id TEXT,
    delivered_at TEXT,
    PRIMARY KEY (session_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_background_agent_events_task
    ON background_agent_events(task_id);

CREATE TABLE IF NOT EXISTS session_goals (
    session_id TEXT PRIMARY KEY,
    goal_id TEXT NOT NULL,
    objective TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active', 'paused', 'blocked', 'budget_limited', 'complete')),
    evidence_json TEXT NOT NULL DEFAULT '[]',
    blocked_reason TEXT,
    token_budget INTEGER,
    tokens_used INTEGER NOT NULL DEFAULT 0,
    time_used_seconds INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session_todo_overrides (
    session_id TEXT PRIMARY KEY,
    items_json TEXT NOT NULL,
    explanation TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS session_todos (
    session_id TEXT PRIMARY KEY,
    items_json TEXT NOT NULL,
    explanation TEXT,
    updated_at TEXT NOT NULL
);
		"""

SQL_SAVE_SESSION = """
INSERT INTO sessions (
    session_id, started_at, last_activity, messages, metadata, name,
    session_type, origin_automation_id, parent_session_id, parent_tool_call_id,
    agent_type, agent_status, area_id, chat_model
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(session_id) DO UPDATE SET
    last_activity = excluded.last_activity,
    messages = excluded.messages,
    metadata = excluded.metadata,
    name = excluded.name,
    session_type = excluded.session_type,
    origin_automation_id = excluded.origin_automation_id,
    parent_session_id = excluded.parent_session_id,
    parent_tool_call_id = excluded.parent_tool_call_id,
    agent_type = excluded.agent_type,
    agent_status = excluded.agent_status,
    area_id = sessions.area_id,
    chat_model = excluded.chat_model
"""

SQL_INSERT_SESSION_IF_ABSENT = """
INSERT INTO sessions (
    session_id, started_at, last_activity, messages, metadata, name,
    session_type, origin_automation_id, parent_session_id, parent_tool_call_id,
    agent_type, agent_status, area_id, chat_model
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(session_id) DO NOTHING
"""

SQL_GET_LATEST = """
SELECT session_id FROM sessions
WHERE archived_at IS NULL
ORDER BY last_activity DESC LIMIT 1
"""

# {direction} is filled with DESC/ASC from a bool — never caller text.
SQL_LIST_SESSIONS = """
SELECT session_id, started_at, last_activity, name,
       session_type, origin_automation_id, parent_session_id, parent_tool_call_id,
       agent_type, agent_status, area_id, chat_model,
       json_array_length(COALESCE(messages, '[]')) AS message_count
FROM sessions
WHERE archived_at IS NULL
ORDER BY last_activity {direction}
LIMIT ? OFFSET ?
"""

SQL_LIST_PRIMARY_SESSIONS = """
SELECT session_id, started_at, last_activity, name,
       session_type, origin_automation_id, parent_session_id, parent_tool_call_id,
       agent_type, agent_status, area_id, chat_model,
       json_array_length(COALESCE(messages, '[]')) AS message_count
FROM sessions
WHERE archived_at IS NULL
  AND COALESCE(session_type, 'chat') != 'agent'
ORDER BY last_activity {direction}
LIMIT ? OFFSET ?
"""

SQL_LIST_ARCHIVED = """
SELECT session_id, started_at, last_activity, name, archived_at,
       session_type, origin_automation_id, parent_session_id, parent_tool_call_id,
       agent_type, agent_status, area_id, chat_model,
       json_array_length(COALESCE(messages, '[]')) AS message_count
FROM sessions
WHERE archived_at IS NOT NULL
ORDER BY archived_at DESC
LIMIT ?
"""

SQL_LOAD_SESSION = "SELECT * FROM sessions WHERE session_id = ?"
# Upsert: a fresh session won't have a row yet on its very first save,
# and an UPDATE-only would silently no-op (lost user message until the
# final end-of-run save).
SQL_UPSERT_PROGRESS = """
INSERT INTO sessions (
    session_id, started_at, last_activity, messages, metadata, name,
    session_type, origin_automation_id, parent_session_id, parent_tool_call_id,
    agent_type, agent_status, area_id, chat_model
)
VALUES (?, ?, ?, ?, '{}', ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(session_id) DO UPDATE SET
    messages = excluded.messages,
    last_activity = excluded.last_activity,
    agent_status = excluded.agent_status,
    area_id = sessions.area_id
"""
SQL_UPDATE_NAME = "UPDATE sessions SET name = ? WHERE session_id = ?"
SQL_UPDATE_NAME_IF_EMPTY = "UPDATE sessions SET name = ? WHERE session_id = ? AND (name IS NULL OR name = '')"
SQL_UPDATE_SESSION_AREA = "UPDATE sessions SET area_id = ? WHERE session_id = ?"
SQL_UPDATE_SESSION_CHAT_MODEL = "UPDATE sessions SET chat_model = ? WHERE session_id = ?"
SQL_SELECT_ARCHIVED_AT = "SELECT archived_at FROM sessions WHERE session_id = ?"
SQL_ARCHIVE = "UPDATE sessions SET archived_at = ? WHERE session_id = ? AND archived_at IS NULL"
SQL_RESTORE = "UPDATE sessions SET archived_at = NULL WHERE session_id = ? AND archived_at IS NOT NULL"
SQL_DELETE_ARCHIVED = "DELETE FROM sessions WHERE session_id = ? AND archived_at IS NOT NULL"

SQL_LOAD_SESSION_MESSAGES_COUNT = "SELECT 1 FROM session_messages WHERE session_id = ? LIMIT 1"
SQL_LOAD_SESSION_MESSAGES_JSON = "SELECT messages FROM sessions WHERE session_id = ?"
CHAT_IDEMPOTENCY_TTL_DAYS = 30
CHAT_IDEMPOTENCY_TERMINAL_STATUSES = ("completed", "cancelled", "error", "failed", "ingested", "interrupted")
# A real request hash is a SHA-256 hex digest, so this can never collide with
# one. It lets DELETE win even when it reaches the server before the matching
# POST has claimed its idempotency key.
CHAT_IDEMPOTENCY_CANCELLED_TOMBSTONE_HASH = "cancelled-before-submit"
AREA_FILTER_UNSET = object()
_AREA_PATCH_UNSET = object()


class SessionStore:
    def __init__(
        self,
        conn: aiosqlite.Connection,
        read_conn: aiosqlite.Connection | None = None,
        chat_completion_conn: aiosqlite.Connection | None = None,
    ):
        self.conn = conn
        self.read_conn = read_conn or conn
        self.chat_completion_conn = chat_completion_conn
        self._background_event_lock = asyncio.Lock()
        self._session_locks_guard = asyncio.Lock()
        self._session_write_locks: dict[str, asyncio.Lock] = {}
        # Durable-event writes per session since the last retention prune.
        self._events_since_prune: dict[str, int] = {}

    async def _session_write_lock(self, session_id: str) -> asyncio.Lock:
        async with self._session_locks_guard:
            lock = self._session_write_locks.get(session_id)
            if lock is None:
                lock = asyncio.Lock()
                self._session_write_locks[session_id] = lock
            return lock

    async def _update(self, sql: str, params: tuple) -> bool:
        cursor = await self.conn.execute(sql, params)
        await self.conn.commit()
        return cursor.rowcount > 0

    def _chat_run_payload(self, row: aiosqlite.Row) -> dict:
        columns = set(row.keys())
        return {
            "run_id": row["run_id"],
            "session_id": row["session_id"],
            "status": row["status"],
            "stop_reason": row["stop_reason"],
            "started_at": row["started_at"],
            "updated_at": row["updated_at"],
            "ended_at": row["ended_at"],
            "last_seq": row["last_seq"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "error_code": row["error_code"] if "error_code" in columns else None,
            "error_message": row["error_message"] if "error_message" in columns else None,
            "client_id": row["client_id"] if "client_id" in columns else None,
        }

    def _chat_queued_message_payload(self, row: aiosqlite.Row) -> dict:
        return {
            "client_id": row["client_id"],
            "session_id": row["session_id"],
            "run_id": row["run_id"],
            "status": row["status"],
            "message": json.loads(row["message_json"]),
            "enqueued_at": row["enqueued_at"],
            "updated_at": row["updated_at"],
            "ingested_at": row["ingested_at"],
            "enqueued_seq": row["enqueued_seq"],
            "ingested_seq": row["ingested_seq"],
        }

    def _chat_idempotency_payload(self, row: aiosqlite.Row) -> dict:
        return {
            "session_id": row["session_id"],
            "client_id": row["client_id"],
            "request_hash": row["request_hash"],
            "run_id": row["run_id"],
            "message_id": row["message_id"],
            "status": row["status"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "expires_at": row["expires_at"],
        }

    def _background_agent_payload(self, row: aiosqlite.Row) -> dict:
        return {
            "task_id": row["task_id"],
            "child_run_id": row["task_id"],
            "child_session_id": row["child_session_id"],
            "session_id": row["session_id"],
            "parent_run_id": row["parent_run_id"],
            "parent_tool_call_id": row["parent_tool_call_id"],
            "agent_type": row["agent_type"] or "background_research",
            "wait": bool(row["wait"]),
            "status": row["status"],
            "command": row["command"],
            "detail": row["detail"],
            "result_ref": row["result_ref"],
            "created_at": row["created_at"],
            "started_at": row["started_at"],
            "updated_at": row["updated_at"],
            "ended_at": row["ended_at"],
            "cancel_requested_at": row["cancel_requested_at"],
            "notified_at": row["notified_at"],
            "completion_id": dict(row).get("completion_id"),
        }

    def _background_agent_event_payload(self, row: aiosqlite.Row) -> dict:
        return {
            "session_id": row["session_id"],
            "seq": row["seq"],
            "task_id": row["task_id"],
            "status": row["status"],
            "detail": row["detail"],
            "result_ref": row["result_ref"],
            "terminal": bool(row["terminal"]),
            "created_at": row["created_at"],
            "event_id": dict(row).get("event_id"),
            "delivered_at": dict(row).get("delivered_at"),
        }

    def _tool_call_payload(self, row: aiosqlite.Row) -> dict:
        columns = set(row.keys())
        return {
            "run_id": row["run_id"],
            "session_id": row["session_id"],
            "tool_call_id": row["tool_call_id"],
            "tool_name": row["tool_name"],
            "action": row["action"],
            "scope": row["scope"],
            "args_hash": row["args_hash"],
            "status": row["status"],
            "result_preview": row["result_preview"],
            "result_ref": row["result_ref"],
            "outcome": json.loads(row["outcome_json"]) if "outcome_json" in columns and row["outcome_json"] else None,
            "started_at": row["started_at"],
            "ended_at": row["ended_at"],
        }

    def _tool_result_payload(self, row: aiosqlite.Row, *, content: str | None = None) -> dict:
        return {
            "tool_result_id": row["tool_result_id"],
            "session_id": row["session_id"],
            "run_id": row["run_id"],
            "tool_call_id": row["tool_call_id"],
            "tool_name": row["tool_name"],
            "content_sha256": row["content_sha256"],
            "content_bytes": row["content_bytes"],
            "stored_bytes": row["stored_bytes"],
            "compression": row["compression"],
            "blob_ref": row["blob_ref"],
            "blob_path": row["blob_path"],
            "preview": row["preview"],
            "retention_class": row["retention_class"],
            "expires_at": row["expires_at"],
            "source_event_seq": row["source_event_seq"],
            "created_at": row["created_at"],
            "content": content,
        }

    def _tool_approval_payload(self, row: aiosqlite.Row) -> dict:
        columns = set(row.keys())
        payload = json.loads(row["payload_json"] or "{}") if "payload_json" in columns else {}
        resolution = (
            json.loads(row["resolution_json"]) if "resolution_json" in columns and row["resolution_json"] else None
        )
        return {
            "run_id": row["run_id"],
            "session_id": row["session_id"],
            "suspension_id": row["tool_call_id"],
            "kind": row["kind"] if "kind" in columns else "tool_approval",
            "payload": payload,
            "resolution": resolution,
            "tool_call_id": row["tool_call_id"],
            "tool_name": row["tool_name"],
            "action": row["action"],
            "scope": row["scope"],
            "preview": row["preview"],
            "diff": row["diff"],
            "status": row["status"],
            "requested_at": row["requested_at"],
            "resolved_at": row["resolved_at"],
            "expires_at": row["expires_at"],
            "result_feedback": row["result_feedback"],
        }

    def _goal_payload(self, row: aiosqlite.Row) -> dict:
        return {
            "session_id": row["session_id"],
            "goal_id": row["goal_id"],
            "objective": row["objective"],
            "status": row["status"],
            "evidence": json.loads(row["evidence_json"] or "[]"),
            "blocked_reason": row["blocked_reason"],
            "token_budget": row["token_budget"],
            "tokens_used": int(row["tokens_used"] or 0),
            "time_used_seconds": int(row["time_used_seconds"] or 0),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _normalize_cwd(default_cwd: str | None) -> str | None:
        cwd = default_cwd.strip() if default_cwd else ""
        return cwd or None

    @staticmethod
    def _area_name_key(name: str) -> str:
        return name.strip().casefold()

    @staticmethod
    def _normalize_area_page_path(page_path: str | None) -> str | None:
        return None if page_path is None else normalize_area_page_path(page_path)

    async def _assert_area_name_available(self, name_key: str, *, exclude_area_id: str | None = None) -> None:
        sql = "SELECT area_id FROM areas WHERE name_key = ? AND archived_at IS NULL"
        params: list[object] = [name_key]
        if exclude_area_id is not None:
            sql += " AND area_id != ?"
            params.append(exclude_area_id)
        rows = await self.read_conn.execute_fetchall(sql, tuple(params))
        if rows:
            raise ValueError("An active Area with that name already exists")

    async def _assert_area_page_available(self, page_path: str | None, *, exclude_area_id: str | None = None) -> None:
        if page_path is None:
            return
        sql = "SELECT area_id FROM areas WHERE page_path = ?"
        params: list[object] = [page_path]
        if exclude_area_id is not None:
            sql += " AND area_id != ?"
            params.append(exclude_area_id)
        rows = await self.read_conn.execute_fetchall(sql, tuple(params))
        if rows:
            raise ValueError("That page is already attached to another Area")

    async def _assert_area_page_id_available(self, page_id: str | None, *, exclude_area_id: str | None = None) -> None:
        if page_id is None:
            return
        sql = "SELECT area_id FROM areas WHERE page_id = ?"
        params: list[object] = [page_id]
        if exclude_area_id is not None:
            sql += " AND area_id != ?"
            params.append(exclude_area_id)
        rows = await self.read_conn.execute_fetchall(sql, tuple(params))
        if rows:
            raise ValueError("That page is already attached to another Area")

    @staticmethod
    def _area_payload(row: aiosqlite.Row) -> dict:
        return {
            "area_id": row["area_id"],
            "name": row["name"],
            "default_cwd": (json.loads(row["default_cwds"] or "[]") or [None])[0],
            "instructions": row["instructions"],
            "knowledge_scope": row["knowledge_scope"] or f"area:{row['area_id']}",
            "page_path": row["page_path"],
            "page_id": row["page_id"],
            "autonomy": row["autonomy"],
            "attention": row["attention"] or "ambient",
            "interrupts": row["interrupts"] or "asks",
            "paused_at": row["paused_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "archived_at": row["archived_at"],
        }

    async def init_schema(self) -> None:
        await self._pre_migrate_tool_results_schema()
        await self.conn.executescript(SCHEMA)
        for col in (
            "name TEXT",
            "archived_at TEXT",
            "session_type TEXT NOT NULL DEFAULT 'chat'",
            "origin_automation_id TEXT",
            "parent_session_id TEXT",
            "parent_tool_call_id TEXT",
            "agent_type TEXT",
            "agent_status TEXT",
            "area_id TEXT REFERENCES areas(area_id) ON DELETE SET NULL",
            "chat_model TEXT",
        ):
            try:
                await self.conn.execute(f"ALTER TABLE sessions ADD COLUMN {col}")
                await self.conn.commit()
            except Exception:
                pass
        # Area capabilities on the container itself : an area with a page is an area; autonomy set means
        # it has a standing agent.
        for col in (
            "name_key TEXT",
            "page_path TEXT",
            "page_id TEXT",
            "autonomy TEXT",
            "attention TEXT",
            "interrupts TEXT",
            "paused_at TEXT",
        ):
            try:
                await self.conn.execute(f"ALTER TABLE areas ADD COLUMN {col}")
                await self.conn.commit()
            except Exception:
                pass
        await self.conn.execute("UPDATE areas SET name_key = lower(trim(name)) WHERE name_key IS NULL OR name_key = ''")
        try:
            await self.conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_areas_active_name_key "
                "ON areas(name_key) WHERE archived_at IS NULL"
            )
            await self.conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_areas_page_path ON areas(page_path) WHERE page_path IS NOT NULL"
            )
            await self.conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_areas_page_id ON areas(page_id) WHERE page_id IS NOT NULL"
            )
        except aiosqlite.IntegrityError as exc:
            raise RuntimeError(
                "Existing Areas violate name/page uniqueness; resolve duplicate Areas before starting"
            ) from exc
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_area_activity ON sessions(area_id, last_activity DESC)"
        )
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sessions_parent_activity ON sessions(parent_session_id, started_at)"
        )
        await self._migrate_session_messages_fts()
        await self._migrate_tool_calls_schema()
        await self._migrate_run_suspensions_schema()
        await self._migrate_background_agent_runs_schema()
        await self._migrate_background_agent_events_schema()
        await self._migrate_chat_compactions_schema()
        await self._migrate_chat_runs_schema()
        await self._migrate_drop_command_sidecar_sessions()
        await self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_chat_idempotency_expires ON chat_idempotency_keys(expires_at)"
        )
        await self.conn.commit()

    async def _pre_migrate_tool_results_schema(self) -> None:
        rows = await self.conn.execute_fetchall("PRAGMA table_info(tool_results)")
        if not rows:
            return
        columns = {row["name"] for row in rows}
        expected = {"tool_result_id", "session_id", "tool_call_id", "content_bytes", "blob_path"}
        if expected.issubset(columns):
            return

        existing = await self.conn.execute_fetchall(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'tool_results_legacy%'"
        )
        existing_names = {row["name"] for row in existing}
        legacy_name = "tool_results_legacy"
        suffix = 2
        while legacy_name in existing_names:
            legacy_name = f"tool_results_legacy_{suffix}"
            suffix += 1

        await self.conn.execute(f"ALTER TABLE tool_results RENAME TO {legacy_name}")
        await self.conn.commit()

    async def create_area(
        self,
        *,
        name: str,
        default_cwd: str | None = None,
        instructions: str | None = None,
        knowledge_scope: str | None = None,
        page_path: str | None = None,
        autonomy: str | None = None,
    ) -> dict:
        trimmed_name = name.strip()
        if not trimmed_name:
            raise ValueError("Area name cannot be blank")
        name_key = self._area_name_key(trimmed_name)
        normalized_page_path = self._normalize_area_page_path(page_path)
        await self._assert_area_name_available(name_key)
        await self._assert_area_page_available(normalized_page_path)
        area_id = f"area_{uuid4().hex[:12]}"
        now = datetime.now(UTC).isoformat()
        scope = (knowledge_scope or "").strip() or f"area:{area_id}"
        await self.conn.execute(
            """
            INSERT INTO areas (
                area_id, name, name_key, default_cwds, instructions, knowledge_scope,
                page_path, page_id, autonomy, created_at, updated_at, archived_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, NULL)
            """,
            (
                area_id,
                trimmed_name,
                name_key,
                json.dumps([self._normalize_cwd(default_cwd)] if self._normalize_cwd(default_cwd) else []),
                instructions.strip() if instructions and instructions.strip() else None,
                scope,
                normalized_page_path,
                autonomy,
                now,
                now,
            ),
        )
        await self.conn.commit()
        area = await self.get_area(area_id)
        if area is None:
            raise RuntimeError("area insert failed")
        return area

    async def get_area(self, area_id: str | None) -> dict | None:
        if not area_id:
            return None
        rows = await self.read_conn.execute_fetchall(
            "SELECT * FROM areas WHERE area_id = ? AND archived_at IS NULL",
            (area_id,),
        )
        return self._area_payload(rows[0]) if rows else None

    async def find_area_by_page_id(self, page_id: str) -> dict | None:
        rows = await self.read_conn.execute_fetchall(
            "SELECT * FROM areas WHERE page_id = ? AND archived_at IS NULL",
            (page_id,),
        )
        return self._area_payload(rows[0]) if rows else None

    async def list_areas(self) -> list[dict]:
        rows = await self.read_conn.execute_fetchall(
            """
            SELECT * FROM areas
            WHERE archived_at IS NULL
            ORDER BY updated_at DESC, created_at DESC
            """
        )
        return [self._area_payload(row) for row in rows]

    async def update_area(
        self,
        area_id: str,
        *,
        name: str | object = _AREA_PATCH_UNSET,
        default_cwd: str | object | None = _AREA_PATCH_UNSET,
        instructions: str | object | None = _AREA_PATCH_UNSET,
        knowledge_scope: str | object | None = _AREA_PATCH_UNSET,
        page_path: str | object | None = _AREA_PATCH_UNSET,
        page_id: str | object | None = _AREA_PATCH_UNSET,
        autonomy: str | object | None = _AREA_PATCH_UNSET,
        attention: str | object = _AREA_PATCH_UNSET,
        interrupts: str | object = _AREA_PATCH_UNSET,
        paused: bool | object = _AREA_PATCH_UNSET,
    ) -> dict | None:
        assignments = ["updated_at = ?"]
        params: list[object] = [datetime.now(UTC).isoformat()]
        if name is not _AREA_PATCH_UNSET:
            trimmed_name = str(name).strip()
            if not trimmed_name:
                raise ValueError("Area name cannot be blank")
            name_key = self._area_name_key(trimmed_name)
            await self._assert_area_name_available(name_key, exclude_area_id=area_id)
            assignments.append("name = ?")
            params.append(trimmed_name)
            assignments.append("name_key = ?")
            params.append(name_key)
        if default_cwd is not _AREA_PATCH_UNSET:
            assignments.append("default_cwds = ?")
            cwd = self._normalize_cwd(default_cwd if isinstance(default_cwd, str) else None)
            params.append(json.dumps([cwd] if cwd else []))
        if instructions is not _AREA_PATCH_UNSET:
            assignments.append("instructions = ?")
            text = instructions if isinstance(instructions, str) else None
            params.append(text.strip() if text and text.strip() else None)
        if knowledge_scope is not _AREA_PATCH_UNSET:
            assignments.append("knowledge_scope = ?")
            scope = knowledge_scope if isinstance(knowledge_scope, str) else None
            params.append(scope.strip() if scope and scope.strip() else f"area:{area_id}")
        if page_path is not _AREA_PATCH_UNSET:
            normalized_page_path = self._normalize_area_page_path(page_path if isinstance(page_path, str) else None)
            await self._assert_area_page_available(normalized_page_path, exclude_area_id=area_id)
            assignments.append("page_path = ?")
            params.append(normalized_page_path)
        if page_id is not _AREA_PATCH_UNSET:
            if page_id is not None and (not isinstance(page_id, str) or not page_id.strip()):
                raise ValueError("page_id must be a nonempty string")
            await self._assert_area_page_id_available(page_id, exclude_area_id=area_id)
            assignments.append("page_id = ?")
            params.append(page_id)
        if autonomy is not _AREA_PATCH_UNSET:
            assignments.append("autonomy = ?")
            params.append(autonomy if isinstance(autonomy, str) else None)
        if attention is not _AREA_PATCH_UNSET:
            if attention not in ("dormant", "ambient", "active"):
                raise ValueError("attention must be dormant | ambient | active")
            assignments.append("attention = ?")
            params.append(attention)
        if interrupts is not _AREA_PATCH_UNSET:
            if interrupts not in ("asks", "all", "none"):
                raise ValueError("interrupts must be asks | all | none")
            assignments.append("interrupts = ?")
            params.append(interrupts)
        if paused is not _AREA_PATCH_UNSET:
            assignments.append("paused_at = ?")
            params.append(datetime.now(UTC).isoformat() if paused else None)
        params.append(area_id)
        cursor = await self.conn.execute(
            f"UPDATE areas SET {', '.join(assignments)} WHERE area_id = ? AND archived_at IS NULL",
            tuple(params),
        )
        await self.conn.commit()
        if cursor.rowcount == 0:
            return None
        return await self.get_area(area_id)

    async def archive_area(self, area_id: str) -> bool:
        now = datetime.now(UTC).isoformat()
        cursor = await self.conn.execute(
            "UPDATE areas SET archived_at = ?, updated_at = ? WHERE area_id = ? AND archived_at IS NULL",
            (now, now, area_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def restore_area(self, area_id: str) -> dict | None:
        rows = await self.read_conn.execute_fetchall(
            "SELECT * FROM areas WHERE area_id = ? AND archived_at IS NOT NULL",
            (area_id,),
        )
        if not rows:
            return None
        row = rows[0]
        await self._assert_area_name_available(row["name_key"], exclude_area_id=area_id)
        now = datetime.now(UTC).isoformat()
        cursor = await self.conn.execute(
            "UPDATE areas SET archived_at = NULL, updated_at = ? WHERE area_id = ? AND archived_at IS NOT NULL",
            (now, area_id),
        )
        await self.conn.commit()
        return await self.get_area(area_id) if cursor.rowcount else None

    async def set_goal(
        self,
        session_id: str,
        objective: str,
        *,
        token_budget: int | None = None,
    ) -> dict:
        lock = await self._session_write_lock(session_id)
        async with lock:
            return await self._set_goal_unlocked(session_id, objective, token_budget=token_budget)

    async def _set_goal_unlocked(
        self,
        session_id: str,
        objective: str,
        *,
        token_budget: int | None = None,
    ) -> dict:
        now = datetime.now(UTC).isoformat()
        goal_id = uuid4().hex
        await self.conn.execute(
            """
            INSERT INTO session_goals (
                session_id, goal_id, objective, status, evidence_json,
                blocked_reason, token_budget, tokens_used, time_used_seconds,
                created_at, updated_at
            )
            VALUES (?, ?, ?, 'active', '[]', NULL, ?, 0, 0, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                goal_id = excluded.goal_id,
                objective = excluded.objective,
                status = excluded.status,
                evidence_json = excluded.evidence_json,
                blocked_reason = NULL,
                token_budget = excluded.token_budget,
                tokens_used = 0,
                time_used_seconds = 0,
                created_at = excluded.created_at,
                updated_at = excluded.updated_at
            """,
            (session_id, goal_id, objective, token_budget, now, now),
        )
        await self.conn.commit()
        goal = await self.get_goal(session_id)
        if goal is None:
            raise RuntimeError("goal insert failed")
        return goal

    async def get_goal(self, session_id: str) -> dict | None:
        rows = await self.read_conn.execute_fetchall(
            "SELECT * FROM session_goals WHERE session_id = ?",
            (session_id,),
        )
        return self._goal_payload(rows[0]) if rows else None

    async def set_todo_override(self, session_id: str, items: list[dict], explanation: str | None = None) -> dict:
        now = datetime.now(UTC).isoformat()
        lock = await self._session_write_lock(session_id)
        async with lock:
            await self.conn.execute(
                """
                INSERT INTO session_todo_overrides (session_id, items_json, explanation, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    items_json = excluded.items_json,
                    explanation = excluded.explanation,
                    updated_at = excluded.updated_at
                """,
                (session_id, json.dumps(items), explanation, now),
            )
            await self.conn.commit()
        return {"items": items, "explanation": explanation, "updated_at": now}

    async def get_todo_override(self, session_id: str) -> dict | None:
        rows = await self.read_conn.execute_fetchall(
            "SELECT items_json, explanation, updated_at FROM session_todo_overrides WHERE session_id = ?",
            (session_id,),
        )
        if not rows:
            return None
        row = rows[0]
        return {
            "items": json.loads(row["items_json"]),
            "explanation": row["explanation"],
            "updated_at": row["updated_at"],
        }

    async def clear_todo_override(self, session_id: str) -> bool:
        lock = await self._session_write_lock(session_id)
        async with lock:
            cursor = await self.conn.execute("DELETE FROM session_todo_overrides WHERE session_id = ?", (session_id,))
            await self.conn.commit()
            return cursor.rowcount > 0

    async def set_session_todos(self, session_id: str, items: list[dict], explanation: str | None = None) -> dict:
        now = datetime.now(UTC).isoformat()
        lock = await self._session_write_lock(session_id)
        async with lock:
            await self.conn.execute(
                """
                INSERT INTO session_todos (session_id, items_json, explanation, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    items_json = excluded.items_json,
                    explanation = excluded.explanation,
                    updated_at = excluded.updated_at
                """,
                (session_id, json.dumps(items), explanation, now),
            )
            await self.conn.commit()
        return {"items": items, "explanation": explanation, "updated_at": now}

    async def get_session_todos(self, session_id: str) -> dict | None:
        rows = await self.read_conn.execute_fetchall(
            "SELECT items_json, explanation, updated_at FROM session_todos WHERE session_id = ?",
            (session_id,),
        )
        if not rows:
            return None
        row = rows[0]
        return {
            "items": json.loads(row["items_json"]),
            "explanation": row["explanation"],
            "updated_at": row["updated_at"],
        }

    async def clear_session_todos(self, session_id: str) -> bool:
        lock = await self._session_write_lock(session_id)
        async with lock:
            cursor = await self.conn.execute("DELETE FROM session_todos WHERE session_id = ?", (session_id,))
            await self.conn.commit()
            return cursor.rowcount > 0

    async def clear_goal(self, session_id: str) -> bool:
        lock = await self._session_write_lock(session_id)
        async with lock:
            cursor = await self.conn.execute("DELETE FROM session_goals WHERE session_id = ?", (session_id,))
            await self.conn.commit()
            return cursor.rowcount > 0

    async def update_goal(
        self,
        session_id: str,
        *,
        goal_id: str | None = None,
        status: str | None = None,
        evidence: str | None = None,
        blocked_reason: str | None = None,
        evidence_kind: str | None = None,
        evidence_blocked_reason: str | None = None,
        tokens_used_delta: int = 0,
        time_used_seconds_delta: int = 0,
    ) -> dict | None:
        lock = await self._session_write_lock(session_id)
        async with lock:
            return await self._update_goal_unlocked(
                session_id,
                goal_id=goal_id,
                status=status,
                evidence=evidence,
                blocked_reason=blocked_reason,
                evidence_kind=evidence_kind,
                evidence_blocked_reason=evidence_blocked_reason,
                tokens_used_delta=tokens_used_delta,
                time_used_seconds_delta=time_used_seconds_delta,
            )

    async def _update_goal_unlocked(
        self,
        session_id: str,
        *,
        goal_id: str | None = None,
        status: str | None = None,
        evidence: str | None = None,
        blocked_reason: str | None = None,
        evidence_kind: str | None = None,
        evidence_blocked_reason: str | None = None,
        tokens_used_delta: int = 0,
        time_used_seconds_delta: int = 0,
    ) -> dict | None:
        current = await self.get_goal(session_id)
        if current is None:
            return None
        if goal_id is not None and current["goal_id"] != goal_id:
            return None
        next_evidence = list(current["evidence"])
        if evidence:
            evidence_entry = {"text": evidence, "created_at": datetime.now(UTC).isoformat()}
            if evidence_kind:
                evidence_entry["kind"] = evidence_kind
            if evidence_blocked_reason:
                evidence_entry["blocked_reason"] = evidence_blocked_reason
            next_evidence.append(evidence_entry)
        next_status = status or current["status"]
        next_tokens_used = current["tokens_used"] + max(0, tokens_used_delta)
        if (
            status is None
            and current.get("token_budget")
            and next_tokens_used >= current["token_budget"]
            and current["status"] == "active"
        ):
            next_status = "budget_limited"
        next_blocked_reason = blocked_reason if next_status == "blocked" else None
        now = datetime.now(UTC).isoformat()
        await self.conn.execute(
            """
            UPDATE session_goals
            SET status = ?,
                evidence_json = ?,
                blocked_reason = ?,
                tokens_used = tokens_used + ?,
                time_used_seconds = time_used_seconds + ?,
                updated_at = ?
            WHERE session_id = ?
            """,
            (
                next_status,
                json.dumps(next_evidence),
                next_blocked_reason,
                max(0, tokens_used_delta),
                max(0, time_used_seconds_delta),
                now,
                session_id,
            ),
        )
        await self.conn.commit()
        return await self.get_goal(session_id)

    async def _migrate_chat_compactions_schema(self) -> None:
        rows = await self.conn.execute_fetchall("PRAGMA table_info(chat_compactions)")
        columns = {row["name"] for row in rows}
        if "rehydration_state" in columns:
            return
        await self.conn.execute("ALTER TABLE chat_compactions ADD COLUMN rehydration_state TEXT")
        await self.conn.commit()

    _FTS_TRIGGERS = """
        CREATE TRIGGER IF NOT EXISTS session_messages_ai
        AFTER INSERT ON session_messages BEGIN
            INSERT INTO session_messages_fts(rowid, search_text)
            VALUES (new.rowid, new.search_text);
        END;

        CREATE TRIGGER IF NOT EXISTS session_messages_ad
        AFTER DELETE ON session_messages BEGIN
            INSERT INTO session_messages_fts(session_messages_fts, rowid, search_text)
            VALUES ('delete', old.rowid, old.search_text);
        END;

        CREATE TRIGGER IF NOT EXISTS session_messages_au
        AFTER UPDATE ON session_messages BEGIN
            INSERT INTO session_messages_fts(session_messages_fts, rowid, search_text)
            VALUES ('delete', old.rowid, old.search_text);
            INSERT INTO session_messages_fts(rowid, search_text)
            VALUES (new.rowid, new.search_text);
        END;
    """

    async def _migrate_session_messages_fts(self) -> None:
        """Full-text index over transcript messages. External-content FTS5
        keyed to session_messages.rowid, kept in sync by triggers so every
        write path stays correct. Indexes the flattened text projection
        (search_text), not the JSON envelope.

        Ordering matters: triggers are dropped before the column backfill so
        the AFTER UPDATE trigger can't issue a 'delete' against rows that were
        never indexed (which corrupts an external-content index). The index is
        rebuilt from content after, and a corrupt pre-existing index is healed
        rather than crashing boot."""
        # 1. Ensure the search_text column (CREATE TABLE only adds it fresh).
        cols = await self.conn.execute_fetchall("PRAGMA table_info(session_messages)")
        if "search_text" not in {c["name"] for c in cols}:
            await self.conn.execute("ALTER TABLE session_messages ADD COLUMN search_text TEXT")
            await self.conn.commit()

        # 2. Drop sync triggers so the backfill below runs without touching a
        #    half-built or corrupt FTS index.
        await self.conn.executescript(
            """
            DROP TRIGGER IF EXISTS session_messages_ai;
            DROP TRIGGER IF EXISTS session_messages_ad;
            DROP TRIGGER IF EXISTS session_messages_au;
            """
        )
        await self.conn.commit()

        # 3. Backfill flattened text for legacy rows (no triggers active).
        legacy = await self.conn.execute_fetchall(
            "SELECT rowid, message_json FROM session_messages WHERE search_text IS NULL"
        )
        for row in legacy:
            try:
                msg = json.loads(row["message_json"])
            except Exception:
                msg = {}
            await self.conn.execute(
                "UPDATE session_messages SET search_text = ? WHERE rowid = ?",
                (self._flatten_message_text(msg), row["rowid"]),
            )
        if legacy:
            await self.conn.commit()

        # 4. Create the FTS table if absent; heal it if a prior run left it
        #    corrupt. Either path rebuilds from content.
        existed = bool(
            await self.conn.execute_fetchall(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='session_messages_fts'"
            )
        )
        needs_rebuild = bool(legacy) or not existed
        if existed:
            try:
                await self.conn.execute(
                    "INSERT INTO session_messages_fts(session_messages_fts) VALUES('integrity-check')"
                )
            except Exception:
                await self.conn.execute("DROP TABLE IF EXISTS session_messages_fts")
                await self.conn.commit()
                existed = False
                needs_rebuild = True
        if not existed:
            await self.conn.execute(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS session_messages_fts USING fts5(
                    search_text,
                    content='session_messages',
                    content_rowid='rowid'
                )
                """
            )

        # 5. Recreate triggers, then rebuild the index from content if needed.
        await self.conn.executescript(self._FTS_TRIGGERS)
        if needs_rebuild:
            await self.conn.execute("INSERT INTO session_messages_fts(session_messages_fts) VALUES('rebuild')")
        await self.conn.commit()

    async def _migrate_chat_runs_schema(self) -> None:
        rows = await self.conn.execute_fetchall("PRAGMA table_info(chat_runs)")
        if not rows:
            return
        columns = {row["name"] for row in rows}
        changed = False
        for column in (
            "error_code TEXT",
            "error_message TEXT",
            "client_id TEXT",
        ):
            name = column.split()[0]
            if name in columns:
                continue
            await self.conn.execute(f"ALTER TABLE chat_runs ADD COLUMN {column}")
            changed = True
        if changed:
            await self.conn.commit()

    async def _migrate_drop_command_sidecar_sessions(self) -> None:
        # The command sidecar is gone; its hidden `command_*` scratch sessions
        # were only ever out of the sidebar because of a WHERE clause that no
        # longer exists.
        cursor = await self.conn.execute("DELETE FROM sessions WHERE agent_type = 'command_sidecar'")
        if cursor.rowcount:
            await self.conn.commit()

    async def _migrate_tool_calls_schema(self) -> None:
        rows = await self.conn.execute_fetchall("PRAGMA table_info(tool_calls)")
        if not rows:
            return

        pk_columns = [row["name"] for row in sorted(rows, key=lambda row: row["pk"]) if row["pk"]]
        if pk_columns != ["run_id", "tool_call_id"]:
            await self.conn.execute("ALTER TABLE tool_calls RENAME TO tool_calls_old")
            await self.conn.execute(
                """
                CREATE TABLE tool_calls (
                    run_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    tool_call_id TEXT NOT NULL,
                    tool_name TEXT NOT NULL,
                    action TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    args_hash TEXT,
                    status TEXT NOT NULL,
                    result_preview TEXT,
                    result_ref TEXT,
                    outcome_json TEXT,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    PRIMARY KEY (run_id, tool_call_id)
                )
                """
            )
            await self.conn.execute(
                """
                INSERT OR IGNORE INTO tool_calls (
                    run_id, session_id, tool_call_id, tool_name, action, scope,
                    args_hash, status, result_preview, started_at, ended_at
                )
                SELECT
                    run_id, session_id, tool_call_id, tool_name, action, scope,
                    args_hash, status, result_preview, started_at, ended_at
                FROM tool_calls_old
                """
            )
            await self.conn.execute("DROP TABLE tool_calls_old")
            await self.conn.execute("CREATE INDEX IF NOT EXISTS idx_tool_calls_run ON tool_calls(run_id)")
            await self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tool_calls_session_started ON tool_calls(session_id, started_at)"
            )
            await self.conn.commit()

        rows = await self.conn.execute_fetchall("PRAGMA table_info(tool_calls)")
        columns = {row["name"] for row in rows}
        if "result_ref" not in columns:
            await self.conn.execute("ALTER TABLE tool_calls ADD COLUMN result_ref TEXT")
        if "outcome_json" not in columns:
            await self.conn.execute("ALTER TABLE tool_calls ADD COLUMN outcome_json TEXT")
        await self.conn.commit()

    async def _migrate_run_suspensions_schema(self) -> None:
        rows = await self.conn.execute_fetchall("PRAGMA table_info(tool_approvals)")
        if not rows:
            return
        columns = {row["name"] for row in rows}
        for column in (
            "kind TEXT NOT NULL DEFAULT 'tool_approval'",
            "payload_json TEXT",
            "resolution_json TEXT",
        ):
            if column.split()[0] not in columns:
                await self.conn.execute(f"ALTER TABLE tool_approvals ADD COLUMN {column}")
        await self.conn.commit()

    async def _migrate_background_agent_runs_schema(self) -> None:
        rows = await self.conn.execute_fetchall("PRAGMA table_info(background_agent_runs)")
        if not rows:
            return

        columns = {row["name"] for row in rows}
        pk_columns = [row["name"] for row in sorted(rows, key=lambda row: row["pk"]) if row["pk"]]
        if "result_text" in columns and pk_columns == ["session_id", "task_id"]:
            changed = False
            if "parent_tool_call_id" not in columns:
                await self.conn.execute("ALTER TABLE background_agent_runs ADD COLUMN parent_tool_call_id TEXT")
                changed = True
            if "child_session_id" not in columns:
                await self.conn.execute("ALTER TABLE background_agent_runs ADD COLUMN child_session_id TEXT")
                changed = True
            if "agent_type" not in columns:
                await self.conn.execute(
                    "ALTER TABLE background_agent_runs ADD COLUMN agent_type TEXT NOT NULL DEFAULT 'background_research'"
                )
                changed = True
            if "wait" not in columns:
                await self.conn.execute("ALTER TABLE background_agent_runs ADD COLUMN wait INTEGER NOT NULL DEFAULT 0")
                changed = True
            if "completion_id" not in columns:
                await self.conn.execute("ALTER TABLE background_agent_runs ADD COLUMN completion_id TEXT")
                changed = True
            if "spawn_spec" not in columns:
                await self.conn.execute("ALTER TABLE background_agent_runs ADD COLUMN spawn_spec TEXT")
                changed = True
            if changed:
                await self.conn.commit()
            return

        result_text_expr = "result_text" if "result_text" in columns else "NULL"
        parent_tool_call_expr = "parent_tool_call_id" if "parent_tool_call_id" in columns else "NULL"
        child_session_expr = "child_session_id" if "child_session_id" in columns else "NULL"
        agent_type_expr = (
            "COALESCE(agent_type, 'background_research')" if "agent_type" in columns else "'background_research'"
        )
        wait_expr = "COALESCE(wait, 0)" if "wait" in columns else "0"
        await self.conn.execute("ALTER TABLE background_agent_runs RENAME TO background_agent_runs_old")
        await self.conn.execute(
            """
            CREATE TABLE background_agent_runs (
                task_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                parent_run_id TEXT,
                parent_tool_call_id TEXT,
                child_session_id TEXT,
                agent_type TEXT NOT NULL DEFAULT 'background_research',
                wait INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                command TEXT NOT NULL,
                detail TEXT,
                result_ref TEXT,
                result_text TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                updated_at TEXT NOT NULL,
                ended_at TEXT,
                cancel_requested_at TEXT,
                notified_at TEXT,
                completion_id TEXT,
                spawn_spec TEXT,
                PRIMARY KEY (session_id, task_id)
            )
            """
        )
        await self.conn.execute(
            f"""
            INSERT OR IGNORE INTO background_agent_runs (
                task_id, session_id, parent_run_id, parent_tool_call_id, child_session_id,
                agent_type, wait, status, command, detail, result_ref, result_text, created_at, started_at,
                updated_at, ended_at, cancel_requested_at, notified_at
            )
            SELECT
                task_id, session_id, parent_run_id, {parent_tool_call_expr}, {child_session_expr},
                {agent_type_expr}, {wait_expr}, status, command,
                detail, result_ref, {result_text_expr}, created_at, started_at,
                updated_at, ended_at, cancel_requested_at, notified_at
            FROM background_agent_runs_old
            """
        )
        await self.conn.execute("DROP TABLE background_agent_runs_old")
        await self.conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_background_agent_runs_session_status
                ON background_agent_runs(session_id, status)
            """
        )
        await self.conn.commit()

    async def _migrate_background_agent_events_schema(self) -> None:
        rows = await self.conn.execute_fetchall("PRAGMA table_info(background_agent_events)")
        if not rows:
            return
        columns = {row["name"] for row in rows}
        if "event_id" not in columns:
            await self.conn.execute("ALTER TABLE background_agent_events ADD COLUMN event_id TEXT")
        if "delivered_at" not in columns:
            await self.conn.execute("ALTER TABLE background_agent_events ADD COLUMN delivered_at TEXT")
        await self.conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_background_agent_events_event_id "
            "ON background_agent_events(event_id) WHERE event_id IS NOT NULL"
        )
        await self.conn.commit()

    def _to_serializable_messages(self, messages: list[dict | Any]) -> list[dict]:
        serializable: list[dict] = []
        for msg in messages:
            if isinstance(msg, BaseModel):
                serializable.append(msg.model_dump())
            elif isinstance(msg, dict):
                serializable.append(msg)
        return serializable

    def _stamp_messages(self, messages: list[dict], now: str) -> None:
        seen: set[str] = set()
        for msg in messages:
            if not msg.get("created_at"):
                msg["created_at"] = now

            message_id = msg.get("message_id") or msg.get("client_id")
            if not isinstance(message_id, str) or not message_id or message_id in seen:
                message_id = f"msg-{uuid4().hex[:16]}"
            msg["message_id"] = message_id
            seen.add(message_id)

    @staticmethod
    def _flatten_message_text(msg: dict) -> str:
        """Plain-text projection of a message for full-text search — the same
        text the agent reads, not the JSON envelope. Flattens content blocks
        (text/tool_use/tool_result) and drops image/base64 noise."""

        def walk(raw: Any) -> list[str]:
            if raw is None:
                return []
            if isinstance(raw, str):
                return [raw]
            if isinstance(raw, list):
                out: list[str] = []
                for block in raw:
                    if isinstance(block, dict):
                        t = block.get("type")
                        if t == "text" and block.get("text"):
                            out.append(str(block["text"]))
                        elif t == "tool_use" and block.get("name"):
                            out.append(str(block["name"]))
                        elif t == "tool_result":
                            out.extend(walk(block.get("content")))
                    elif isinstance(block, str):
                        out.append(block)
                return out
            if isinstance(raw, dict):
                return walk(raw.get("content"))
            return [str(raw)]

        return "\n".join(p for p in walk(msg.get("content")) if p).strip()

    async def _mirror_session_messages(self, session_id: str, messages: list[dict]) -> None:
        # session_messages is the durable UI/debug transcript, not just a
        # cache of the compacted model context. Rewrites update known rows
        # and append new ones, but must not delete raw pre-compaction rows.
        if not messages:
            await self.conn.execute("DELETE FROM session_messages WHERE session_id = ?", (session_id,))
            await self.conn.execute("DELETE FROM session_turns WHERE session_id = ?", (session_id,))
            return

        rows = await self.conn.execute_fetchall(
            "SELECT message_id, seq FROM session_messages WHERE session_id = ?",
            (session_id,),
        )
        existing = {row["message_id"]: row["seq"] for row in rows}
        next_seq = max(existing.values(), default=-1) + 1

        for msg in messages:
            message_id = msg.get("message_id")
            if not isinstance(message_id, str) or not message_id:
                continue

            role = str(msg.get("role") or "")
            client_id = msg.get("client_id") if isinstance(msg.get("client_id"), str) else None
            created_at = str(msg.get("created_at") or datetime.now(UTC).isoformat())
            message_json = await asyncio.to_thread(lambda m=msg: json.dumps(m, default=str))
            search_text = self._flatten_message_text(msg)

            if message_id in existing:
                await self.conn.execute(
                    """
                    UPDATE session_messages
                    SET role = ?, message_json = ?, client_id = ?, created_at = ?, search_text = ?
                    WHERE session_id = ? AND message_id = ?
                    """,
                    (role, message_json, client_id, created_at, search_text, session_id, message_id),
                )
            else:
                await self.conn.execute(
                    """
                    INSERT INTO session_messages
                        (session_id, message_id, seq, role, message_json, client_id, created_at, search_text)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (session_id, message_id, next_seq, role, message_json, client_id, created_at, search_text),
                )
                next_seq += 1
        await self._rebuild_session_turns(session_id)

    def _message_row_payload(self, row: aiosqlite.Row) -> dict:
        return {
            "session_id": row["session_id"],
            "message_id": row["message_id"],
            "seq": row["seq"],
            "role": row["role"],
            "client_id": row["client_id"],
            "created_at": row["created_at"],
            "message": json.loads(row["message_json"]),
        }

    def _is_turn_message(self, row: aiosqlite.Row) -> bool:
        if row["role"] == "system":
            return False
        message = json.loads(row["message_json"])
        content = message.get("content", "")
        return not (isinstance(content, str) and content.startswith(SESSION_HANDOFF_MARKER))

    async def _rebuild_session_turns(self, session_id: str) -> None:
        rows = await self.conn.execute_fetchall(
            "SELECT * FROM session_messages WHERE session_id = ? ORDER BY seq ASC",
            (session_id,),
        )
        await self.conn.execute("DELETE FROM session_turns WHERE session_id = ?", (session_id,))

        current_start: aiosqlite.Row | None = None
        current_end: aiosqlite.Row | None = None
        turn_index = 0

        async def flush_current() -> None:
            nonlocal current_start, current_end, turn_index
            if current_start is None or current_end is None:
                return
            turn_id = f"{session_id}:{turn_index}"
            await self.conn.execute(
                """
                INSERT INTO session_turns (
                    session_id, turn_id, turn_index, user_message_id,
                    message_start_id, message_end_id, message_start_seq, message_end_seq,
                    started_at, ended_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    turn_id,
                    turn_index,
                    current_start["message_id"],
                    current_start["message_id"],
                    current_end["message_id"],
                    current_start["seq"],
                    current_end["seq"],
                    current_start["created_at"],
                    current_end["created_at"],
                ),
            )
            turn_index += 1
            current_start = None
            current_end = None

        for row in rows:
            if not self._is_turn_message(row):
                continue
            if row["role"] == "user":
                await flush_current()
                current_start = row
            if current_start is not None:
                current_end = row

        await flush_current()

    async def _ensure_session_messages_unlocked(self, session_id: str) -> None:
        has_rows = await self.read_conn.execute_fetchall(SQL_LOAD_SESSION_MESSAGES_COUNT, (session_id,))
        if has_rows:
            return

        rows = await self.read_conn.execute_fetchall(SQL_LOAD_SESSION_MESSAGES_JSON, (session_id,))
        if not rows or not rows[0]["messages"]:
            return

        messages = await asyncio.to_thread(lambda: json.loads(rows[0]["messages"]))
        if not isinstance(messages, list) or not messages:
            return

        now = datetime.now(UTC).isoformat()
        serializable = [msg for msg in messages if isinstance(msg, dict)]
        self._stamp_messages(serializable, now)
        messages_json = await asyncio.to_thread(lambda: json.dumps(serializable, default=str))
        await self._mirror_session_messages(session_id, serializable)
        await self.conn.execute("UPDATE sessions SET messages = ? WHERE session_id = ?", (messages_json, session_id))
        await self.conn.commit()

    async def _ensure_session_messages(self, session_id: str) -> None:
        lock = await self._session_write_lock(session_id)
        async with lock:
            await self._ensure_session_messages_unlocked(session_id)

    async def update_progress(self, state: SessionState, messages: list[dict | Any]) -> None:
        """Lightweight mid-run save: rewrite messages + bump last_activity,
        upserting the row so a fresh session's first save lands instead of
        silently no-op'ing. Leaves metadata alone — the final save in the
        chat service re-stamps last_input_tokens."""
        lock = await self._session_write_lock(state.session_id)
        async with lock:
            serializable = self._to_serializable_messages(messages)
            now = datetime.now(UTC).isoformat()
            self._stamp_messages(serializable, now)

            messages_json = await asyncio.to_thread(lambda: json.dumps(serializable, default=str))
            await self.conn.execute(
                SQL_UPSERT_PROGRESS,
                (
                    state.session_id,
                    state.started_at.isoformat(),
                    now,
                    messages_json,
                    state.name,
                    state.session_type,
                    state.origin_automation_id,
                    state.parent_session_id,
                    state.parent_tool_call_id,
                    state.agent_type,
                    state.agent_status,
                    state.area_id,
                    state.chat_model,
                ),
            )
            await self._mirror_session_messages(state.session_id, serializable)
            await self.conn.commit()

    async def record_chat_run_started(
        self,
        run_id: str,
        session_id: str,
        *,
        metadata: dict | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        metadata = dict(metadata or {})
        client_id = metadata.get("client_id") if isinstance(metadata.get("client_id"), str) else None
        metadata_json = await asyncio.to_thread(lambda: json.dumps(metadata))
        try:
            await self.conn.execute(
                """
                INSERT INTO chat_runs (
                    run_id, session_id, status, started_at, updated_at, metadata_json, client_id
                )
                VALUES (?, ?, 'pending', ?, ?, ?, ?)
                """,
                (run_id, session_id, now, now, metadata_json, client_id),
            )
            await self.conn.execute(
                """
                UPDATE chat_runs
                SET status = 'interrupted',
                    stop_reason = 'superseded',
                    error_code = 'run_superseded',
                    error_message = 'Run was superseded by a newer run in the same session.',
                    updated_at = ?,
                    ended_at = ?
                WHERE session_id = ?
                  AND run_id != ?
                  AND status IN ('pending', 'running')
                """,
                (now, now, session_id, run_id),
            )
            await self.conn.commit()
        except BaseException:
            await self.conn.rollback()
            raise

    async def prune_expired_chat_idempotency_keys(self, now: datetime | None = None) -> int:
        now_iso = (now or datetime.now(UTC)).isoformat()
        cursor = await self.conn.execute(
            f"""
            DELETE FROM chat_idempotency_keys
            WHERE expires_at IS NOT NULL
              AND expires_at <= ?
              AND status IN ({", ".join("?" for _ in CHAT_IDEMPOTENCY_TERMINAL_STATUSES)})
            """,
            (now_iso, *CHAT_IDEMPOTENCY_TERMINAL_STATUSES),
        )
        await self.conn.commit()
        return cursor.rowcount

    async def claim_chat_idempotency_key(
        self,
        *,
        session_id: str,
        client_id: str,
        request_hash: str,
        status: str = "accepted",
        expires_at: str | None = None,
    ) -> tuple[bool, dict]:
        now_dt = datetime.now(UTC)
        now = now_dt.isoformat()
        expires_at = expires_at or (now_dt + timedelta(days=CHAT_IDEMPOTENCY_TTL_DAYS)).isoformat()
        await self.conn.execute(
            """
            INSERT OR IGNORE INTO chat_idempotency_keys (
                session_id, client_id, request_hash, status, created_at, updated_at, expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, client_id, request_hash, status, now, now, expires_at),
        )
        await self.conn.commit()
        row = await self.get_chat_idempotency_key(session_id, client_id)
        if row is None:
            raise RuntimeError("chat idempotency claim insert failed")
        return row["request_hash"] == request_hash and row["created_at"] == now, row

    async def get_chat_idempotency_key(self, session_id: str, client_id: str) -> dict | None:
        rows = await self.read_conn.execute_fetchall(
            """
            SELECT * FROM chat_idempotency_keys
            WHERE session_id = ? AND client_id = ?
            """,
            (session_id, client_id),
        )
        if not rows:
            return None
        return self._chat_idempotency_payload(rows[0])

    async def update_chat_idempotency_key(
        self,
        *,
        session_id: str,
        client_id: str,
        status: str,
        run_id: str | None = None,
        message_id: str | None = None,
    ) -> dict | None:
        now_dt = datetime.now(UTC)
        now = now_dt.isoformat()
        expires_at = (
            (now_dt + timedelta(days=CHAT_IDEMPOTENCY_TTL_DAYS)).isoformat()
            if status in CHAT_IDEMPOTENCY_TERMINAL_STATUSES
            else None
        )
        await self.conn.execute(
            """
            UPDATE chat_idempotency_keys
            SET status = ?,
                run_id = COALESCE(?, run_id),
                message_id = COALESCE(?, message_id),
                updated_at = ?,
                expires_at = COALESCE(?, expires_at)
            WHERE session_id = ? AND client_id = ?
            """,
            (status, run_id, message_id, now, expires_at, session_id, client_id),
        )
        await self.conn.commit()
        return await self.get_chat_idempotency_key(session_id, client_id)

    async def _upsert_cancelled_chat_idempotency(
        self,
        *,
        session_id: str,
        client_id: str,
        run_id: str | None,
        now: str,
        expires_at: str,
    ) -> None:
        """Durably make a client id terminal without replacing its request hash."""
        await self.conn.execute(
            """
            INSERT INTO chat_idempotency_keys (
                session_id, client_id, request_hash, run_id, status, created_at, updated_at, expires_at
            )
            VALUES (?, ?, ?, ?, 'cancelled', ?, ?, ?)
            ON CONFLICT(session_id, client_id) DO UPDATE SET
                status = 'cancelled',
                run_id = COALESCE(chat_idempotency_keys.run_id, excluded.run_id),
                updated_at = excluded.updated_at,
                expires_at = excluded.expires_at
            """,
            (
                session_id,
                client_id,
                CHAT_IDEMPOTENCY_CANCELLED_TOMBSTONE_HASH,
                run_id,
                now,
                now,
                expires_at,
            ),
        )

    async def cancel_chat_queued_message(
        self,
        *,
        session_id: str,
        client_id: str,
        run_id: str | None = None,
    ) -> str:
        """Cancel a queued message, or tombstone a client id before it queues.

        Returns ``cancelled`` when the client may discard its optimistic queue
        item and ``ingested`` once the agent has already consumed it.
        """
        now_dt = datetime.now(UTC)
        now = now_dt.isoformat()
        expires_at = (now_dt + timedelta(days=CHAT_IDEMPOTENCY_TTL_DAYS)).isoformat()
        rows = await self.conn.execute_fetchall(
            """
            SELECT status, run_id FROM chat_queued_messages
            WHERE session_id = ? AND client_id = ?
            """,
            (session_id, client_id),
        )
        if rows:
            row = rows[0]
            if row["status"] == "ingested":
                return "ingested"
            if row["status"] in {"queued", "failed_retryable", "cancelled"}:
                if row["status"] != "cancelled":
                    await self.conn.execute(
                        """
                        UPDATE chat_queued_messages
                        SET status = 'cancelled', updated_at = ?
                        WHERE session_id = ? AND client_id = ?
                          AND status IN ('queued', 'failed_retryable')
                        """,
                        (now, session_id, client_id),
                    )
                await self._upsert_cancelled_chat_idempotency(
                    session_id=session_id,
                    client_id=client_id,
                    run_id=row["run_id"],
                    now=now,
                    expires_at=expires_at,
                )
                await self.conn.commit()
                return "cancelled"
            return "ingested"

        idempotency = await self.get_chat_idempotency_key(session_id, client_id)
        if idempotency is not None:
            return "cancelled" if idempotency["status"] == "cancelled" else "ingested"

        await self._upsert_cancelled_chat_idempotency(
            session_id=session_id,
            client_id=client_id,
            run_id=run_id,
            now=now,
            expires_at=expires_at,
        )
        await self.conn.commit()
        return "cancelled"

    async def record_chat_run_status(
        self,
        run_id: str,
        status: str,
        *,
        stop_reason: str | None = None,
        last_seq: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        ended_at = now if status in {"completed", "cancelled", "error", "failed", "interrupted"} else None
        await self.conn.execute(
            """
            UPDATE chat_runs
            SET status = ?,
                stop_reason = ?,
                updated_at = ?,
                ended_at = ?,
                last_seq = COALESCE(?, last_seq),
                error_code = ?,
                error_message = ?
            WHERE run_id = ?
            """,
            (status, stop_reason, now, ended_at, last_seq, error_code, error_message, run_id),
        )
        await self.conn.commit()

    async def record_chat_run_completed_with_outbox(
        self,
        event: RunCompleted,
        *,
        stop_reason: str | None,
        last_seq: int | None,
    ) -> None:
        if self.chat_completion_conn is None:
            raise RuntimeError("chat completion connection is not configured")
        now = datetime.now(UTC).isoformat()
        conn = self.chat_completion_conn
        await conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = await conn.execute(
                """
                UPDATE chat_runs
                SET status = 'completed',
                    stop_reason = ?,
                    updated_at = ?,
                    ended_at = ?,
                    last_seq = COALESCE(?, last_seq),
                    error_code = NULL,
                    error_message = NULL
                WHERE run_id = ?
                """,
                (stop_reason, now, now, last_seq, event.run_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown chat run: {event.run_id}")
            outbox = OutboxStore(conn)
            await outbox.enqueue_run_completed_in_transaction(event)
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise

    async def record_chat_run_failed_with_outbox(
        self,
        event: RunFailed,
        *,
        status: str,
        stop_reason: str,
        last_seq: int | None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        if self.chat_completion_conn is None:
            raise RuntimeError("chat completion connection is not configured")
        now = datetime.now(UTC).isoformat()
        conn = self.chat_completion_conn
        await conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = await conn.execute(
                """
                UPDATE chat_runs
                SET status = ?,
                    stop_reason = ?,
                    updated_at = ?,
                    ended_at = ?,
                    last_seq = COALESCE(?, last_seq),
                    error_code = ?,
                    error_message = ?
                WHERE run_id = ?
                """,
                (
                    status,
                    stop_reason,
                    now,
                    now,
                    last_seq,
                    error_code,
                    error_message,
                    event.run_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown chat run: {event.run_id}")
            outbox = OutboxStore(conn)
            await outbox.enqueue_run_failed_in_transaction(event)
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise

    async def get_chat_run(self, run_id: str) -> dict | None:
        rows = await self.read_conn.execute_fetchall("SELECT * FROM chat_runs WHERE run_id = ?", (run_id,))
        if not rows:
            return None
        return self._chat_run_payload(rows[0])

    async def get_latest_chat_run_for_session(self, session_id: str) -> dict | None:
        rows = await self.read_conn.execute_fetchall(
            """
            SELECT * FROM chat_runs
            WHERE session_id = ?
            ORDER BY updated_at DESC, started_at DESC
            LIMIT 1
            """,
            (session_id,),
        )
        if not rows:
            return None
        return self._chat_run_payload(rows[0])

    async def list_interrupted_chat_runs(self) -> list[dict]:
        rows = await self.read_conn.execute_fetchall(
            """
            SELECT * FROM chat_runs
            WHERE status = 'interrupted' AND stop_reason = 'server_restart'
            ORDER BY updated_at ASC
            """
        )
        return [self._chat_run_payload(row) for row in rows]

    async def list_pending_tool_approvals(self, session_id: str, *, run_id: str | None = None) -> list[dict]:
        return await self.list_pending_run_suspensions(session_id, run_id=run_id, kind="tool_approval")

    async def list_pending_integration_connections(
        self,
        session_id: str,
        *,
        run_id: str | None = None,
    ) -> list[dict]:
        return await self.list_pending_run_suspensions(
            session_id,
            run_id=run_id,
            kind="integration_connection",
        )

    async def list_pending_run_suspensions(
        self,
        session_id: str,
        *,
        run_id: str | None = None,
        kind: str | None = None,
    ) -> list[dict]:
        conditions = ["session_id = ?", "status = 'pending'"]
        params: list[str] = [session_id]
        if run_id is not None:
            conditions.append("run_id = ?")
            params.append(run_id)
        if kind is not None:
            conditions.append("kind = ?")
            params.append(kind)
        rows = await self.read_conn.execute_fetchall(
            f"SELECT * FROM tool_approvals WHERE {' AND '.join(conditions)} ORDER BY requested_at ASC",
            tuple(params),
        )
        return [self._tool_approval_payload(row) for row in rows]

    async def list_all_pending_run_suspensions(self, *, kind: str | None = None) -> list[dict]:
        """Every pending suspension across all sessions — the queryable index
        of outstanding approvals, so the UI can surface them after a restart
        or from another session instead of scanning transcripts."""
        conditions = ["status = 'pending'"]
        params: list[str] = []
        if kind is not None:
            conditions.append("kind = ?")
            params.append(kind)
        rows = await self.read_conn.execute_fetchall(
            f"SELECT * FROM tool_approvals WHERE {' AND '.join(conditions)} ORDER BY requested_at ASC",
            tuple(params),
        )
        return [self._tool_approval_payload(row) for row in rows]

    async def record_tool_call_started(
        self,
        *,
        run_id: str,
        session_id: str,
        tool_call_id: str,
        tool_name: str,
        action: str,
        scope: str,
        args_hash: str | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        await self.conn.execute(
            """
            INSERT INTO tool_calls (
                run_id, session_id, tool_call_id, tool_name, action, scope,
                args_hash, status, started_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?)
            ON CONFLICT(run_id, tool_call_id) DO UPDATE SET
                run_id = excluded.run_id,
                session_id = excluded.session_id,
                tool_name = excluded.tool_name,
                action = excluded.action,
                scope = excluded.scope,
                args_hash = excluded.args_hash,
                status = excluded.status,
                result_preview = NULL,
                result_ref = NULL,
                outcome_json = NULL,
                started_at = excluded.started_at,
                ended_at = NULL
            WHERE tool_calls.status IN ('created', 'awaiting')
            """,
            (run_id, session_id, tool_call_id, tool_name, action, scope, args_hash, now),
        )
        await self.conn.commit()

    async def record_tool_call_created(
        self,
        *,
        run_id: str,
        session_id: str,
        tool_call_id: str,
        tool_name: str,
        action: str,
        scope: str,
        args_hash: str | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        await self.conn.execute(
            """
            INSERT OR IGNORE INTO tool_calls (
                run_id, session_id, tool_call_id, tool_name, action, scope,
                args_hash, status, started_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'created', ?)
            """,
            (run_id, session_id, tool_call_id, tool_name, action, scope, args_hash, now),
        )
        await self.conn.commit()

    async def record_tool_call_finished(
        self,
        *,
        run_id: str,
        tool_call_id: str,
        status: str,
        result_preview: str | None = None,
        outcome: dict | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        await self.conn.execute(
            """
            UPDATE tool_calls
            SET status = ?, result_preview = ?, outcome_json = ?, ended_at = ?
            WHERE run_id = ? AND tool_call_id = ?
            """,
            (
                status,
                result_preview,
                json.dumps(outcome, sort_keys=True, separators=(",", ":")) if outcome else None,
                now,
                run_id,
                tool_call_id,
            ),
        )
        await self.conn.commit()

    async def list_tool_calls(self, *, run_id: str) -> list[dict]:
        rows = await self.read_conn.execute_fetchall(
            "SELECT * FROM tool_calls WHERE run_id = ? ORDER BY started_at ASC",
            (run_id,),
        )
        return [self._tool_call_payload(row) for row in rows]

    async def list_tool_call_outcomes(
        self,
        *,
        session_id: str,
        tool_call_ids: list[str],
    ) -> dict[str, dict]:
        call_ids = list(dict.fromkeys(call_id for call_id in tool_call_ids if call_id))
        outcomes: dict[str, dict] = {}
        for offset in range(0, len(call_ids), 200):
            chunk = call_ids[offset : offset + 200]
            placeholders = ", ".join("?" for _ in chunk)
            rows = await self.read_conn.execute_fetchall(
                f"""
                SELECT tool_call_id, outcome_json
                FROM tool_calls
                WHERE session_id = ?
                  AND tool_call_id IN ({placeholders})
                  AND outcome_json IS NOT NULL
                ORDER BY ended_at ASC, started_at ASC
                """,
                (session_id, *chunk),
            )
            for row in rows:
                outcomes[row["tool_call_id"]] = json.loads(row["outcome_json"])
        return outcomes

    async def record_run_context_manifest(
        self,
        *,
        run_id: str,
        session_id: str,
        manifest: list[dict],
    ) -> None:
        now = datetime.now(UTC).isoformat()
        manifest_json = json.dumps(manifest, sort_keys=True, separators=(",", ":"))
        await self.conn.execute(
            """
            INSERT INTO run_sidecars (run_id, session_id, context_manifest_json, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                session_id = excluded.session_id,
                context_manifest_json = excluded.context_manifest_json,
                updated_at = excluded.updated_at
            """,
            (run_id, session_id, manifest_json, now),
        )
        await self.conn.commit()

    async def record_run_evidence(
        self,
        *,
        run_id: str,
        session_id: str,
        source_refs: list[dict],
    ) -> dict:
        calls = await self.list_tool_calls(run_id=run_id)
        approval_rows = await self.read_conn.execute_fetchall(
            "SELECT * FROM tool_approvals WHERE run_id = ? AND kind = 'tool_approval' ORDER BY requested_at ASC",
            (run_id,),
        )
        approvals = [
            {
                "tool_call_id": row["tool_call_id"],
                "tool_name": row["tool_name"],
                "status": row["status"],
                **({"feedback": row["result_feedback"]} if row["result_feedback"] else {}),
            }
            for row in approval_rows
        ]
        effects: list[dict] = []
        receipts: list[dict] = []
        checks: list[dict] = []
        limitations: list[dict] = []
        for call in calls:
            outcome = call.get("outcome") or {}
            tool_call_id = call["tool_call_id"]
            if effect := outcome.get("effect"):
                effects.append({"tool_call_id": tool_call_id, **effect})
            if receipt := outcome.get("receipt"):
                receipts.append({"tool_call_id": tool_call_id, "receipt": receipt})
            if verification := outcome.get("verification"):
                checks.append({"tool_call_id": tool_call_id, **verification})
            outcome_status = outcome.get("status")
            if outcome_status and outcome_status != "succeeded":
                error = outcome.get("error") or {}
                limitations.append(
                    {
                        "tool_call_id": tool_call_id,
                        "status": outcome_status,
                        "code": error.get("code") or outcome_status,
                        **({"recovery_action": error["recovery_action"]} if error.get("recovery_action") else {}),
                    }
                )
            elif call["status"] == "running":
                limitations.append(
                    {
                        "tool_call_id": tool_call_id,
                        "status": "uncertain",
                        "code": "execution_state_uncertain",
                        "recovery_action": "Verify whether the operation completed before retrying.",
                    }
                )
        sources = sorted(source_refs, key=lambda ref: (str(ref.get("provider", "")), str(ref.get("ref", ""))))
        evidence = {
            "sources": sources,
            "approvals": approvals,
            "effects": effects,
            "receipts": receipts,
            "checks": checks,
            "limitations": limitations,
        }
        now = datetime.now(UTC).isoformat()
        await self.conn.execute(
            """
            INSERT INTO run_sidecars (
                run_id, session_id, source_refs_json, evidence_json, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                session_id = excluded.session_id,
                source_refs_json = excluded.source_refs_json,
                evidence_json = excluded.evidence_json,
                updated_at = excluded.updated_at
            """,
            (
                run_id,
                session_id,
                json.dumps(sources, sort_keys=True, separators=(",", ":")),
                json.dumps(evidence, sort_keys=True, separators=(",", ":")),
                now,
            ),
        )
        await self.conn.commit()
        return evidence

    async def get_run_sidecars(self, run_id: str) -> dict | None:
        rows = await self.read_conn.execute_fetchall("SELECT * FROM run_sidecars WHERE run_id = ?", (run_id,))
        if not rows:
            return None
        row = rows[0]
        return {
            "run_id": row["run_id"],
            "session_id": row["session_id"],
            "context_manifest": json.loads(row["context_manifest_json"]),
            "source_refs": json.loads(row["source_refs_json"]),
            "evidence": json.loads(row["evidence_json"]),
            "updated_at": row["updated_at"],
        }

    async def get_run_sidecars_for_turn(self, *, session_id: str, turn_id: str) -> dict | None:
        run_id = await self._run_id_for_turn(session_id=session_id, turn_id=turn_id)
        if run_id is None:
            return None
        sidecars = await self.get_run_sidecars(run_id)
        if sidecars is None or sidecars["session_id"] != session_id:
            return None
        return sidecars

    async def _run_id_for_turn(self, *, session_id: str, turn_id: str) -> str | None:
        meta_prefix = "meta-user-"
        if turn_id.startswith(meta_prefix):
            run_id = turn_id.removeprefix(meta_prefix)
            rows = await self.read_conn.execute_fetchall(
                "SELECT run_id FROM chat_runs WHERE session_id = ? AND run_id = ? LIMIT 1",
                (session_id, run_id),
            )
            return rows[0]["run_id"] if rows else None

        rows = await self.read_conn.execute_fetchall(
            """
            SELECT run_id FROM chat_runs
            WHERE session_id = ? AND client_id = ?
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (session_id, turn_id),
        )
        if rows:
            return rows[0]["run_id"]

        rows = await self.read_conn.execute_fetchall(
            """
            SELECT run_id FROM chat_queued_messages
            WHERE session_id = ? AND client_id = ? AND status = 'ingested'
            LIMIT 1
            """,
            (session_id, turn_id),
        )
        return rows[0]["run_id"] if rows else None

    async def get_tool_result(self, tool_result_id: str) -> dict | None:
        rows = await self.read_conn.execute_fetchall(
            "SELECT * FROM tool_results WHERE tool_result_id = ?",
            (tool_result_id,),
        )
        if not rows:
            return None
        row = rows[0]
        content = await asyncio.to_thread(read_raw_tool_result, row["blob_path"], compression=row["compression"])
        return self._tool_result_payload(row, content=content)

    async def get_tool_result_for_call(self, *, run_id: str, tool_call_id: str) -> dict | None:
        rows = await self.read_conn.execute_fetchall(
            """
            SELECT * FROM tool_results
            WHERE run_id = ? AND tool_call_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (run_id, tool_call_id),
        )
        if not rows:
            return None
        row = rows[0]
        content = await asyncio.to_thread(read_raw_tool_result, row["blob_path"], compression=row["compression"])
        return self._tool_result_payload(row, content=content)

    async def record_tool_approval_requested(
        self,
        *,
        run_id: str,
        session_id: str,
        tool_call_id: str,
        tool_name: str,
        action: str,
        scope: str,
        preview: str | None = None,
        diff: str | None = None,
        expires_at: str | None = None,
        description: str | None = None,
        agent_type: str | None = None,
        agent_name: str | None = None,
        parent_session_id: str | None = None,
    ) -> None:
        await self.record_run_suspension(
            run_id=run_id,
            session_id=session_id,
            suspension_id=tool_call_id,
            kind="tool_approval",
            payload={
                "tool_name": tool_name,
                "action": action,
                "scope": scope,
                "preview": preview,
                "diff": diff,
                "description": description,
                "agent_type": agent_type,
                "agent_name": agent_name,
                "parent_session_id": parent_session_id,
            },
            expires_at=expires_at,
        )

    async def record_integration_connection_requested(
        self,
        *,
        run_id: str,
        session_id: str,
        tool_call_id: str,
        descriptor,
        source: str,
        detail: str,
        expires_at: str | None = None,
    ) -> None:
        await self.record_run_suspension(
            run_id=run_id,
            session_id=session_id,
            suspension_id=tool_call_id,
            kind="integration_connection",
            payload={
                "tool_name": "request_connection",
                "action": descriptor.action,
                "scope": "external",
                "integration_id": descriptor.integration_id,
                "connection_id": descriptor.connection_id,
                "label": descriptor.label,
                "reason": descriptor.state,
                "detail": detail,
                "capability": descriptor.capability,
                "settings_tab": descriptor.settings_tab,
                "required_scopes": list(descriptor.required_scopes),
                "tool_names": list(descriptor.tool_names),
                "source": source,
            },
            expires_at=expires_at,
        )

    async def record_run_suspension(
        self,
        *,
        run_id: str,
        session_id: str,
        suspension_id: str,
        kind: str,
        payload: dict,
        expires_at: str | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        tool_name = str(payload.get("tool_name") or kind)
        action = str(payload.get("action") or "suspend")
        scope = str(payload.get("scope") or "internal")
        preview = payload.get("preview")
        diff = payload.get("diff")
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        await self.conn.execute(
            """
            INSERT INTO tool_approvals (
                run_id, session_id, tool_call_id, tool_name, action, scope,
                preview, diff, status, requested_at, expires_at, kind, payload_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)
            ON CONFLICT(run_id, tool_call_id) DO UPDATE SET
                session_id = excluded.session_id,
                tool_name = excluded.tool_name,
                action = excluded.action,
                scope = excluded.scope,
                preview = excluded.preview,
                diff = excluded.diff,
                requested_at = excluded.requested_at,
                expires_at = excluded.expires_at,
                kind = excluded.kind,
                payload_json = excluded.payload_json
            WHERE tool_approvals.status = 'pending'
            """,
            (
                run_id,
                session_id,
                suspension_id,
                tool_name,
                action,
                scope,
                preview,
                diff,
                now,
                expires_at,
                kind,
                payload_json,
            ),
        )
        await self.conn.execute(
            """
            UPDATE tool_calls SET status = 'awaiting'
            WHERE run_id = ? AND tool_call_id = ? AND status IN ('created', 'running')
            """,
            (run_id, suspension_id),
        )
        await self.conn.commit()

    async def resolve_tool_approval(
        self,
        *,
        run_id: str,
        tool_call_id: str,
        status: str,
        result_feedback: str | None = None,
        source: str | None = None,
    ) -> bool:
        resolution = {"approved": status == "approved", "result": result_feedback or ""}
        if source:
            resolution["source"] = source
        return await self.resolve_run_suspension(
            run_id=run_id,
            suspension_id=tool_call_id,
            status=status,
            resolution=resolution,
        )

    async def resolve_integration_connection(
        self,
        *,
        run_id: str,
        tool_call_id: str,
        status: str,
        result_feedback: str | None = None,
    ) -> bool:
        return await self.resolve_run_suspension(
            run_id=run_id,
            suspension_id=tool_call_id,
            status=status,
            resolution={"approved": status == "approved", "result": result_feedback or ""},
        )

    async def resolve_run_suspension(
        self,
        *,
        run_id: str,
        suspension_id: str,
        status: str,
        resolution: dict | None = None,
    ) -> bool:
        now = datetime.now(UTC).isoformat()
        resolution_json = json.dumps(resolution, sort_keys=True, separators=(",", ":")) if resolution else None
        result_feedback = resolution.get("result") if resolution else None
        cursor = await self.conn.execute(
            """
            UPDATE tool_approvals
            SET status = ?,
                resolved_at = COALESCE(resolved_at, ?),
                result_feedback = COALESCE(?, result_feedback),
                resolution_json = COALESCE(?, resolution_json)
            WHERE run_id = ? AND tool_call_id = ?
              AND status = 'pending'
            """,
            (status, now, result_feedback, resolution_json, run_id, suspension_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def expire_tool_approval(
        self,
        *,
        run_id: str,
        tool_call_id: str,
        result_feedback: str | None = None,
        source: str | None = None,
    ) -> bool:
        return await self.resolve_tool_approval(
            run_id=run_id,
            tool_call_id=tool_call_id,
            status="expired",
            result_feedback=result_feedback,
            source=source or "timeout",
        )

    async def get_tool_approval(self, *, run_id: str, tool_call_id: str) -> dict | None:
        return await self.get_run_suspension(run_id=run_id, suspension_id=tool_call_id)

    async def get_run_suspension(self, *, run_id: str, suspension_id: str) -> dict | None:
        rows = await self.read_conn.execute_fetchall(
            "SELECT * FROM tool_approvals WHERE run_id = ? AND tool_call_id = ?",
            (run_id, suspension_id),
        )
        if not rows:
            return None
        return self._tool_approval_payload(rows[0])

    async def mark_run_suspension_consumed(self, *, run_id: str, suspension_id: str) -> bool:
        return await self._update(
            """
            UPDATE tool_calls SET status = 'running'
            WHERE run_id = ? AND tool_call_id = ? AND status = 'awaiting'
            """,
            (run_id, suspension_id),
        )

    async def mark_interrupted_chat_runs(self) -> int:
        now = datetime.now(UTC).isoformat()
        cursor = await self.conn.execute(
            """
            UPDATE chat_runs
            SET status = 'interrupted',
                stop_reason = 'server_restart',
                error_code = 'run_interrupted',
                error_message = 'Run was interrupted by server restart.',
                updated_at = ?,
                ended_at = ?
            WHERE status IN ('pending', 'running', 'backgrounded')
            """,
            (now, now),
        )
        await self.conn.commit()
        return cursor.rowcount

    async def record_background_agent_started(
        self,
        *,
        task_id: str,
        session_id: str,
        parent_run_id: str | None,
        command: str,
        parent_tool_call_id: str | None = None,
        child_session_id: str | None = None,
        agent_type: str = "background_research",
        wait: bool = False,
        spawn_spec: str | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        cursor = await self.conn.execute(
            """
            INSERT INTO background_agent_runs (
                task_id, session_id, parent_run_id, parent_tool_call_id, child_session_id,
                agent_type, wait, status, command, spawn_spec,
                created_at, started_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?)
            ON CONFLICT(session_id, task_id) DO UPDATE SET
                session_id = excluded.session_id,
                parent_run_id = excluded.parent_run_id,
                parent_tool_call_id = excluded.parent_tool_call_id,
                child_session_id = excluded.child_session_id,
                agent_type = excluded.agent_type,
                wait = excluded.wait,
                status = 'running',
                command = excluded.command,
                spawn_spec = COALESCE(excluded.spawn_spec, spawn_spec),
                detail = NULL,
                result_ref = NULL,
                result_text = NULL,
                updated_at = excluded.updated_at,
                ended_at = NULL,
                cancel_requested_at = NULL,
                notified_at = NULL,
                completion_id = NULL
            WHERE background_agent_runs.completion_id IS NULL
            """,
            (
                task_id,
                session_id,
                parent_run_id,
                parent_tool_call_id,
                child_session_id,
                agent_type,
                int(wait),
                command,
                spawn_spec,
                now,
                now,
                now,
            ),
        )
        await self.conn.commit()
        if cursor.rowcount > 0:
            await self.record_background_agent_event(
                task_id=task_id,
                session_id=session_id,
                status="started",
            )

    async def record_background_agent_event(
        self,
        *,
        task_id: str,
        session_id: str,
        status: str,
        detail: str | None = None,
        result_ref: str | None = None,
    ) -> int:
        terminal = status in {"completed", "failed", "cancelled", "interrupted"}
        now = datetime.now(UTC).isoformat()
        async with self._background_event_lock:
            rows = await self.conn.execute_fetchall(
                """
                SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq
                FROM background_agent_events
                WHERE session_id = ?
                """,
                (session_id,),
            )
            seq = int(rows[0]["next_seq"])
            await self.conn.execute(
                """
                INSERT INTO background_agent_events (
                    session_id, seq, task_id, status, detail, result_ref, terminal, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, seq, task_id, status, detail, result_ref, int(terminal), now),
            )
            await self.conn.execute(
                """
            UPDATE background_agent_runs
            SET detail = COALESCE(?, detail),
                result_ref = COALESCE(?, result_ref),
                updated_at = ?
            WHERE session_id = ? AND task_id = ?
            """,
                (detail, result_ref, now, session_id, task_id),
            )
            await self.conn.commit()
        return seq

    async def record_background_agent_finished(
        self,
        *,
        task_id: str,
        session_id: str,
        status: str,
        result_ref: str | None = None,
        detail: str | None = None,
        result_text: str | None = None,
    ) -> None:
        await self.claim_background_agent_completion(
            task_id=task_id,
            session_id=session_id,
            status=status,
            detail=detail,
            result_ref=result_ref,
            result_text=result_text,
            completion_id=f"bg:{task_id}:{status}",
        )

    async def claim_background_agent_completion(
        self,
        *,
        task_id: str,
        session_id: str,
        status: str,
        completion_id: str,
        result_ref: str | None = None,
        detail: str | None = None,
        result_text: str | None = None,
    ) -> dict:
        now = datetime.now(UTC).isoformat()
        async with self._background_event_lock:
            rows = await self.conn.execute_fetchall(
                "SELECT * FROM background_agent_runs WHERE session_id = ? AND task_id = ?",
                (session_id, task_id),
            )
            if not rows:
                raise KeyError(f"Unknown background task {session_id}/{task_id}")
            existing = rows[0]
            if existing["completion_id"]:
                return {
                    "claimed": False,
                    "delivered": existing["notified_at"] is not None,
                    "completion_id": existing["completion_id"],
                    "status": existing["status"],
                    "result_ref": existing["result_ref"],
                    "result_text": existing["result_text"],
                }

            seq_rows = await self.conn.execute_fetchall(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM background_agent_events WHERE session_id = ?",
                (session_id,),
            )
            seq = int(seq_rows[0]["next_seq"])
            await self.conn.execute(
                """
                UPDATE background_agent_runs
                SET status = ?, detail = COALESCE(?, detail), result_ref = COALESCE(?, result_ref),
                    result_text = COALESCE(?, result_text), completion_id = ?, updated_at = ?, ended_at = ?
                WHERE session_id = ? AND task_id = ? AND completion_id IS NULL
                """,
                (status, detail, result_ref, result_text, completion_id, now, now, session_id, task_id),
            )
            await self.conn.execute(
                """
                INSERT INTO background_agent_events (
                    session_id, seq, task_id, status, detail, result_ref, terminal, created_at, event_id
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (session_id, seq, task_id, status, detail, result_ref, now, completion_id),
            )
            await self.conn.commit()
        return {
            "claimed": True,
            "delivered": False,
            "completion_id": completion_id,
            "status": status,
            "result_ref": result_ref,
            "result_text": result_text,
        }

    async def mark_background_completion_delivered(
        self,
        *,
        session_id: str,
        task_id: str,
        completion_id: str,
    ) -> bool:
        now = datetime.now(UTC).isoformat()
        cursor = await self.conn.execute(
            """
            UPDATE background_agent_runs SET notified_at = COALESCE(notified_at, ?), updated_at = ?
            WHERE session_id = ? AND task_id = ? AND completion_id = ?
            """,
            (now, now, session_id, task_id, completion_id),
        )
        await self.conn.execute(
            "UPDATE background_agent_events SET delivered_at = COALESCE(delivered_at, ?) WHERE event_id = ?",
            (now, completion_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def list_undelivered_background_completions(self) -> list[dict]:
        rows = await self.read_conn.execute_fetchall(
            """
            SELECT * FROM background_agent_runs
            WHERE completion_id IS NOT NULL AND notified_at IS NULL
            ORDER BY ended_at ASC
            """
        )
        return [
            {
                **self._background_agent_payload(row),
                "result_text": row["result_text"],
            }
            for row in rows
        ]

    async def request_background_agent_cancel(self, session_id: str, task_id: str) -> bool:
        now = datetime.now(UTC).isoformat()
        cursor = await self.conn.execute(
            """
            UPDATE background_agent_runs
            SET status = 'cancel_requested',
                cancel_requested_at = COALESCE(cancel_requested_at, ?),
                updated_at = ?
            WHERE session_id = ? AND task_id = ?
              AND status NOT IN ('completed', 'failed', 'cancelled', 'interrupted')
            """,
            (now, now, session_id, task_id),
        )
        await self.conn.commit()
        changed = cursor.rowcount > 0
        if changed:
            await self.record_background_agent_event(
                task_id=task_id,
                session_id=session_id,
                status="cancel_requested",
            )
        return changed

    async def get_background_agent_result(self, session_id: str, task_id: str) -> str | None:
        rows = await self.read_conn.execute_fetchall(
            """
            SELECT result_text FROM background_agent_runs
            WHERE session_id = ? AND task_id = ?
            """,
            (session_id, task_id),
        )
        if not rows:
            return None
        value = rows[0]["result_text"]
        return value if isinstance(value, str) else None

    async def list_background_agent_runs(
        self,
        session_id: str,
        *,
        include_terminal: bool = True,
    ) -> list[dict]:
        if include_terminal:
            rows = await self.read_conn.execute_fetchall(
                """
                SELECT * FROM background_agent_runs
                WHERE session_id = ?
                ORDER BY updated_at DESC
                """,
                (session_id,),
            )
        else:
            rows = await self.read_conn.execute_fetchall(
                """
                SELECT * FROM background_agent_runs
                WHERE session_id = ?
                  AND status NOT IN ('completed', 'failed', 'cancelled', 'interrupted')
                ORDER BY updated_at DESC
                """,
                (session_id,),
            )
        return [self._background_agent_payload(row) for row in rows]

    async def list_background_agent_events(
        self,
        session_id: str,
        *,
        after_seq: int = 0,
        limit: int = 10000,
    ) -> list[dict]:
        rows = await self.read_conn.execute_fetchall(
            """
            SELECT * FROM background_agent_events
            WHERE session_id = ? AND seq > ?
            ORDER BY seq ASC
            LIMIT ?
            """,
            (session_id, after_seq, limit),
        )
        return [self._background_agent_event_payload(row) for row in rows]

    async def mark_interrupted_background_agent_runs(self) -> int:
        now = datetime.now(UTC).isoformat()
        rows = await self.conn.execute_fetchall(
            """
            SELECT task_id, session_id FROM background_agent_runs
            WHERE status IN ('running', 'activity', 'cancel_requested')
            """,
        )
        if not rows:
            return 0
        await self.conn.execute(
            """
            UPDATE background_agent_runs
            SET status = 'interrupted',
                detail = COALESCE(detail, 'server_restart'),
                updated_at = ?,
                ended_at = COALESCE(ended_at, ?)
            WHERE status IN ('running', 'activity', 'cancel_requested')
            """,
            (now, now),
        )
        for row in rows:
            await self.record_background_agent_event(
                task_id=row["task_id"],
                session_id=row["session_id"],
                status="interrupted",
                detail="server_restart",
            )
        await self.conn.commit()
        return len(rows)

    async def mark_interrupted_agent_sessions(self) -> int:
        """A subagent session left 'running' after a restart can never resume —
        its run died with the process — so the status is a lie that strands the
        UI on a spinner until a manual reload. Flip orphaned agent sessions to
        'interrupted' so a history load resolves them. Mirrors
        mark_interrupted_background_agent_runs for the foreground/session path."""
        rows = await self.conn.execute_fetchall(
            """
            SELECT session_id FROM sessions
            WHERE session_type = 'agent' AND agent_status = 'running'
            """,
        )
        if not rows:
            return 0
        await self.conn.execute(
            """
            UPDATE sessions SET agent_status = 'interrupted'
            WHERE session_type = 'agent' AND agent_status = 'running'
            """,
        )
        await self.conn.commit()
        return len(rows)

    async def mark_interrupted_chat_queued_messages_retryable(self) -> int:
        now = datetime.now(UTC).isoformat()
        cursor = await self.conn.execute(
            """
            UPDATE chat_queued_messages
            SET status = 'failed_retryable',
                updated_at = ?
            WHERE status = 'queued'
              AND run_id IN (
                  SELECT run_id FROM chat_runs WHERE status = 'interrupted'
              )
            """,
            (now,),
        )
        await self.conn.commit()
        return cursor.rowcount

    async def record_chat_queued_message(
        self,
        *,
        client_id: str,
        session_id: str,
        run_id: str,
        message: dict,
        enqueued_seq: int | None = None,
    ) -> str:
        """Record a queue item without reopening a terminal client id.

        A cancellation can arrive while an earlier enqueue request is waiting
        on storage. Its tombstone must win; otherwise the late write recreates
        an invisible, never-drained queue row.
        """
        terminal_receipt = await self.conn.execute_fetchall(
            """
            SELECT status FROM chat_idempotency_keys
            WHERE session_id = ? AND client_id = ?
              AND status IN ('cancelled', 'ingested')
            """,
            (session_id, client_id),
        )
        if terminal_receipt:
            return str(terminal_receipt[0]["status"])

        now = datetime.now(UTC).isoformat()
        message_json = await asyncio.to_thread(lambda: json.dumps(message, default=str))
        cursor = await self.conn.execute(
            """
            INSERT INTO chat_queued_messages (
                client_id, session_id, run_id, status, message_json, enqueued_at, updated_at, enqueued_seq
            )
            VALUES (?, ?, ?, 'queued', ?, ?, ?, ?)
            ON CONFLICT(client_id) DO UPDATE SET
                session_id = excluded.session_id,
                run_id = excluded.run_id,
                status = excluded.status,
                message_json = excluded.message_json,
                updated_at = excluded.updated_at,
                enqueued_seq = excluded.enqueued_seq,
                ingested_at = NULL,
                ingested_seq = NULL
            WHERE chat_queued_messages.status NOT IN ('cancelled', 'ingested')
            """,
            (client_id, session_id, run_id, message_json, now, now, enqueued_seq),
        )
        await self.conn.commit()
        if cursor.rowcount > 0:
            return "queued"
        rows = await self.conn.execute_fetchall(
            "SELECT status FROM chat_queued_messages WHERE client_id = ?",
            (client_id,),
        )
        return str(rows[0]["status"]) if rows else "cancelled"

    async def mark_chat_queued_message_ingested(self, client_id: str, *, ingested_seq: int | None = None) -> None:
        now_dt = datetime.now(UTC)
        now = now_dt.isoformat()
        expires_at = (now_dt + timedelta(days=CHAT_IDEMPOTENCY_TTL_DAYS)).isoformat()
        await self.conn.execute(
            """
            UPDATE chat_queued_messages
            SET status = 'ingested', updated_at = ?, ingested_at = ?, ingested_seq = COALESCE(?, ingested_seq)
            WHERE client_id = ?
            """,
            (now, now, ingested_seq, client_id),
        )
        # The client id is a durable request receipt too. Leaving it at
        # ``queued`` makes a retry render a ghost queue item after the agent
        # has already consumed it.
        await self.conn.execute(
            """
            UPDATE chat_idempotency_keys
            SET status = 'ingested', updated_at = ?, expires_at = ?
            WHERE client_id = ?
              AND session_id = (
                  SELECT session_id FROM chat_queued_messages WHERE client_id = ?
              )
              AND status != 'cancelled'
            """,
            (now, expires_at, client_id, client_id),
        )
        await self.conn.commit()

    async def mark_chat_queued_message_cancelled(self, client_id: str) -> None:
        now = datetime.now(UTC).isoformat()
        await self.conn.execute(
            """
            UPDATE chat_queued_messages
            SET status = 'cancelled', updated_at = ?
            WHERE client_id = ? AND status = 'queued'
            """,
            (now, client_id),
        )
        await self.conn.commit()

    async def list_chat_queued_messages(self, session_id: str, *, status: str | None = None) -> list[dict]:
        if status:
            rows = await self.read_conn.execute_fetchall(
                """
                SELECT * FROM chat_queued_messages
                WHERE session_id = ? AND status = ?
                ORDER BY enqueued_at ASC
                """,
                (session_id, status),
            )
        else:
            rows = await self.read_conn.execute_fetchall(
                """
                SELECT * FROM chat_queued_messages
                WHERE session_id = ?
                ORDER BY enqueued_at ASC
                """,
                (session_id,),
            )
        return [self._chat_queued_message_payload(row) for row in rows]

    @staticmethod
    def _tool_result_id(*, session_id: str, seq: int, tool_call_id: str, content_sha256: str) -> str:
        seed = f"{session_id}\0{seq}\0{tool_call_id}\0{content_sha256}".encode()
        return f"tr_{hashlib.sha256(seed).hexdigest()[:24]}"

    @staticmethod
    def _raw_tool_result_pointer_content(
        *,
        preview: str,
        tool_result_id: str,
        content_bytes: int,
    ) -> str:
        prefix = preview_text(preview)
        note = f"[Full raw tool result stored as {tool_result_id}; {content_bytes} bytes.]"
        return f"{prefix}\n\n{note}" if prefix else note

    @staticmethod
    def _strip_empty_raw_tool_result_fields(payload: dict) -> dict:
        for key in ("raw_ref", "content_sha256", "content_bytes", "retention_class"):
            if payload.get(key) is None:
                payload.pop(key, None)
        return payload

    @staticmethod
    def _allowlisted_durable_tool_result_data(data: dict | None) -> dict | None:
        if not isinstance(data, dict):
            return None
        allowed = {key: data[key] for key in _DURABLE_TOOL_RESULT_DATA_KEYS if key in data}
        return allowed or None

    @classmethod
    def _prepare_durable_tool_result_payload(
        cls,
        *,
        record: StreamRecord,
        payload: dict,
        now: str,
    ) -> tuple[dict, tuple | None, tuple | None]:
        if payload.get("type") != "TOOL_CALL_RESULT":
            return payload, None, None

        payload = dict(payload)
        content = payload.get("content")
        blob: RawToolResultBlob | None = internal_blob_from_data(payload.get("data"))
        cleaned_data = strip_internal_raw_tool_result_data(payload.get("data"))
        cleaned_data = cls._allowlisted_durable_tool_result_data(cleaned_data)
        if cleaned_data is None:
            payload.pop("data", None)
        else:
            payload["data"] = cleaned_data

        tool_call_id = payload.get("tool_call_id")
        if not isinstance(tool_call_id, str) or not tool_call_id:
            return cls._strip_empty_raw_tool_result_fields(payload), None, None

        if blob is None:
            if not isinstance(content, str) or len(content.encode("utf-8")) <= RAW_TOOL_RESULT_INLINE_MAX_BYTES:
                return cls._strip_empty_raw_tool_result_fields(payload), None, None
            blob = persist_raw_tool_result(content)
            preview = payload.get("preview") if isinstance(payload.get("preview"), str) else ""
            preview = preview or preview_text(content)
            payload["preview"] = preview_text(preview)
            payload["content"] = cls._raw_tool_result_pointer_content(
                preview=preview,
                tool_result_id=cls._tool_result_id(
                    session_id=record.session_id,
                    seq=record.seq,
                    tool_call_id=tool_call_id,
                    content_sha256=blob.content_sha256,
                ),
                content_bytes=blob.content_bytes,
            )

        tool_result_id = cls._tool_result_id(
            session_id=record.session_id,
            seq=record.seq,
            tool_call_id=tool_call_id,
            content_sha256=blob.content_sha256,
        )
        run_id = payload.get("run_id") if isinstance(payload.get("run_id"), str) else None
        preview = payload.get("preview") if isinstance(payload.get("preview"), str) else ""
        retention_class = "session"
        payload["raw_ref"] = tool_result_id
        payload.pop("content_sha256", None)
        payload["content_bytes"] = blob.content_bytes
        payload["retention_class"] = retention_class

        tool_result_row = (
            tool_result_id,
            record.session_id,
            run_id,
            tool_call_id,
            payload.get("name") if isinstance(payload.get("name"), str) else None,
            blob.content_sha256,
            blob.content_bytes,
            blob.stored_bytes,
            blob.compression,
            blob.blob_ref,
            blob.blob_path,
            preview_text(preview),
            retention_class,
            None,
            record.seq,
            now,
        )
        tool_call_ref_update = (tool_result_id, record.session_id, tool_call_id)
        return payload, tool_result_row, tool_call_ref_update

    async def record_session_event(self, record: StreamRecord) -> None:
        await self.record_session_events([record])

    async def record_session_events(self, records: list[StreamRecord]) -> None:
        """Persist a batch of durable events in ONE transaction (one commit).

        The SSE writer drains its queue and hands a batch here so a high-volume
        run does not pay a commit (and its WAL fsync) per event on the single
        shared write connection — which otherwise serializes ahead of, and
        starves, request-path writes like POST /chat/message. Serialization +
        JSON encoding run in a worker thread to keep the event loop free."""
        if not records:
            return

        def _build_rows() -> tuple[list[tuple], list[tuple], list[tuple], dict[str, int]]:
            now = datetime.now(UTC).isoformat()
            rows: list[tuple] = []
            tool_result_rows: list[tuple] = []
            tool_call_ref_updates: list[tuple] = []
            per_session: dict[str, int] = {}
            for record in records:
                sse = record.event.to_sse()
                payload = json.loads(sse["data"])
                payload, tool_result_row, tool_call_ref_update = self._prepare_durable_tool_result_payload(
                    record=record,
                    payload=payload,
                    now=now,
                )
                if tool_result_row is not None:
                    tool_result_rows.append(tool_result_row)
                if tool_call_ref_update is not None:
                    tool_call_ref_updates.append(tool_call_ref_update)
                run_id = payload.get("run_id") if isinstance(payload.get("run_id"), str) else None
                rows.append(
                    (
                        record.session_id,
                        record.seq,
                        str(payload.get("type") or sse["event"]),
                        json.dumps(payload, default=str),
                        run_id,
                        now,
                    )
                )
                per_session[record.session_id] = per_session.get(record.session_id, 0) + 1
            return rows, tool_result_rows, tool_call_ref_updates, per_session

        rows, tool_result_rows, tool_call_ref_updates, per_session = await asyncio.to_thread(_build_rows)
        if tool_result_rows:
            await self.conn.executemany(
                """
                INSERT OR IGNORE INTO tool_results (
                    tool_result_id, session_id, run_id, tool_call_id, tool_name,
                    content_sha256, content_bytes, stored_bytes, compression,
                    blob_ref, blob_path, preview, retention_class, expires_at,
                    source_event_seq, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                tool_result_rows,
            )
        if tool_call_ref_updates:
            await self.conn.executemany(
                """
                UPDATE tool_calls
                SET result_ref = ?
                WHERE session_id = ? AND tool_call_id = ?
                """,
                tool_call_ref_updates,
            )
        await self.conn.executemany(
            """
            INSERT INTO session_events (
                session_id, seq, event_type, event_json, run_id, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        await self.conn.commit()

        # Amortized retention: cap each session's durable event log to the
        # newest N rows. Trimming the oldest never touches an active run's
        # tail (its events are the newest), so no checkpoint guard is needed.
        for session_id, n in per_session.items():
            count = self._events_since_prune.get(session_id, 0) + n
            if count >= SESSION_EVENT_PRUNE_INTERVAL:
                self._events_since_prune[session_id] = 0
                await self.prune_session_events(session_id, SESSION_EVENT_DURABLE_RETENTION)
            else:
                self._events_since_prune[session_id] = count

    async def prune_session_events(self, session_id: str, keep: int = SESSION_EVENT_DURABLE_RETENTION) -> int:
        """Keep only the newest `keep` durable events for a session, deleting
        older ones (SQLite equivalent of Redis XADD MAXLEN~). The subquery
        yields the (keep+1)-th newest seq; if the session has <= keep events
        it returns no row and nothing is deleted."""
        cursor = await self.conn.execute(
            """
            DELETE FROM session_events
            WHERE session_id = ?
              AND seq <= (
                SELECT seq FROM session_events
                WHERE session_id = ?
                ORDER BY seq DESC
                LIMIT 1 OFFSET ?
              )
            """,
            (session_id, session_id, keep),
        )
        await self.conn.commit()
        return cursor.rowcount

    async def list_session_events(
        self,
        session_id: str,
        *,
        after_seq: int = 0,
        limit: int = 10000,
    ) -> list[StreamRecord]:
        rows = await self.read_conn.execute_fetchall(
            """
            SELECT seq, event_json
            FROM session_events
            WHERE session_id = ? AND seq > ?
            ORDER BY seq ASC
            LIMIT ?
            """,
            (session_id, after_seq, limit),
        )
        records: list[StreamRecord] = []
        for row in rows:
            payload = json.loads(row["event_json"])
            records.append(
                StreamRecord(
                    seq=row["seq"],
                    session_id=session_id,
                    event=event_from_payload(payload),
                )
            )
        return records

    async def get_latest_session_event_seq(self, session_id: str) -> int:
        rows = await self.read_conn.execute_fetchall(
            "SELECT COALESCE(MAX(seq), 0) AS latest_seq FROM session_events WHERE session_id = ?",
            (session_id,),
        )
        return int(rows[0]["latest_seq"] or 0)

    async def get_latest_session_checkpoint_seq(self, session_id: str) -> int:
        rows = await self.read_conn.execute_fetchall(
            "SELECT COALESCE(MAX(last_seq), 0) AS latest_seq FROM chat_runs WHERE session_id = ? AND last_seq IS NOT NULL",
            (session_id,),
        )
        return int(rows[0]["latest_seq"] or 0)

    async def record_chat_compaction(
        self,
        *,
        compaction_id: str,
        session_id: str,
        boundary_seq: int,
        messages_before: int,
        messages_after: int,
        rehydration_state: dict | None = None,
    ) -> None:
        rehydration_state_json = await asyncio.to_thread(
            lambda: json.dumps(rehydration_state, default=str) if rehydration_state is not None else None
        )
        await self.conn.execute(
            """
            INSERT INTO chat_compactions (
                compaction_id, session_id, boundary_seq, messages_before, messages_after,
                rehydration_state, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(compaction_id) DO UPDATE SET
                session_id = excluded.session_id,
                boundary_seq = excluded.boundary_seq,
                messages_before = excluded.messages_before,
                messages_after = excluded.messages_after,
                rehydration_state = excluded.rehydration_state
            """,
            (
                compaction_id,
                session_id,
                boundary_seq,
                messages_before,
                messages_after,
                rehydration_state_json,
                datetime.now(UTC).isoformat(),
            ),
        )
        await self.conn.commit()

    async def list_chat_compactions(self, session_id: str) -> list[dict]:
        rows = await self.read_conn.execute_fetchall(
            """
            SELECT *
            FROM chat_compactions
            WHERE session_id = ?
            ORDER BY boundary_seq ASC
            """,
            (session_id,),
        )
        return [
            {
                "compaction_id": row["compaction_id"],
                "session_id": row["session_id"],
                "boundary_seq": row["boundary_seq"],
                "messages_before": row["messages_before"],
                "messages_after": row["messages_after"],
                "rehydration_state": json.loads(row["rehydration_state"]) if row["rehydration_state"] else None,
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    async def save_session(self, state: SessionState, messages: list[dict | Any], metadata: dict | None = None) -> None:
        lock = await self._session_write_lock(state.session_id)
        async with lock:
            serializable_messages = self._to_serializable_messages(messages)

            # Stamp created_at on every message that hasn't been stamped yet.
            # The list is shared with the agent's in-memory context so the
            # stamp persists across saves without a side-table lookup.
            now = datetime.now(UTC).isoformat()
            self._stamp_messages(serializable_messages, now)

            meta = metadata or {}
            messages_json, metadata_json = await asyncio.to_thread(
                lambda: (json.dumps(serializable_messages, default=str), json.dumps(meta))
            )
            await self.conn.execute(
                SQL_SAVE_SESSION,
                (
                    state.session_id,
                    state.started_at.isoformat(),
                    state.last_activity.isoformat(),
                    messages_json,
                    metadata_json,
                    state.name,
                    state.session_type,
                    state.origin_automation_id,
                    state.parent_session_id,
                    state.parent_tool_call_id,
                    state.agent_type,
                    state.agent_status,
                    state.area_id,
                    state.chat_model,
                ),
            )
            await self._mirror_session_messages(state.session_id, serializable_messages)
            await self.conn.commit()

    async def create_session_if_absent(
        self,
        state: SessionState,
        messages: list[dict | Any] | None = None,
        metadata: dict | None = None,
    ) -> bool:
        """Insert a new session without replacing a row created by a racing owner."""
        lock = await self._session_write_lock(state.session_id)
        async with lock:
            serializable_messages = self._to_serializable_messages(messages or [])
            now = datetime.now(UTC).isoformat()
            self._stamp_messages(serializable_messages, now)
            meta = metadata or {}
            messages_json, metadata_json = await asyncio.to_thread(
                lambda: (json.dumps(serializable_messages, default=str), json.dumps(meta))
            )
            cursor = await self.conn.execute(
                SQL_INSERT_SESSION_IF_ABSENT,
                (
                    state.session_id,
                    state.started_at.isoformat(),
                    state.last_activity.isoformat(),
                    messages_json,
                    metadata_json,
                    state.name,
                    state.session_type,
                    state.origin_automation_id,
                    state.parent_session_id,
                    state.parent_tool_call_id,
                    state.agent_type,
                    state.agent_status,
                    state.area_id,
                    state.chat_model,
                ),
            )
            created = cursor.rowcount > 0
            if created:
                await self._mirror_session_messages(state.session_id, serializable_messages)
            await self.conn.commit()
            return created

    async def load_session(self, session_id: str) -> SessionData | None:
        rows = await self.read_conn.execute_fetchall(SQL_LOAD_SESSION, (session_id,))
        if not rows:
            return None

        row = rows[0]
        started_at = datetime.fromisoformat(row["started_at"])
        last_activity = datetime.fromisoformat(row["last_activity"])
        # Attach UTC to naive datetimes from old sessions
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=UTC)
        if last_activity.tzinfo is None:
            last_activity = last_activity.replace(tzinfo=UTC)

        name = row["name"]

        state = SessionState(
            session_id=row["session_id"],
            started_at=started_at,
            last_activity=last_activity,
            name=name,
            session_type=row["session_type"] or "chat",
            origin_automation_id=row["origin_automation_id"],
            parent_session_id=dict(row).get("parent_session_id"),
            parent_tool_call_id=dict(row).get("parent_tool_call_id"),
            agent_type=dict(row).get("agent_type"),
            agent_status=dict(row).get("agent_status"),
            area_id=row["area_id"],
            chat_model=dict(row).get("chat_model"),
        )

        raw_messages, raw_metadata = row["messages"], row["metadata"]
        messages, metadata = await asyncio.to_thread(
            lambda: (json.loads(raw_messages) if raw_messages else [], json.loads(raw_metadata) if raw_metadata else {})
        )
        return SessionData(
            state=state,
            messages=messages,
            last_input_tokens=metadata.get("last_input_tokens"),
            last_message_count=metadata.get("last_message_count"),
        )

    async def get_latest_id(self) -> str | None:
        rows = await self.read_conn.execute_fetchall(SQL_GET_LATEST)
        return rows[0]["session_id"] if rows else None

    async def list_sessions(
        self,
        limit: int = 20,
        area_id: str | object | None = AREA_FILTER_UNSET,
        include_agents: bool = True,
        offset: int = 0,
        newest_first: bool = True,
    ) -> list[dict]:
        direction = "DESC" if newest_first else "ASC"
        if area_id is AREA_FILTER_UNSET:
            sql = SQL_LIST_SESSIONS if include_agents else SQL_LIST_PRIMARY_SESSIONS
            rows = await self.read_conn.execute_fetchall(sql.format(direction=direction), (limit, offset))
        elif area_id is None:
            rows = await self.read_conn.execute_fetchall(
                f"""
                SELECT session_id, started_at, last_activity, name,
                       session_type, origin_automation_id, parent_session_id, parent_tool_call_id,
                       agent_type, agent_status, area_id, chat_model,
                       json_array_length(COALESCE(messages, '[]')) AS message_count
                FROM sessions
                WHERE archived_at IS NULL
                  AND area_id IS NULL
                  AND (? OR COALESCE(session_type, 'chat') != 'agent')
                ORDER BY last_activity {direction}
                LIMIT ? OFFSET ?
                """,
                (include_agents, limit, offset),
            )
        else:
            rows = await self.read_conn.execute_fetchall(
                f"""
                SELECT session_id, started_at, last_activity, name,
                       session_type, origin_automation_id, parent_session_id, parent_tool_call_id,
                       agent_type, agent_status, area_id, chat_model,
                       json_array_length(COALESCE(messages, '[]')) AS message_count
                FROM sessions
                WHERE archived_at IS NULL
                  AND area_id = ?
                  AND (? OR COALESCE(session_type, 'chat') != 'agent')
                ORDER BY last_activity {direction}
                LIMIT ? OFFSET ?
                """,
                (area_id, include_agents, limit, offset),
            )
        return [
            {
                "session_id": row["session_id"],
                "started_at": row["started_at"],
                "last_activity": row["last_activity"],
                "name": row["name"],
                "message_count": row["message_count"],
                "session_type": row["session_type"] or "chat",
                "origin_automation_id": row["origin_automation_id"],
                "parent_session_id": row["parent_session_id"],
                "parent_tool_call_id": row["parent_tool_call_id"],
                "agent_type": row["agent_type"],
                "agent_status": row["agent_status"],
                "area_id": row["area_id"],
                "chat_model": dict(row).get("chat_model"),
            }
            for row in rows
        ]

    async def update_session_name(self, session_id: str, name: str) -> bool:
        return await self._update(SQL_UPDATE_NAME, (name, session_id))

    async def update_session_area(self, session_id: str, area_id: str | None) -> bool:
        return await self._update(SQL_UPDATE_SESSION_AREA, (area_id, session_id))

    async def update_session_chat_model(self, session_id: str, chat_model: str | None) -> bool:
        return await self._update(SQL_UPDATE_SESSION_CHAT_MODEL, (chat_model, session_id))

    async def update_session_name_if_empty(self, session_id: str, name: str) -> bool:
        return await self._update(SQL_UPDATE_NAME_IF_EMPTY, (name, session_id))

    async def is_session_archived(self, session_id: str) -> bool:
        """Archived state is deliberately absent from `SessionState`: nothing on
        the run path may act on it. Callers that curate the sidebar ask here."""
        rows = await self.read_conn.execute_fetchall(SQL_SELECT_ARCHIVED_AT, (session_id,))
        return bool(rows) and rows[0]["archived_at"] is not None

    async def archive_session(self, session_id: str) -> bool:
        return await self._update(SQL_ARCHIVE, (datetime.now(UTC).isoformat(), session_id))

    async def restore_session(self, session_id: str) -> bool:
        return await self._update(SQL_RESTORE, (session_id,))

    async def list_archived_sessions(self, limit: int = 20) -> list[dict]:
        rows = await self.read_conn.execute_fetchall(SQL_LIST_ARCHIVED, (limit,))
        return [
            {
                "session_id": row["session_id"],
                "started_at": row["started_at"],
                "last_activity": row["last_activity"],
                "name": row["name"],
                "message_count": row["message_count"],
                "archived_at": row["archived_at"],
                "session_type": row["session_type"] or "chat",
                "origin_automation_id": row["origin_automation_id"],
                "parent_session_id": row["parent_session_id"],
                "parent_tool_call_id": row["parent_tool_call_id"],
                "agent_type": row["agent_type"],
                "agent_status": row["agent_status"],
                "area_id": row["area_id"],
                "chat_model": dict(row).get("chat_model"),
            }
            for row in rows
        ]

    async def permanently_delete_session(self, session_id: str) -> bool:
        return await self._update(SQL_DELETE_ARCHIVED, (session_id,))

    async def list_session_messages(
        self,
        session_id: str,
        limit: int = 100,
        before: str | None = None,
        after: str | None = None,
        around: str | None = None,
        around_seq: int | None = None,
        before_seq: int | None = None,
        after_seq: int | None = None,
        area_id: str | object | None = AREA_FILTER_UNSET,
    ) -> dict:
        if area_id is not AREA_FILTER_UNSET and not await self._session_matches_area(session_id, area_id):
            return {
                "messages": [],
                "has_more_before": False,
                "has_more_after": False,
                "before": None,
                "after": None,
            }
        await self._ensure_session_messages(session_id)
        limit = max(1, min(limit, 250))

        async def seq_for_message(ref: str | None) -> int | None:
            if not ref:
                return None
            rows = await self.read_conn.execute_fetchall(
                """
                SELECT seq FROM session_messages
                WHERE session_id = ? AND (message_id = ? OR client_id = ?)
                LIMIT 1
                """,
                (session_id, ref, ref),
            )
            return int(rows[0]["seq"]) if rows else None

        rows: list[Any]
        around_at = await seq_for_message(around)
        # Raw-int seq cursors (from search hits / prior pages) take precedence
        # over message-id refs when both are somehow supplied.
        before_at = before_seq if before_seq is not None else await seq_for_message(before)
        after_at = after_seq if after_seq is not None else await seq_for_message(after)
        if around_seq is not None:
            start = max(0, around_seq - (limit // 2))
            rows = await self.read_conn.execute_fetchall(
                """
                SELECT * FROM session_messages
                WHERE session_id = ? AND seq >= ?
                ORDER BY seq ASC
                LIMIT ?
                """,
                (session_id, start, limit),
            )
        elif around_at is not None:
            start = max(0, around_at - (limit // 2))
            rows = await self.read_conn.execute_fetchall(
                """
                SELECT * FROM session_messages
                WHERE session_id = ? AND seq >= ?
                ORDER BY seq ASC
                LIMIT ?
                """,
                (session_id, start, limit),
            )
        elif before_at is not None:
            desc_rows = await self.read_conn.execute_fetchall(
                """
                SELECT * FROM session_messages
                WHERE session_id = ? AND seq < ?
                ORDER BY seq DESC
                LIMIT ?
                """,
                (session_id, before_at, limit),
            )
            rows = list(reversed(desc_rows))
        elif after_at is not None:
            rows = await self.read_conn.execute_fetchall(
                """
                SELECT * FROM session_messages
                WHERE session_id = ? AND seq > ?
                ORDER BY seq ASC
                LIMIT ?
                """,
                (session_id, after_at, limit),
            )
        else:
            desc_rows = await self.read_conn.execute_fetchall(
                """
                SELECT * FROM session_messages
                WHERE session_id = ?
                ORDER BY seq DESC
                LIMIT ?
                """,
                (session_id, limit),
            )
            rows = list(reversed(desc_rows))
            rows = await self._latest_rows_with_visible_user_anchor(session_id, rows)

        messages = [self._message_row_payload(row) for row in rows]
        first_seq = messages[0]["seq"] if messages else None
        last_seq = messages[-1]["seq"] if messages else None
        has_more_before = False
        has_more_after = False
        if first_seq is not None:
            has_more_before = bool(
                await self.read_conn.execute_fetchall(
                    "SELECT 1 FROM session_messages WHERE session_id = ? AND seq < ? LIMIT 1",
                    (session_id, first_seq),
                )
            )
        if last_seq is not None:
            has_more_after = bool(
                await self.read_conn.execute_fetchall(
                    "SELECT 1 FROM session_messages WHERE session_id = ? AND seq > ? LIMIT 1",
                    (session_id, last_seq),
                )
            )

        return {
            "messages": messages,
            "has_more_before": has_more_before,
            "has_more_after": has_more_after,
            "before": messages[0]["message_id"] if messages else None,
            "after": messages[-1]["message_id"] if messages else None,
        }

    async def messages_since(self, session_id: str, seq: int) -> list[dict]:
        """Ordered transcript rows with seq > `seq` (oldest-first) for the
        curator. Returns the same `_message_row_payload` shape as list_messages
        (carries `seq`, `role`, parsed `message`)."""
        rows = await self.read_conn.execute_fetchall(
            """
            SELECT * FROM session_messages
            WHERE session_id = ? AND seq > ?
            ORDER BY seq ASC
            """,
            (session_id, seq),
        )
        return [self._message_row_payload(row) for row in rows]

    async def recent_session_scopes(self, limit: int) -> list[dict]:
        """The `limit` most-recently-active live sessions (archived excluded),
        as {session_id, area_id, session_type, origin_automation_id} — the
        curation sweep's worklist (it gates on the origin fields)."""
        rows = await self.read_conn.execute_fetchall(
            """
            SELECT session_id, area_id, session_type, origin_automation_id FROM sessions
            WHERE archived_at IS NULL
            ORDER BY last_activity DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [
            {
                "session_id": row["session_id"],
                "area_id": row["area_id"],
                "session_type": row["session_type"] or "chat",
                "origin_automation_id": row["origin_automation_id"],
            }
            for row in rows
        ]

    async def session_scope(self, session_id: str) -> dict | None:
        """Return one session's scope exactly, independent of sweep recency."""
        rows = await self.read_conn.execute_fetchall(
            """
            SELECT session_id, area_id, session_type, origin_automation_id
            FROM sessions
            WHERE session_id = ?
            """,
            (session_id,),
        )
        if not rows:
            return None
        row = rows[0]
        return {
            "session_id": row["session_id"],
            "area_id": row["area_id"],
            "session_type": row["session_type"] or "chat",
            "origin_automation_id": row["origin_automation_id"],
        }

    async def search_messages(
        self,
        query: str,
        *,
        limit: int = 20,
        offset: int = 0,
        session_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
        area_id: str | object | None = AREA_FILTER_UNSET,
    ) -> dict:
        """Full-text search across transcript messages using SQLite FTS5.

        Returns {hits, has_more}. Each hit carries session_id + session name,
        seq, role, created_at, and a trimmed snippet. Scope to one chat with
        `session_id`; bound by time with ISO `since`/`until`. Empty/whitespace
        query → no hits (rather than an FTS syntax error)."""
        q = query.strip()
        if not q:
            return {"hits": [], "has_more": False}
        # Bound the FTS5 parser input so an oversized/pathological query can't peg
        # a core (it spins without raising, so the except-fallback below won't catch it).
        q = q[:MAX_FTS_QUERY_CHARS]
        limit = max(1, min(limit, 100))
        offset = max(0, offset)

        where = ["session_messages_fts MATCH ?"]
        params: list[Any] = [q]
        if session_id is not None:
            where.append("m.session_id = ?")
            params.append(session_id)
        if since is not None:
            where.append("m.created_at >= ?")
            params.append(since)
        if until is not None:
            where.append("m.created_at <= ?")
            params.append(until)
        if area_id is not AREA_FILTER_UNSET:
            if area_id is None:
                where.append("s.area_id IS NULL")
            else:
                where.append("s.area_id = ?")
                params.append(area_id)

        # One extra row signals a further page.
        fts_sql_limit, fts_sql_offset = limit + 1, offset
        sql = f"""
            SELECT m.session_id AS session_id, s.name AS session_name,
                   m.seq AS seq, m.role AS role, m.created_at AS created_at,
                   snippet(session_messages_fts, 0, '[', ']', '…', 16) AS snippet
            FROM session_messages_fts
            JOIN session_messages m ON m.rowid = session_messages_fts.rowid
            LEFT JOIN sessions s ON s.session_id = m.session_id
            WHERE {" AND ".join(where)}
            ORDER BY bm25(session_messages_fts), m.created_at DESC
            LIMIT ? OFFSET ?
        """
        params.extend([fts_sql_limit, fts_sql_offset])

        try:
            fts_rows = await self.read_conn.execute_fetchall(sql, tuple(params))
        except Exception:
            # Malformed FTS query (stray operators, unbalanced quotes). Retry
            # as a quoted phrase so user text never surfaces a SQL error.
            phrase = '"' + q.replace('"', '""') + '"'
            params[0] = phrase
            fts_rows = await self.read_conn.execute_fetchall(sql, tuple(params))

        has_more = len(fts_rows) > limit
        hits = [self._search_hit(r) for r in fts_rows[:limit]]
        return {"hits": hits, "has_more": has_more}

    @staticmethod
    def _search_hit(r: Any, snippet: str | None = None) -> dict:
        return {
            "session_id": r["session_id"],
            "session_name": r["session_name"],
            "seq": r["seq"],
            "role": r["role"],
            "created_at": r["created_at"],
            "snippet": (snippet if snippet is not None else (r["snippet"] or "")).strip(),
        }

    async def _session_matches_area(self, session_id: str, area_id: str | object | None) -> bool:
        rows = await self.read_conn.execute_fetchall(
            "SELECT area_id FROM sessions WHERE session_id = ? LIMIT 1",
            (session_id,),
        )
        return bool(rows) and rows[0]["area_id"] == area_id

    def _row_is_visible_user(self, row: Any) -> bool:
        if row["role"] != "user":
            return False
        try:
            message = json.loads(row["message_json"])
        except Exception:
            return True
        return not bool(message.get("is_meta"))

    async def _latest_rows_with_visible_user_anchor(self, session_id: str, rows: list[Any]) -> list[Any]:
        if not rows:
            return rows

        visible_users = [row for row in rows if self._row_is_visible_user(row)]
        if len(visible_users) >= 2:
            return rows

        if visible_users:
            anchor = visible_users[0]
            previous_anchor = await self._visible_user_before(session_id, anchor["seq"])
            if previous_anchor:
                expanded = await self._bounded_rows_between(
                    session_id,
                    previous_anchor["seq"],
                    rows[-1]["seq"],
                    max_count=LATEST_VISIBLE_ANCHOR_ROW_LIMIT,
                )
                if expanded is not None:
                    return expanded
            return rows

        anchor = await self._visible_user_before(session_id, rows[0]["seq"])
        if not anchor:
            # No visible-user anchor before the window. Automation / channel
            # sessions drive their turns with meta user messages
            # (loop:/bg:/goal:), so a tool-heavy active run leaves the newest
            # window with zero visible anchors. Fall back to the most recent
            # user turn boundary regardless of meta, so prior turns still load
            # instead of dead-ending on the active run's tool stream.
            return await self._expand_from_user_boundary(session_id, rows)

        previous_anchor = await self._visible_user_before(session_id, anchor["seq"])
        if previous_anchor:
            expanded = await self._bounded_rows_between(
                session_id,
                previous_anchor["seq"],
                rows[-1]["seq"],
                max_count=LATEST_VISIBLE_ANCHOR_ROW_LIMIT,
            )
            if expanded is not None:
                return expanded

        expanded = await self._bounded_rows_between(
            session_id,
            anchor["seq"],
            rows[-1]["seq"],
            max_count=LATEST_VISIBLE_ANCHOR_ROW_LIMIT,
        )
        if expanded is not None:
            return expanded
        return rows

    async def _visible_user_before(self, session_id: str, before_seq: int) -> Any | None:
        rows = await self.read_conn.execute_fetchall(
            """
            SELECT * FROM session_messages
            WHERE session_id = ? AND seq < ? AND role = 'user'
            ORDER BY seq DESC
            LIMIT 50
            """,
            (session_id, before_seq),
        )
        for row in rows:
            if self._row_is_visible_user(row):
                return row
        return None

    async def _user_before(self, session_id: str, before_seq: int) -> Any | None:
        """Most recent user row before `before_seq`, meta or not — a turn
        boundary for sessions that have no visible (non-meta) user."""
        rows = await self.read_conn.execute_fetchall(
            """
            SELECT * FROM session_messages
            WHERE session_id = ? AND seq < ? AND role = 'user'
            ORDER BY seq DESC
            LIMIT 1
            """,
            (session_id, before_seq),
        )
        return rows[0] if rows else None

    async def _expand_from_user_boundary(self, session_id: str, rows: list[Any]) -> list[Any]:
        boundary = await self._user_before(session_id, rows[0]["seq"])
        if boundary is None:
            return rows
        # Reach back one further turn boundary so the previous exchange shows,
        # not just the active run's own opening line.
        previous = await self._user_before(session_id, boundary["seq"])
        start_seq = (previous or boundary)["seq"]
        expanded = await self._bounded_rows_between(
            session_id,
            start_seq,
            rows[-1]["seq"],
            max_count=LATEST_VISIBLE_ANCHOR_ROW_LIMIT,
        )
        return expanded if expanded is not None else rows

    async def _bounded_rows_between(
        self,
        session_id: str,
        start_seq: int,
        end_seq: int,
        *,
        max_count: int = 250,
    ) -> list[Any] | None:
        count_rows = await self.read_conn.execute_fetchall(
            """
            SELECT COUNT(*) AS count FROM session_messages
            WHERE session_id = ? AND seq >= ? AND seq <= ?
            """,
            (session_id, start_seq, end_seq),
        )
        if not count_rows or int(count_rows[0]["count"]) > max_count:
            return None
        return await self.read_conn.execute_fetchall(
            """
            SELECT * FROM session_messages
            WHERE session_id = ? AND seq >= ? AND seq <= ?
            ORDER BY seq ASC
            """,
            (session_id, start_seq, end_seq),
        )

    @staticmethod
    def _row_message_json(row: Any) -> dict:
        try:
            payload = json.loads(row["message_json"])
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    async def delete_session_messages_from(
        self,
        session_id: str,
        message_id: str | None = None,
        seq: int | None = None,
    ) -> bool:
        lock = await self._session_write_lock(session_id)
        async with lock:
            await self._ensure_session_messages_unlocked(session_id)
            target_seq = seq
            if target_seq is None and message_id:
                rows = await self.read_conn.execute_fetchall(
                    """
                    SELECT seq FROM session_messages
                    WHERE session_id = ? AND (message_id = ? OR client_id = ?)
                    LIMIT 1
                    """,
                    (session_id, message_id, message_id),
                )
                target_seq = int(rows[0]["seq"]) if rows else None
            if target_seq is None:
                return False

            cursor = await self.conn.execute(
                "DELETE FROM session_messages WHERE session_id = ? AND seq >= ?",
                (session_id, target_seq),
            )
            await self._rebuild_session_turns(session_id)
            await self.conn.commit()
            return cursor.rowcount > 0

    async def list_session_turns(self, session_id: str, limit: int = 100) -> list[dict]:
        await self._ensure_session_messages(session_id)
        rows = await self.read_conn.execute_fetchall(
            """
            SELECT *
            FROM session_turns
            WHERE session_id = ?
            ORDER BY turn_index ASC
            LIMIT ?
            """,
            (session_id, max(1, min(limit, 500))),
        )
        return [
            {
                "session_id": row["session_id"],
                "turn_id": row["turn_id"],
                "turn_index": row["turn_index"],
                "user_message_id": row["user_message_id"],
                "message_start_id": row["message_start_id"],
                "message_end_id": row["message_end_id"],
                "message_start_seq": row["message_start_seq"],
                "message_end_seq": row["message_end_seq"],
                "started_at": row["started_at"],
                "ended_at": row["ended_at"],
            }
            for row in rows
        ]
