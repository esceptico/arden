import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiosqlite

from arden.constants import SESSION_HANDOFF_MARKER
from arden.context.areas_store import AREA_PATCH_UNSET, AreaStore
from arden.context.background_cancellation_store import BackgroundCancellationStore
from arden.context.background_start_store import BackgroundStartStore
from arden.context.background_store import BackgroundAgentStore
from arden.context.chat_idempotency_store import ChatIdempotencyStore
from arden.context.chat_run_store import ChatRunStore
from arden.context.errors import SessionDataCorruptionError
from arden.context.goals_store import GoalStore
from arden.context.models import BackgroundStartDisposition, SessionData, SessionHistoryHeader, SessionState
from arden.context.schema import initialize_context_schema
from arden.context.session_event_store import SessionEventStore
from arden.context.storage_store import SessionStorageStore
from arden.context.store_rows import (
    tool_approval_payload,
    tool_call_payload,
)
from arden.context.transcript_store import AREA_FILTER_UNSET, TranscriptStore
from arden.core.public_refs import is_public_ref, public_ref
from arden.events.internal import RunCompleted, RunFailed
from arden.logging import get_logger
from arden.storage_budget import StorageSessionCandidate
from arden.trajectory.cold_storage import ColdBundleManifest

_logger = get_logger(__name__)

_MESSAGE_ROLES = frozenset({"system", "user", "assistant", "tool"})


class StaleSessionContextError(RuntimeError):
    """A run tried to save through a context generation that was replaced."""


def _reject_nonfinite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value}")


def _encode_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


SQL_SAVE_SESSION = """
INSERT INTO sessions (
    session_id, public_ref, started_at, last_activity, messages, metadata, name,
    session_type, origin_automation_id, parent_session_id, parent_tool_call_id,
    agent_type, agent_status, area_id, chat_model, context_generation, active_message_count
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(session_id) DO UPDATE SET
    last_activity = excluded.last_activity,
    messages = excluded.messages,
    active_message_count = excluded.active_message_count,
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
WHERE sessions.context_generation = excluded.context_generation
"""

SQL_INSERT_SESSION_IF_ABSENT = """
INSERT INTO sessions (
    session_id, public_ref, started_at, last_activity, messages, metadata, name,
    session_type, origin_automation_id, parent_session_id, parent_tool_call_id,
    agent_type, agent_status, area_id, chat_model, context_generation, active_message_count
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(session_id) DO NOTHING
"""

SQL_GET_LATEST = """
SELECT session_id FROM sessions
WHERE archived_at IS NULL
ORDER BY last_activity DESC LIMIT 1
"""

# {direction} is filled with DESC/ASC from a bool — never caller text.
SQL_LIST_SESSIONS = """
SELECT session_id, public_ref, started_at, last_activity, name,
       session_type, origin_automation_id, parent_session_id, parent_tool_call_id,
       agent_type, agent_status, area_id, chat_model,
       active_message_count AS message_count
FROM sessions
WHERE archived_at IS NULL
ORDER BY last_activity {direction}
LIMIT ? OFFSET ?
"""

SQL_LIST_PRIMARY_SESSIONS = """
SELECT session_id, public_ref, started_at, last_activity, name,
       session_type, origin_automation_id, parent_session_id, parent_tool_call_id,
       agent_type, agent_status, area_id, chat_model,
       active_message_count AS message_count
FROM sessions
WHERE archived_at IS NULL
  AND COALESCE(session_type, 'chat') != 'agent'
ORDER BY last_activity {direction}
LIMIT ? OFFSET ?
"""

SQL_LIST_ARCHIVED = """
SELECT session_id, public_ref, started_at, last_activity, name, archived_at,
       session_type, origin_automation_id, parent_session_id, parent_tool_call_id,
       agent_type, agent_status, area_id, chat_model,
       COALESCE(storage_state, 'hot') AS storage_state,
       COALESCE(cold_bundle_bytes, 0) AS cold_bundle_bytes,
       CASE WHEN COALESCE(storage_state, 'hot') = 'cold'
            THEN COALESCE(cold_message_count, 0)
            ELSE active_message_count END AS message_count
FROM sessions
WHERE archived_at IS NOT NULL
ORDER BY archived_at DESC
LIMIT ?
"""

SQL_LOAD_SESSION = "SELECT * FROM sessions WHERE session_id = ?"
SQL_LOAD_SESSION_HISTORY_HEADER = """
SELECT s.session_id, s.public_ref, s.started_at, s.last_activity, s.metadata, s.name, s.session_type,
       s.origin_automation_id, s.parent_session_id, s.parent_tool_call_id,
       s.agent_type, s.agent_status, s.area_id, s.chat_model, s.context_generation,
       s.active_message_count
FROM sessions AS s
WHERE s.session_id = ?
"""
# Upsert: a fresh session won't have a row yet on its very first save,
# and an UPDATE-only would silently no-op (lost user message until the
# final end-of-run save).
SQL_UPSERT_PROGRESS = """
INSERT INTO sessions (
    session_id, public_ref, started_at, last_activity, messages, metadata, name,
    session_type, origin_automation_id, parent_session_id, parent_tool_call_id,
    agent_type, agent_status, area_id, chat_model, context_generation, active_message_count
)
VALUES (?, ?, ?, ?, ?, '{}', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(session_id) DO UPDATE SET
    messages = excluded.messages,
    active_message_count = excluded.active_message_count,
    last_activity = excluded.last_activity,
    agent_status = excluded.agent_status,
    area_id = sessions.area_id
WHERE sessions.context_generation = excluded.context_generation
"""
SQL_UPDATE_NAME = "UPDATE sessions SET name = ? WHERE session_id = ?"
SQL_UPDATE_NAME_IF_EMPTY = "UPDATE sessions SET name = ? WHERE session_id = ? AND (name IS NULL OR name = '')"
SQL_UPDATE_SESSION_AREA = "UPDATE sessions SET area_id = ? WHERE session_id = ?"
SQL_UPDATE_SESSION_CHAT_MODEL = "UPDATE sessions SET chat_model = ? WHERE session_id = ?"
SQL_SELECT_ARCHIVED_AT = "SELECT archived_at FROM sessions WHERE session_id = ?"
SQL_ARCHIVE = "UPDATE sessions SET archived_at = ? WHERE session_id = ? AND archived_at IS NULL"
SQL_RESTORE = "UPDATE sessions SET archived_at = NULL WHERE session_id = ? AND archived_at IS NOT NULL"
SQL_DELETE_ARCHIVED = "DELETE FROM sessions WHERE session_id = ? AND archived_at IS NOT NULL"


class SessionStore:
    def __init__(
        self,
        conn: aiosqlite.Connection,
        read_conn: aiosqlite.Connection | None = None,
        chat_completion_conn: aiosqlite.Connection | None = None,
        *,
        event_conn: aiosqlite.Connection,
    ):
        self.conn = conn
        self.read_conn = read_conn or conn
        self.chat_completion_conn = chat_completion_conn
        self._background_event_lock = asyncio.Lock()
        self._storage_maintenance_lock = asyncio.Lock()
        # Context snapshots span projection, transcript, checkpoint, and turn
        # writes. Keep those logical transactions off the shared connection so
        # another session's commit cannot release their savepoint mid-flight.
        self._session_context_transaction_lock = asyncio.Lock()
        self._session_locks_guard = asyncio.Lock()
        self._session_write_locks: dict[str, asyncio.Lock] = {}
        self.areas = AreaStore(conn, self.read_conn)
        self.goals = GoalStore(conn, self.read_conn, self._session_write_lock)
        self.transcripts = TranscriptStore(self.read_conn)
        self.storage = SessionStorageStore(
            conn,
            self.read_conn,
            self._storage_maintenance_lock,
            self._session_write_lock,
            self._strict_active_projection,
        )
        self.background_agents = BackgroundAgentStore(conn, self.read_conn, self._background_event_lock)
        self.background_starts = BackgroundStartStore(conn, self._background_event_lock)
        self.background_cancellations = BackgroundCancellationStore(conn, self._background_event_lock)
        self.chat_runs = ChatRunStore(conn, self.read_conn, chat_completion_conn)
        self.chat_idempotency = ChatIdempotencyStore(conn, self.read_conn)
        self.events = SessionEventStore(event_conn, self.read_conn)

    async def _session_write_lock(self, session_id: str) -> asyncio.Lock:
        async with self._session_locks_guard:
            lock = self._session_write_locks.get(session_id)
            if lock is None:
                lock = asyncio.Lock()
                self._session_write_locks[session_id] = lock
            return lock

    @asynccontextmanager
    async def _session_context_transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._session_context_transaction_lock, self.storage.maintenance_connection() as connection:
            await connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
                await connection.commit()
            except BaseException:
                await connection.rollback()
                raise

    async def _update(self, sql: str, params: tuple) -> bool:
        cursor = await self.conn.execute(sql, params)
        await self.conn.commit()
        return cursor.rowcount > 0

    async def init_schema(self) -> None:
        await initialize_context_schema(self.conn)

    @staticmethod
    def _session_ref_text(name: object, session_type: object) -> tuple[str, str]:
        kind = str(session_type) if session_type in {"chat", "channel", "agent"} else "chat"
        title = name.strip() if isinstance(name, str) and name.strip() else kind
        return title, kind

    @classmethod
    def _strict_active_projection(cls, session_id: str, raw_messages: object) -> list[dict]:
        if not isinstance(raw_messages, str):
            raise SessionDataCorruptionError(f"session {session_id} messages projection is missing")
        try:
            messages = json.loads(raw_messages, parse_constant=_reject_nonfinite_json_constant)
        except ValueError as exc:
            raise SessionDataCorruptionError(f"session {session_id} messages projection is invalid JSON") from exc
        if not isinstance(messages, list):
            raise SessionDataCorruptionError(f"session {session_id} messages projection must be an array")

        message_ids: set[str] = set()
        for index, message in enumerate(messages):
            if not isinstance(message, dict):
                raise SessionDataCorruptionError(f"session {session_id} projection message {index} must be an object")
            cls._validate_persisted_message(session_id, message, source=f"projection message {index}")
            message_id = message["message_id"]
            if cls._is_handoff_message_payload(message):
                try:
                    cls._validated_handoff_receipt(message, message_id)
                except ValueError as exc:
                    raise SessionDataCorruptionError(
                        f"session {session_id} projection message {index} has an invalid handoff receipt"
                    ) from exc
            if message_id in message_ids:
                raise SessionDataCorruptionError(f"session {session_id} projection duplicates message_id {message_id}")
            message_ids.add(message_id)
        return messages

    async def ensure_startup_recovery_indexes(self) -> None:
        """Build recovery-only indexes after core API readiness."""

        await self.conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_chat_runs_recovery_status
                ON chat_runs(status)
                WHERE status IN ('pending', 'running', 'backgrounded', 'interrupted');
            CREATE INDEX IF NOT EXISTS idx_background_agent_runs_recovery_status
                ON background_agent_runs(status, updated_at)
                WHERE status IN ('running', 'activity', 'cancel_requested', 'interrupted');
            CREATE INDEX IF NOT EXISTS idx_background_agent_runs_undelivered
                ON background_agent_runs(ended_at)
                WHERE completion_id IS NOT NULL AND notified_at IS NULL;
            CREATE INDEX IF NOT EXISTS idx_sessions_agent_recovery
                ON sessions(agent_status)
                WHERE session_type = 'agent' AND agent_status = 'running';
            """
        )
        await self.conn.commit()

    async def create_area(
        self,
        *,
        name: str,
        default_cwd: str | None = None,
        instructions: str | None = None,
        page_path: str | None = None,
        autonomy: str | None = None,
    ) -> dict:
        return await self.areas.create(
            name=name,
            default_cwd=default_cwd,
            instructions=instructions,
            page_path=page_path,
            autonomy=autonomy,
        )

    async def get_area(self, area_id: str | None) -> dict | None:
        return await self.areas.get(area_id)

    async def get_area_by_ref(self, area_ref: str) -> dict | None:
        return await self.areas.get_by_ref(area_ref)

    async def find_area_by_page_id(self, page_id: str) -> dict | None:
        return await self.areas.find_by_page_id(page_id)

    async def list_areas(self) -> list[dict]:
        return await self.areas.list_active()

    async def update_area(
        self,
        area_id: str,
        *,
        name: str | object = AREA_PATCH_UNSET,
        default_cwd: str | object | None = AREA_PATCH_UNSET,
        instructions: str | object | None = AREA_PATCH_UNSET,
        page_path: str | object | None = AREA_PATCH_UNSET,
        page_id: str | object | None = AREA_PATCH_UNSET,
        autonomy: str | object | None = AREA_PATCH_UNSET,
        attention: str | object = AREA_PATCH_UNSET,
        interrupts: str | object = AREA_PATCH_UNSET,
        paused: bool | object = AREA_PATCH_UNSET,
    ) -> dict | None:
        return await self.areas.update(
            area_id,
            name=name,
            default_cwd=default_cwd,
            instructions=instructions,
            page_path=page_path,
            page_id=page_id,
            autonomy=autonomy,
            attention=attention,
            interrupts=interrupts,
            paused=paused,
        )

    async def archive_area(self, area_id: str) -> bool:
        return await self.areas.archive(area_id)

    async def restore_area(self, area_id: str) -> dict | None:
        return await self.areas.restore(area_id)

    async def set_goal(
        self,
        session_id: str,
        objective: str,
        *,
        token_budget: int | None = None,
    ) -> dict:
        return await self.goals.set(session_id, objective, token_budget=token_budget)

    async def get_goal(self, session_id: str) -> dict | None:
        return await self.goals.get(session_id)

    async def set_todo_override(
        self,
        session_id: str,
        items: list[dict],
        explanation: str | None = None,
    ) -> dict:
        return await self.goals.set_todo_override(session_id, items, explanation)

    async def get_todo_override(self, session_id: str) -> dict | None:
        return await self.goals.get_todo_override(session_id)

    async def clear_todo_override(self, session_id: str) -> bool:
        return await self.goals.clear_todo_override(session_id)

    async def set_session_todos(
        self,
        session_id: str,
        items: list[dict],
        explanation: str | None = None,
    ) -> dict:
        return await self.goals.set_session_todos(session_id, items, explanation)

    async def get_session_todos(self, session_id: str) -> dict | None:
        return await self.goals.get_session_todos(session_id)

    async def clear_session_todos(self, session_id: str) -> bool:
        return await self.goals.clear_session_todos(session_id)

    async def clear_goal(self, session_id: str) -> bool:
        return await self.goals.clear(session_id)

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
        return await self.goals.update(
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

    def _to_serializable_messages(self, messages: list[dict]) -> list[dict]:
        serializable: list[dict] = []
        for index, msg in enumerate(messages):
            if not isinstance(msg, dict):
                raise TypeError(f"message {index} must be an object")
            serializable.append(msg)
        return serializable

    def _stamp_messages(self, messages: list[dict], now: str) -> None:
        seen: set[str] = set()
        for index, msg in enumerate(messages):
            role = msg.get("role")
            if not isinstance(role, str) or role not in _MESSAGE_ROLES:
                raise ValueError(f"message {index} has invalid role")

            created_at = msg.get("created_at")
            if created_at is None:
                msg["created_at"] = now
            elif not isinstance(created_at, str) or not created_at:
                raise ValueError(f"message {index} has invalid created_at")
            else:
                try:
                    parsed_created_at = datetime.fromisoformat(created_at)
                except ValueError as exc:
                    raise ValueError(f"message {index} has invalid created_at") from exc
                if parsed_created_at.tzinfo is None:
                    raise ValueError(f"message {index} created_at must include a timezone")

            for field in ("message_id", "client_id"):
                value = msg.get(field)
                if field in msg and (not isinstance(value, str) or not value):
                    raise ValueError(f"message {index} has invalid {field}")

            message_id = msg.get("message_id") or msg.get("client_id")
            if message_id is None:
                message_id = f"msg-{uuid4().hex[:16]}"
            if message_id in seen:
                raise ValueError(f"message {index} duplicates message_id {message_id!r}")
            msg["message_id"] = message_id
            seen.add(message_id)

    @staticmethod
    def _message_id_sequence(messages: list[dict]) -> list[str] | None:
        ids: list[str] = []
        for message in messages:
            message_id = message.get("message_id") or message.get("client_id")
            if not isinstance(message_id, str) or not message_id:
                return None
            ids.append(message_id)
        return ids

    @staticmethod
    def _projection_etag(raw_messages: str | None) -> str:
        marker = b"\x00" if raw_messages is None else b"\x01" + raw_messages.encode()
        return hashlib.sha256(marker).hexdigest()

    @classmethod
    def renew_handoff_projection(
        cls,
        messages: list[dict],
        *,
        messages_before: int,
        reason: str,
    ) -> list[dict]:
        """Renew a copied handoff so its identity and retained-tail receipt stay true."""
        handoff_index = next(
            (index for index in range(len(messages) - 1, -1, -1) if cls._is_handoff_message_payload(messages[index])),
            None,
        )
        if handoff_index is None:
            return messages

        tail = messages[handoff_index + 1 :]
        tail_ids = cls._message_id_sequence(tail)
        if tail_ids is None:
            raise ValueError("rewind requires stable retained-tail message ids")
        previous = messages[handoff_index]
        previous_id = previous.get("message_id")
        compaction_id = f"compact-{uuid4().hex[:16]}"
        compaction: dict[str, Any] = {
            "compaction_id": compaction_id,
            "kind": "session_handoff",
            "messages_before": messages_before,
            "messages_after": len(messages),
            "preserved_tail_ids": tail_ids,
            "reason": reason,
        }
        if isinstance(previous_id, str) and previous_id:
            compaction["parent_compaction_id"] = previous_id
        previous_compaction = previous.get("compaction")
        if isinstance(previous_compaction, dict):
            if isinstance(previous_compaction.get("rehydration"), dict):
                compaction["rehydration"] = previous_compaction["rehydration"]
            tool_result_refs = previous_compaction.get("tool_result_refs")
            if isinstance(tool_result_refs, list):
                refs = list(dict.fromkeys(ref for ref in tool_result_refs if isinstance(ref, str) and ref))
                if refs:
                    compaction["tool_result_refs"] = refs
            background_result_refs = previous_compaction.get("background_result_refs")
            if isinstance(background_result_refs, list):
                refs = list(dict.fromkeys(ref for ref in background_result_refs if isinstance(ref, str) and ref))
                if refs:
                    compaction["background_result_refs"] = refs
        replacement = {
            "role": previous["role"],
            "content": previous["content"],
            "message_id": compaction_id,
            "compaction": compaction,
        }
        return [*messages[:handoff_index], replacement, *tail]

    @staticmethod
    def active_background_result_ids(messages: list[dict]) -> set[str]:
        """Return durable background task IDs referenced by active context."""
        refs: set[str] = set()
        for message in messages:
            direct = message.get("background_result_ref")
            if isinstance(direct, str) and direct:
                refs.add(direct)
            compaction = message.get("compaction")
            nested = compaction.get("background_result_refs") if isinstance(compaction, dict) else None
            if isinstance(nested, list):
                refs.update(ref for ref in nested if isinstance(ref, str) and ref)
        return refs

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

    async def _mirror_session_messages(
        self,
        session_id: str,
        messages: list[dict],
        *,
        connection: aiosqlite.Connection,
    ) -> None:
        # session_messages is the durable transcript. Normal snapshot saves
        # may append rows, but never rewrite or delete an existing message.
        # Explicit user rewind/session deletion use dedicated destructive APIs.
        if not messages:
            return

        max_seq_rows = await connection.execute_fetchall(
            "SELECT COALESCE(MAX(seq), -1) AS max_seq FROM session_messages WHERE session_id = ?",
            (session_id,),
        )
        next_seq = int(max_seq_rows[0]["max_seq"]) + 1
        active_ids = [message["message_id"] for message in messages]
        existing: dict[str, aiosqlite.Row | dict] = {}
        for start in range(0, len(active_ids), 500):
            batch = active_ids[start : start + 500]
            placeholders = ",".join("?" for _ in batch)
            rows = await connection.execute_fetchall(
                f"""
                SELECT message_id, seq, message_json
                FROM session_messages
                WHERE session_id = ? AND message_id IN ({placeholders})
                """,
                (session_id, *batch),
            )
            existing.update((row["message_id"], row) for row in rows)

        for msg in messages:
            message_id = msg["message_id"]
            role = msg["role"]
            client_id = msg.get("client_id")
            created_at = msg["created_at"]
            message_json = await asyncio.to_thread(lambda m=msg: _encode_json(m))
            search_text = self._flatten_message_text(msg)

            if message_id not in existing:
                await connection.execute(
                    """
                    INSERT INTO session_messages
                        (session_id, message_id, seq, role, message_json, client_id, created_at, search_text)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (session_id, message_id, next_seq, role, message_json, client_id, created_at, search_text),
                )
                existing[message_id] = {
                    "message_id": message_id,
                    "seq": next_seq,
                    "message_json": message_json,
                }
                next_seq += 1
            else:
                try:
                    persisted = json.loads(
                        existing[message_id]["message_json"],
                        parse_constant=_reject_nonfinite_json_constant,
                    )
                except (TypeError, ValueError) as exc:
                    raise SessionDataCorruptionError(
                        f"session {session_id} message {message_id} has invalid transcript JSON"
                    ) from exc
                if not isinstance(persisted, dict):
                    raise SessionDataCorruptionError(
                        f"session {session_id} message {message_id} transcript envelope must be an object"
                    )
                if (
                    self._is_handoff_message_payload(persisted) or self._is_handoff_message_payload(msg)
                ) and persisted != msg:
                    raise SessionDataCorruptionError(
                        f"session {session_id} handoff {message_id} conflicts with its immutable transcript message"
                    )
            await self._record_compaction_message(
                session_id=session_id,
                message_id=message_id,
                seq=existing[message_id]["seq"],
                message=msg,
                created_at=created_at,
                connection=connection,
            )
        await self._rebuild_session_turns(session_id, connection=connection)

    async def _record_compaction_message(
        self,
        *,
        session_id: str,
        message_id: str,
        seq: int,
        message: dict,
        created_at: str,
        connection: aiosqlite.Connection,
    ) -> None:
        if not self._is_handoff_message_payload(message):
            return
        compaction, _retained_ids, rehydration = self._validated_handoff_receipt(message, message_id)
        messages_before = compaction["messages_before"]
        messages_after = compaction["messages_after"]
        rehydration_json = _encode_json(rehydration) if rehydration is not None else None
        existing_rows = await connection.execute_fetchall(
            """
            SELECT boundary_seq, messages_before, messages_after, rehydration_state, created_at
            FROM chat_compactions
            WHERE session_id = ? AND compaction_id = ?
            """,
            (session_id, message_id),
        )
        if existing_rows:
            existing = existing_rows[0]
            try:
                existing_rehydration = (
                    json.loads(existing["rehydration_state"], parse_constant=_reject_nonfinite_json_constant)
                    if existing["rehydration_state"] is not None
                    else None
                )
            except (TypeError, ValueError) as exc:
                raise SessionDataCorruptionError(
                    f"session {session_id} compaction {message_id} has invalid rehydration state"
                ) from exc
            if (
                existing["boundary_seq"] != seq
                or existing["messages_before"] != messages_before
                or existing["messages_after"] != messages_after
                or existing_rehydration != rehydration
                or existing["created_at"] != created_at
            ):
                raise SessionDataCorruptionError(
                    f"session {session_id} compaction {message_id} conflicts with its receipt"
                )
            return
        await connection.execute(
            """
            INSERT INTO chat_compactions (
                compaction_id, session_id, boundary_seq, messages_before, messages_after,
                rehydration_state, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_id,
                session_id,
                seq,
                messages_before,
                messages_after,
                rehydration_json,
                created_at,
            ),
        )

    @staticmethod
    def _validated_handoff_receipt(message: dict, message_id: str) -> tuple[dict, list[str], dict | None]:
        compaction = message["compaction"]
        content = message.get("content")
        if not isinstance(content, str) or not content.startswith(SESSION_HANDOFF_MARKER):
            raise ValueError("session handoff content must start with the handoff marker")
        compaction_id = compaction.get("compaction_id")
        if not isinstance(compaction_id, str) or not compaction_id or compaction_id != message_id:
            raise ValueError("session handoff compaction_id must equal message_id")
        messages_before = compaction.get("messages_before")
        messages_after = compaction.get("messages_after")
        if (
            isinstance(messages_before, bool)
            or not isinstance(messages_before, int)
            or messages_before < 0
            or isinstance(messages_after, bool)
            or not isinstance(messages_after, int)
            or messages_after < 0
        ):
            raise ValueError("session handoff message counts must be nonnegative integers")
        retained_ids = compaction.get("preserved_tail_ids")
        if (
            not isinstance(retained_ids, list)
            or not all(isinstance(value, str) and value for value in retained_ids)
            or len(set(retained_ids)) != len(retained_ids)
        ):
            raise ValueError("session handoff preserved_tail_ids must contain unique message IDs")
        rehydration = compaction.get("rehydration")
        if rehydration is not None and not isinstance(rehydration, dict):
            raise ValueError("session handoff rehydration must be an object")
        return compaction, retained_ids, rehydration

    @staticmethod
    def _is_handoff_message_payload(message: dict) -> bool:
        compaction = message.get("compaction")
        return isinstance(compaction, dict) and compaction.get("kind") == "session_handoff"

    @classmethod
    def _active_context_from_transcript_rows(
        cls,
        session_id: str,
        rows: list[aiosqlite.Row],
    ) -> list[dict]:
        """Recover the active model view when its saved projection is invalid.

        ``sessions.messages`` is the authoritative active projection during
        ordinary operation. The immutable transcript is a recovery source, not
        a second live view: independently saved snapshots can contain branches,
        and the system row is intentionally refreshed in place. A modern
        handoff makes recovery deterministic by naming its retained tail.
        Legacy handoffs lack that information, so recovery fails closed.
        """
        parsed: list[tuple[int, str, dict]] = []
        for row in rows:
            try:
                message = json.loads(row["message_json"], parse_constant=_reject_nonfinite_json_constant)
            except (TypeError, ValueError) as exc:
                raise SessionDataCorruptionError(
                    f"session {session_id} transcript message {row['message_id']} is invalid JSON"
                ) from exc
            if not isinstance(message, dict):
                raise SessionDataCorruptionError(
                    f"session {session_id} transcript message {row['message_id']} must be an object"
                )
            cls._validate_persisted_message(session_id, message, source=f"transcript message {row['message_id']}")
            if (
                message["message_id"] != row["message_id"]
                or message["role"] != row["role"]
                or message["created_at"] != row["created_at"]
            ):
                raise SessionDataCorruptionError(
                    f"session {session_id} transcript message {row['message_id']} conflicts with its row"
                )
            parsed.append((int(row["seq"]), str(row["message_id"]), message))

        if not parsed:
            raise SessionDataCorruptionError(
                f"session {session_id} active projection is invalid and its transcript is empty"
            )

        handoffs = [item for item in parsed if cls._is_handoff_message_payload(item[2])]
        if not handoffs:
            return [message for _seq, _message_id, message in parsed]

        checkpoint: tuple[int, str, dict] | None = None
        retained_ids: list[str] | None = None
        for item in handoffs:
            try:
                _compaction, candidate_ids, _rehydration = cls._validated_handoff_receipt(item[2], item[1])
            except ValueError:
                continue
            checkpoint = item
            retained_ids = candidate_ids

        if checkpoint is None or retained_ids is None or handoffs[-1][0] != checkpoint[0]:
            raise SessionDataCorruptionError(
                f"session {session_id} active projection has no recoverable current handoff"
            )

        by_id = {message_id: message for _seq, message_id, message in parsed}
        if any(message_id not in by_id for message_id in retained_ids):
            raise SessionDataCorruptionError(
                f"session {session_id} current handoff references unavailable transcript messages"
            )

        system = next((message for _seq, _id, message in parsed if message.get("role") == "system"), None)
        checkpoint_seq, checkpoint_id, checkpoint_message = checkpoint
        retained = [by_id[message_id] for message_id in retained_ids]
        retained_set = set(retained_ids)
        appended = [
            message
            for seq, message_id, message in parsed
            if seq > checkpoint_seq
            and message_id != checkpoint_id
            and message_id not in retained_set
            and message.get("role") != "system"
        ]
        return [*([system] if system is not None else []), checkpoint_message, *retained, *appended]

    @staticmethod
    def _validate_persisted_message(session_id: str, message: dict, *, source: str) -> None:
        role = message.get("role")
        if not isinstance(role, str) or role not in _MESSAGE_ROLES:
            raise SessionDataCorruptionError(f"session {session_id} {source} has invalid role")
        for field in ("message_id", "created_at"):
            value = message.get(field)
            if not isinstance(value, str) or not value:
                raise SessionDataCorruptionError(f"session {session_id} {source} has invalid {field}")
        try:
            created_at = datetime.fromisoformat(message["created_at"])
        except ValueError as exc:
            raise SessionDataCorruptionError(f"session {session_id} {source} has invalid created_at") from exc
        if created_at.tzinfo is None:
            raise SessionDataCorruptionError(f"session {session_id} {source} created_at has no timezone")
        client_id = message.get("client_id")
        if "client_id" in message and (not isinstance(client_id, str) or not client_id):
            raise SessionDataCorruptionError(f"session {session_id} {source} has invalid client_id")

    def _is_turn_message(self, row: aiosqlite.Row) -> bool:
        if row["role"] == "system":
            return False
        message = json.loads(row["message_json"])
        return not self._is_handoff_message_payload(message)

    async def _rebuild_session_turns(
        self,
        session_id: str,
        *,
        connection: aiosqlite.Connection,
    ) -> None:
        rows = await connection.execute_fetchall(
            "SELECT * FROM session_messages WHERE session_id = ? ORDER BY seq ASC",
            (session_id,),
        )
        await connection.execute("DELETE FROM session_turns WHERE session_id = ?", (session_id,))

        current_start: aiosqlite.Row | None = None
        current_end: aiosqlite.Row | None = None
        turn_index = 0

        async def flush_current() -> None:
            nonlocal current_start, current_end, turn_index
            if current_start is None or current_end is None:
                return
            turn_id = f"{session_id}:{turn_index}"
            await connection.execute(
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

    async def update_progress(self, state: SessionState, messages: list[dict]) -> None:
        """Lightweight mid-run save: rewrite messages + bump last_activity,
        upserting the row so a fresh session's first save lands instead of
        silently no-op'ing. Leaves metadata alone — the final save in the
        chat service re-stamps last_input_tokens."""
        lock = await self._session_write_lock(state.session_id)
        async with lock:
            async with self._session_context_transaction() as connection:
                serializable = self._to_serializable_messages(messages)
                await self._ensure_session_public_ref(state, connection)
                now = datetime.now(UTC).isoformat()
                self._stamp_messages(serializable, now)

                messages_json = await asyncio.to_thread(lambda: _encode_json(serializable))
                cursor = await connection.execute(
                    SQL_UPSERT_PROGRESS,
                    (
                        state.session_id,
                        state.public_ref,
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
                        state.context_generation,
                        len(serializable),
                    ),
                )
                if cursor.rowcount == 0:
                    raise StaleSessionContextError(
                        f"session {state.session_id} context was replaced while its run was active"
                    )
                await self._mirror_session_messages(state.session_id, serializable, connection=connection)
            state.context_etag = self._projection_etag(messages_json)

    async def record_chat_run_started(
        self,
        run_id: str,
        session_id: str,
        *,
        metadata: dict | None = None,
    ) -> None:
        await self.chat_runs.record_chat_run_started(run_id, session_id, metadata=metadata)

    async def prune_expired_chat_idempotency_keys(self, now: datetime | None = None) -> int:
        return await self.chat_idempotency.prune_expired_chat_idempotency_keys(now)

    async def claim_chat_idempotency_key(
        self,
        *,
        session_id: str,
        client_id: str,
        request_hash: str,
        status: str = "accepted",
        expires_at: str | None = None,
    ) -> tuple[bool, dict]:
        return await self.chat_idempotency.claim_chat_idempotency_key(
            session_id=session_id,
            client_id=client_id,
            request_hash=request_hash,
            status=status,
            expires_at=expires_at,
        )

    async def get_chat_idempotency_key(self, session_id: str, client_id: str) -> dict | None:
        return await self.chat_idempotency.get_chat_idempotency_key(session_id, client_id)

    async def update_chat_idempotency_key(
        self,
        *,
        session_id: str,
        client_id: str,
        status: str,
        run_id: str | None = None,
        message_id: str | None = None,
    ) -> dict | None:
        return await self.chat_idempotency.update_chat_idempotency_key(
            session_id=session_id,
            client_id=client_id,
            status=status,
            run_id=run_id,
            message_id=message_id,
        )

    async def cancel_chat_idempotency_key(
        self,
        *,
        session_id: str,
        client_id: str,
        run_id: str | None = None,
    ) -> str:
        return await self.chat_idempotency.cancel_chat_idempotency_key(
            session_id=session_id,
            client_id=client_id,
            run_id=run_id,
        )

    async def release_interrupted_queued_receipts(self) -> int:
        return await self.chat_idempotency.release_interrupted_queued_receipts()

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
        await self.chat_runs.record_chat_run_status(
            run_id,
            status,
            stop_reason=stop_reason,
            last_seq=last_seq,
            error_code=error_code,
            error_message=error_message,
        )

    async def record_chat_run_completed_with_outbox(
        self,
        event: RunCompleted,
        *,
        stop_reason: str | None,
        last_seq: int | None,
    ) -> None:
        await self.chat_runs.record_chat_run_completed_with_outbox(
            event,
            stop_reason=stop_reason,
            last_seq=last_seq,
        )

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
        await self.chat_runs.record_chat_run_failed_with_outbox(
            event,
            status=status,
            stop_reason=stop_reason,
            last_seq=last_seq,
            error_code=error_code,
            error_message=error_message,
        )

    async def get_chat_run(self, run_id: str) -> dict | None:
        return await self.chat_runs.get_chat_run(run_id)

    async def get_latest_chat_run_for_session(self, session_id: str) -> dict | None:
        return await self.chat_runs.get_latest_chat_run_for_session(session_id)

    async def list_interrupted_chat_runs(self) -> list[dict]:
        return await self.chat_runs.list_interrupted_chat_runs()

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
        return [tool_approval_payload(row) for row in rows]

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
        return [tool_approval_payload(row) for row in rows]

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
        return [tool_call_payload(row) for row in rows]

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

    async def create_session_branch(
        self,
        *,
        source_session_id: str,
        state: SessionState,
        messages: list[dict],
        tool_call_ids: set[str],
        background_agent_refs: set[str],
        metadata: dict | None = None,
    ) -> None:
        """Atomically persist a branch and its durable recovery records."""
        lock = await self._session_write_lock(state.session_id)
        async with lock, self._session_context_transaction() as connection:
            source = await connection.execute_fetchall(
                "SELECT 1 FROM sessions WHERE session_id = ?",
                (source_session_id,),
            )
            if not source:
                raise ValueError(f"source session {source_session_id} no longer exists")

            serializable, etag = await self._save_session_snapshot_unlocked(
                state,
                messages,
                metadata,
                connection=connection,
            )

            await self.events.clone_for_branch(
                connection=connection,
                source_session_id=source_session_id,
                target_session_id=state.session_id,
                tool_call_ids=tool_call_ids,
                projection=_encode_json(serializable),
            )

            if background_agent_refs:
                placeholders = ",".join("?" for _ in background_agent_refs)
                rows = await connection.execute_fetchall(
                    f"""
                    SELECT * FROM background_agent_runs
                    WHERE session_id = ? AND agent_ref IN ({placeholders})
                      AND status IN ('completed', 'failed', 'cancelled', 'interrupted')
                      AND completion_id IS NOT NULL
                    """,
                    (source_session_id, *sorted(background_agent_refs)),
                )
                by_ref = {str(row["agent_ref"]): row for row in rows}
                missing = sorted(background_agent_refs - by_ref.keys())
                if missing:
                    raise ValueError(f"background results unavailable for branch: {', '.join(missing)}")
                now = datetime.now(UTC).isoformat()
                await connection.executemany(
                    """
                    INSERT INTO background_agent_runs (
                        task_id, agent_ref, session_id, parent_run_id, parent_tool_call_id,
                        suspension_id, child_session_id, agent_type, wait, status,
                        command, detail, result_ref, result_text, created_at,
                        started_at, updated_at, ended_at, cancel_requested_at,
                        cancel_actor, terminal_cause, cancel_generation,
                        cancel_idempotency_key, notified_at, completion_id,
                        spawn_spec, spawn_attempts
                    ) VALUES (
                        ?, ?, ?, NULL, NULL, NULL, NULL, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        NULL, NULL, NULL, 0, NULL, ?, ?, NULL, 0
                    )
                    """,
                    [
                        (
                            row["task_id"],
                            row["agent_ref"],
                            state.session_id,
                            row["agent_type"],
                            row["status"],
                            row["command"],
                            row["detail"],
                            row["result_ref"],
                            row["result_text"],
                            row["created_at"],
                            row["started_at"],
                            row["updated_at"],
                            row["ended_at"],
                            now,
                            f"branch:{state.session_id}:{row['task_id']}:{row['status']}",
                        )
                        for row in by_ref.values()
                    ],
                )
        state.context_etag = etag

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
        from arden.integrations.connection_request import IntegrationConnectionRequestEnvelope

        envelope = IntegrationConnectionRequestEnvelope.from_descriptor(
            descriptor,
            source=source,
            detail=detail,
        )
        await self.record_run_suspension(
            run_id=run_id,
            session_id=session_id,
            suspension_id=tool_call_id,
            kind="integration_connection",
            payload=envelope.model_dump(mode="json"),
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
        return tool_approval_payload(rows[0])

    async def mark_run_suspension_consumed(self, *, run_id: str, suspension_id: str) -> bool:
        return await self._update(
            """
            UPDATE tool_calls SET status = 'running'
            WHERE run_id = ? AND tool_call_id = ? AND status = 'awaiting'
            """,
            (run_id, suspension_id),
        )

    async def mark_interrupted_chat_runs(self) -> int:
        return await self.chat_runs.mark_interrupted_chat_runs()

    async def record_background_agent_started(
        self,
        *,
        task_id: str,
        agent_ref: str,
        session_id: str,
        parent_run_id: str | None,
        command: str,
        parent_tool_call_id: str | None = None,
        suspension_id: str | None = None,
        child_session_id: str | None = None,
        agent_type: str = "background_research",
        wait: bool = False,
        spawn_spec: str | None = None,
    ) -> BackgroundStartDisposition:
        return await self.background_starts.record_background_agent_started(
            task_id=task_id,
            agent_ref=agent_ref,
            session_id=session_id,
            parent_run_id=parent_run_id,
            command=command,
            parent_tool_call_id=parent_tool_call_id,
            suspension_id=suspension_id,
            child_session_id=child_session_id,
            agent_type=agent_type,
            wait=wait,
            spawn_spec=spawn_spec,
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
        return await self.background_agents.record_background_agent_event(
            task_id=task_id,
            session_id=session_id,
            status=status,
            detail=detail,
            result_ref=result_ref,
        )

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
        await self.background_agents.record_background_agent_finished(
            task_id=task_id,
            session_id=session_id,
            status=status,
            result_ref=result_ref,
            detail=detail,
            result_text=result_text,
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
        return await self.background_agents.claim_background_agent_completion(
            task_id=task_id,
            session_id=session_id,
            status=status,
            completion_id=completion_id,
            result_ref=result_ref,
            detail=detail,
            result_text=result_text,
        )

    async def mark_background_completion_delivered(
        self,
        *,
        session_id: str,
        task_id: str,
        completion_id: str,
    ) -> bool:
        return await self.background_agents.mark_background_completion_delivered(
            session_id=session_id,
            task_id=task_id,
            completion_id=completion_id,
        )

    async def list_undelivered_background_completions(self) -> list[dict]:
        return await self.background_agents.list_undelivered_background_completions()

    async def request_background_agent_cancel(self, session_id: str, task_id: str) -> bool:
        return await self.background_cancellations.request_background_agent_cancel(session_id, task_id)

    async def request_background_agent_cancel_cascade(
        self,
        *,
        session_id: str,
        task_id: str,
        actor: str,
        cause: str,
        idempotency_key: str,
    ) -> list[tuple[str, str]]:
        return await self.background_cancellations.request_background_agent_cancel_cascade(
            session_id=session_id,
            task_id=task_id,
            actor=actor,
            cause=cause,
            idempotency_key=idempotency_key,
        )

    async def get_background_agent_result(self, session_id: str, task_id: str) -> str | None:
        return await self.background_agents.get_background_agent_result(session_id, task_id)

    async def get_background_agent_result_by_ref(self, session_id: str, agent_ref: str) -> str | None:
        return await self.background_agents.get_background_agent_result_by_ref(session_id, agent_ref)

    async def get_background_agent_run(self, session_id: str, task_id: str) -> dict | None:
        return await self.background_agents.get_background_agent_run(session_id, task_id)

    async def list_respawnable_background_agent_runs(self, *, max_attempts: int) -> list[dict]:
        return await self.background_agents.list_respawnable_background_agent_runs(max_attempts=max_attempts)

    async def increment_background_agent_spawn_attempts(self, session_id: str, task_id: str) -> int:
        return await self.background_agents.increment_background_agent_spawn_attempts(session_id, task_id)

    async def list_background_agent_runs(
        self,
        session_id: str,
        *,
        include_terminal: bool = True,
    ) -> list[dict]:
        return await self.background_agents.list_background_agent_runs(
            session_id,
            include_terminal=include_terminal,
        )

    async def list_background_agent_events(
        self,
        session_id: str,
        *,
        after_seq: int = 0,
        limit: int = 10000,
    ) -> list[dict]:
        return await self.background_agents.list_background_agent_events(
            session_id,
            after_seq=after_seq,
            limit=limit,
        )

    async def mark_interrupted_background_agent_runs(self) -> int:
        return await self.background_cancellations.mark_interrupted_background_agent_runs()

    async def mark_interrupted_agent_sessions(self) -> int:
        return await self.background_cancellations.mark_interrupted_agent_sessions()

    async def list_capture_eligible_sessions(self, *, active_within_days: int = 14) -> list[tuple[str, int]]:
        """User chat sessions with transcript turns, newest activity first bounded."""

        cutoff = (datetime.now(UTC) - timedelta(days=active_within_days)).isoformat()
        rows = await self.read_conn.execute_fetchall(
            """
            SELECT s.session_id AS session_id, MAX(m.seq) AS max_seq
            FROM sessions s
            JOIN session_messages m ON m.session_id = s.session_id
            WHERE s.session_type = 'chat'
              AND s.origin_automation_id IS NULL
              AND s.archived_at IS NULL
              AND s.last_activity >= ?
              AND m.role IN ('user', 'assistant')
            GROUP BY s.session_id
            """,
            (cutoff,),
        )
        return [(row["session_id"], int(row["max_seq"])) for row in rows]

    async def list_transcript_messages_after(self, session_id: str, after_seq: int, limit: int) -> list[dict]:
        rows = await self.read_conn.execute_fetchall(
            """
            SELECT seq, role, COALESCE(search_text, '') AS text, message_id, created_at
            FROM session_messages
            WHERE session_id = ? AND seq > ? AND role IN ('user', 'assistant')
            ORDER BY seq ASC
            LIMIT ?
            """,
            (session_id, after_seq, limit),
        )
        return [dict(row) for row in rows]

    async def list_latest_transcript_messages(self, session_id: str, limit: int) -> list[dict]:
        rows = await self.read_conn.execute_fetchall(
            """
            SELECT seq, role, COALESCE(search_text, '') AS text, message_id, created_at
            FROM session_messages
            WHERE session_id = ? AND role IN ('user', 'assistant')
            ORDER BY seq DESC
            LIMIT ?
            """,
            (session_id, limit),
        )
        return [dict(row) for row in reversed(rows)]

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

    async def _save_session_snapshot_unlocked(
        self,
        state: SessionState,
        messages: list[dict],
        metadata: dict | None,
        *,
        expected_generation: int | None = None,
        connection: aiosqlite.Connection,
    ) -> tuple[list[dict], str]:
        serializable_messages = self._to_serializable_messages(messages)
        await self._ensure_session_public_ref(state, connection)

        # The list is shared with the agent's in-memory context, so stable
        # identity and timestamps survive later append-only saves.
        now = datetime.now(UTC).isoformat()
        self._stamp_messages(serializable_messages, now)
        meta = metadata or {}
        messages_json, metadata_json = await asyncio.to_thread(
            lambda: (_encode_json(serializable_messages), _encode_json(meta))
        )
        generation = state.context_generation if expected_generation is None else expected_generation
        cursor = await connection.execute(
            SQL_SAVE_SESSION,
            (
                state.session_id,
                state.public_ref,
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
                generation,
                len(serializable_messages),
            ),
        )
        if cursor.rowcount == 0:
            raise StaleSessionContextError(f"session {state.session_id} context was replaced while its run was active")
        await self._mirror_session_messages(state.session_id, serializable_messages, connection=connection)
        return serializable_messages, self._projection_etag(messages_json)

    async def save_session(self, state: SessionState, messages: list[dict], metadata: dict | None = None) -> None:
        lock = await self._session_write_lock(state.session_id)
        async with lock:
            async with self._session_context_transaction() as connection:
                _serializable, etag = await self._save_session_snapshot_unlocked(
                    state,
                    messages,
                    metadata,
                    connection=connection,
                )
            state.context_etag = etag

    async def _advance_context_generation_unlocked(
        self,
        state: SessionState,
        *,
        expected_generation: int,
        expected_projection_etag: str,
        expected_message_ids: list[str] | None = None,
        connection: aiosqlite.Connection,
    ) -> int | None:
        rows = await connection.execute_fetchall(
            "SELECT messages, context_generation FROM sessions WHERE session_id = ?",
            (state.session_id,),
        )
        if not rows:
            return None
        raw_messages = rows[0]["messages"]
        if (
            int(rows[0]["context_generation"]) != expected_generation
            or self._projection_etag(raw_messages) != expected_projection_etag
        ):
            return None
        try:
            current = json.loads(raw_messages) if raw_messages is not None else None
        except (TypeError, json.JSONDecodeError):
            current = None
        if (
            expected_message_ids is not None
            and isinstance(current, list)
            and self._message_id_sequence(current) != expected_message_ids
        ):
            return None

        next_generation = expected_generation + 1
        cursor = await connection.execute(
            """
            UPDATE sessions
            SET context_generation = ?
            WHERE session_id = ? AND context_generation = ? AND messages IS ?
            """,
            (next_generation, state.session_id, expected_generation, raw_messages),
        )
        return next_generation if cursor.rowcount == 1 else None

    async def rewind_session(
        self,
        state: SessionState,
        messages: list[dict],
        *,
        expected_message_ids: list[str],
        discard_message_ids: list[str],
        expected_generation: int | None = None,
        expected_projection_etag: str | None = None,
        metadata: dict | None = None,
    ) -> bool:
        """Atomically replace the active projection and delete reverted rows.

        Ordinary saves never rewrite or delete transcript rows. A user-requested
        rewind is the explicit exception. Deletion is by active message identity,
        not raw transcript sequence: compaction reorders retained-tail messages.
        """
        lock = await self._session_write_lock(state.session_id)
        async with lock:
            async with self._session_context_transaction() as connection:
                generation = state.context_generation if expected_generation is None else expected_generation
                etag = state.context_etag if expected_projection_etag is None else expected_projection_etag
                next_generation = (
                    await self._advance_context_generation_unlocked(
                        state,
                        expected_generation=generation,
                        expected_projection_etag=etag,
                        expected_message_ids=expected_message_ids,
                        connection=connection,
                    )
                    if etag is not None
                    else None
                )
                if next_generation is None:
                    return False

                serializable = self._to_serializable_messages(messages)
                self._stamp_messages(serializable, datetime.now(UTC).isoformat())
                replacement = self.renew_handoff_projection(
                    serializable,
                    messages_before=len(expected_message_ids),
                    reason="user_rewind",
                )
                replacement, replacement_etag = await self._save_session_snapshot_unlocked(
                    state,
                    replacement,
                    metadata,
                    expected_generation=next_generation,
                    connection=connection,
                )
                unique_ids = list(dict.fromkeys(discard_message_ids))
                if unique_ids:
                    await connection.executemany(
                        "DELETE FROM session_messages WHERE session_id = ? AND message_id = ?",
                        [(state.session_id, message_id) for message_id in unique_ids],
                    )
                await self._rebuild_session_turns(state.session_id, connection=connection)
            state.context_generation = next_generation
            state.context_etag = replacement_etag
            messages[:] = replacement
            return True

    async def clear_session_context(
        self,
        state: SessionState,
        *,
        expected_message_ids: list[str],
        expected_generation: int | None = None,
        expected_projection_etag: str | None = None,
        metadata: dict | None = None,
    ) -> bool:
        """Atomically clear the active projection and its transcript."""
        lock = await self._session_write_lock(state.session_id)
        async with lock:
            async with self._session_context_transaction() as connection:
                generation = state.context_generation if expected_generation is None else expected_generation
                etag = state.context_etag if expected_projection_etag is None else expected_projection_etag
                next_generation = (
                    await self._advance_context_generation_unlocked(
                        state,
                        expected_generation=generation,
                        expected_projection_etag=etag,
                        expected_message_ids=expected_message_ids,
                        connection=connection,
                    )
                    if etag is not None
                    else None
                )
                if next_generation is None:
                    return False

                _messages, cleared_etag = await self._save_session_snapshot_unlocked(
                    state,
                    [],
                    metadata,
                    expected_generation=next_generation,
                    connection=connection,
                )
                await connection.execute("DELETE FROM session_messages WHERE session_id = ?", (state.session_id,))
                await connection.execute("DELETE FROM session_turns WHERE session_id = ?", (state.session_id,))
                await connection.execute("DELETE FROM chat_compactions WHERE session_id = ?", (state.session_id,))
            state.context_generation = next_generation
            state.context_etag = cleared_etag
            return True

    async def create_session_if_absent(
        self,
        state: SessionState,
        messages: list[dict] | None = None,
        metadata: dict | None = None,
    ) -> tuple[SessionState, bool]:
        """Insert a new session without replacing a row created by a racing owner."""
        lock = await self._session_write_lock(state.session_id)
        async with lock:
            async with self._session_context_transaction() as connection:
                existing = await connection.execute_fetchall(SQL_LOAD_SESSION, (state.session_id,))
                if existing:
                    durable_state = self._session_state_from_row(existing[0])
                    durable_state.context_etag = self._projection_etag(existing[0]["messages"])
                    return durable_state, False
                serializable_messages = self._to_serializable_messages(messages or [])
                await self._ensure_session_public_ref(state, connection)
                now = datetime.now(UTC).isoformat()
                self._stamp_messages(serializable_messages, now)
                meta = metadata or {}
                messages_json, metadata_json = await asyncio.to_thread(
                    lambda: (_encode_json(serializable_messages), _encode_json(meta))
                )
                cursor = await connection.execute(
                    SQL_INSERT_SESSION_IF_ABSENT,
                    (
                        state.session_id,
                        state.public_ref,
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
                        state.context_generation,
                        len(serializable_messages),
                    ),
                )
                created = cursor.rowcount > 0
                if not created:
                    raise RuntimeError("session insert lost its exclusive transaction")
                await self._mirror_session_messages(
                    state.session_id,
                    serializable_messages,
                    connection=connection,
                )
            state.context_etag = self._projection_etag(messages_json)
            return state, True

    @staticmethod
    def _session_state_from_row(row: aiosqlite.Row) -> SessionState:
        session_id = row["session_id"]
        try:
            started_at = datetime.fromisoformat(row["started_at"])
            last_activity = datetime.fromisoformat(row["last_activity"])
        except (TypeError, ValueError) as exc:
            raise SessionDataCorruptionError(f"session {session_id} has invalid timestamps") from exc
        if started_at.tzinfo is None or last_activity.tzinfo is None:
            raise SessionDataCorruptionError(f"session {session_id} has timezone-naive timestamps")

        session_type = row["session_type"]
        if session_type not in {"chat", "channel", "agent"}:
            raise SessionDataCorruptionError(f"session {session_id} has invalid session_type")
        context_generation = row["context_generation"]
        if isinstance(context_generation, bool) or not isinstance(context_generation, int) or context_generation < 0:
            raise SessionDataCorruptionError(f"session {session_id} has invalid context_generation")

        return SessionState(
            session_id=session_id,
            started_at=started_at,
            public_ref=row["public_ref"],
            last_activity=last_activity,
            name=row["name"],
            session_type=session_type,
            origin_automation_id=row["origin_automation_id"],
            parent_session_id=row["parent_session_id"],
            parent_tool_call_id=row["parent_tool_call_id"],
            agent_type=row["agent_type"],
            agent_status=row["agent_status"],
            area_id=row["area_id"],
            chat_model=row["chat_model"],
            context_generation=context_generation,
        )

    @staticmethod
    def _session_metadata(session_id: str, raw_metadata: str | None) -> dict:
        if not isinstance(raw_metadata, str):
            raise SessionDataCorruptionError(f"session {session_id} metadata is missing")
        try:
            parsed = json.loads(raw_metadata, parse_constant=_reject_nonfinite_json_constant)
        except ValueError as exc:
            raise SessionDataCorruptionError(f"session {session_id} metadata is invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise SessionDataCorruptionError(f"session {session_id} metadata must be an object")
        return parsed

    @staticmethod
    def _optional_nonnegative_metadata_int(session_id: str, metadata: dict, field: str) -> int | None:
        value = metadata.get(field)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise SessionDataCorruptionError(f"session {session_id} metadata.{field} must be a nonnegative integer")
        return value

    @staticmethod
    def _active_message_count(session_id: str, value: object) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise SessionDataCorruptionError(f"session {session_id} has invalid active_message_count")
        return value

    async def load_session_history_header(self, session_id: str) -> SessionHistoryHeader | None:
        """Load only fields needed to render one paged history response."""
        rows = await self.read_conn.execute_fetchall(SQL_LOAD_SESSION_HISTORY_HEADER, (session_id,))
        if not rows:
            return None

        row = rows[0]
        state = self._session_state_from_row(row)
        metadata = self._session_metadata(session_id, row["metadata"])
        input_tokens = self._optional_nonnegative_metadata_int(session_id, metadata, "last_input_tokens")
        message_count = self._active_message_count(session_id, row["active_message_count"])

        return SessionHistoryHeader(
            state=state,
            last_input_tokens=input_tokens,
            active_message_count=message_count,
        )

    async def load_session(self, session_id: str) -> SessionData | None:
        rows = await self.read_conn.execute_fetchall(SQL_LOAD_SESSION, (session_id,))
        if not rows:
            return None

        row = rows[0]
        state = self._session_state_from_row(row)

        raw_messages, raw_metadata = row["messages"], row["metadata"]
        context_etag = self._projection_etag(raw_messages)
        state.context_etag = context_etag

        def _parse_session_projection() -> tuple[list[dict], bool, dict]:
            try:
                messages = self._strict_active_projection(session_id, raw_messages)
                projection_valid = True
            except SessionDataCorruptionError:
                messages = []
                projection_valid = False
            metadata = self._session_metadata(session_id, raw_metadata)
            return messages, projection_valid, metadata

        messages, projection_valid, metadata = await asyncio.to_thread(_parse_session_projection)
        if not projection_valid:
            transcript_rows = await self.read_conn.execute_fetchall(
                """
                SELECT seq, message_id, role, message_json, created_at
                FROM session_messages
                WHERE session_id = ?
                ORDER BY seq ASC
                """,
                (session_id,),
            )
            messages = self._active_context_from_transcript_rows(session_id, transcript_rows)
        message_count = self._active_message_count(session_id, row["active_message_count"])
        if message_count != len(messages):
            raise SessionDataCorruptionError(
                f"session {session_id} active_message_count does not match active messages"
            )
        await self.events.restore_active_tool_result_files(session_id, messages)
        input_tokens = self._optional_nonnegative_metadata_int(session_id, metadata, "last_input_tokens")
        return SessionData(
            state=state,
            messages=messages,
            last_input_tokens=input_tokens,
            context_generation=state.context_generation,
            context_etag=context_etag,
        )

    async def get_latest_id(self) -> str | None:
        rows = await self.read_conn.execute_fetchall(SQL_GET_LATEST)
        return rows[0]["session_id"] if rows else None

    async def _ensure_session_public_ref(
        self,
        state: SessionState,
        connection: aiosqlite.Connection,
    ) -> None:
        rows = await connection.execute_fetchall(
            "SELECT public_ref FROM sessions WHERE session_id = ?",
            (state.session_id,),
        )
        if rows:
            stored_ref = rows[0]["public_ref"]
            if not isinstance(stored_ref, str) or not is_public_ref(stored_ref):
                raise ValueError("stored session public_ref is invalid")
            if state.public_ref is not None and state.public_ref != stored_ref:
                raise ValueError("session public_ref is immutable")
            state.public_ref = stored_ref
            return
        if state.public_ref is None:
            title, kind = self._session_ref_text(state.name, state.session_type)
            state.public_ref = public_ref(title, state.session_id, empty_slug=kind)
        if not is_public_ref(state.public_ref):
            raise ValueError("session public_ref is invalid")

    async def get_session_id_by_ref(
        self,
        session_ref: str,
        *,
        area_id: str | object | None = AREA_FILTER_UNSET,
    ) -> str | None:
        if not is_public_ref(session_ref):
            return None
        if area_id is AREA_FILTER_UNSET:
            query = "SELECT session_id FROM sessions WHERE public_ref = ?"
            params: tuple[object, ...] = (session_ref,)
        elif area_id is None:
            query = "SELECT session_id FROM sessions WHERE public_ref = ? AND area_id IS NULL"
            params = (session_ref,)
        else:
            query = "SELECT session_id FROM sessions WHERE public_ref = ? AND area_id = ?"
            params = (session_ref, area_id)
        rows = await self.read_conn.execute_fetchall(query, params)
        return str(rows[0]["session_id"]) if rows else None

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
                       public_ref,
                       session_type, origin_automation_id, parent_session_id, parent_tool_call_id,
                       agent_type, agent_status, area_id, chat_model,
                       active_message_count AS message_count
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
                       public_ref,
                       session_type, origin_automation_id, parent_session_id, parent_tool_call_id,
                       agent_type, agent_status, area_id, chat_model,
                       active_message_count AS message_count
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
                "public_ref": row["public_ref"],
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
                "storage_state": dict(row).get("storage_state") or "hot",
                "cold_bundle_bytes": int(dict(row).get("cold_bundle_bytes") or 0),
            }
            for row in rows
        ]

    async def update_session_name(self, session_id: str, name: str) -> bool:
        return await self._update(SQL_UPDATE_NAME, (name, session_id))

    async def update_session_area(self, session_id: str, area_id: str | None) -> bool:
        return await self._update(SQL_UPDATE_SESSION_AREA, (area_id, session_id))

    async def update_session_chat_model(self, session_id: str, chat_model: str | None) -> bool:
        return await self._update(SQL_UPDATE_SESSION_CHAT_MODEL, (chat_model, session_id))

    async def touch_session_access(self, session_id: str) -> bool:
        return await self._update(
            "UPDATE sessions SET last_accessed_at = ? WHERE session_id = ? AND archived_at IS NULL",
            (datetime.now(UTC).isoformat(), session_id),
        )

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
                "public_ref": row["public_ref"],
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
                "storage_state": dict(row).get("storage_state") or "hot",
                "cold_bundle_bytes": int(dict(row).get("cold_bundle_bytes") or 0),
            }
            for row in rows
        ]

    async def permanently_delete_session(self, session_id: str) -> bool:
        return await self.storage.permanently_delete_session(session_id)

    async def cold_convert_session(
        self,
        session_id: str,
        *,
        cold_root: Path,
    ) -> ColdBundleManifest | None:
        return await self.storage.cold_convert_session(session_id, cold_root=cold_root)

    async def rehydrate_cold_session(self, session_id: str, *, blob_root: Path) -> bool:
        return await self.storage.rehydrate_cold_session(session_id, blob_root=blob_root)

    async def purge_session(self, session_id: str, *, require_archived: bool) -> bool:
        return await self.storage.purge_session(session_id, require_archived=require_archived)

    async def list_storage_cleanup_candidates(self) -> list[StorageSessionCandidate]:
        return await self.storage.list_storage_cleanup_candidates()

    async def database_reclaim_status(self) -> dict[str, int | str]:
        return await self.storage.database_reclaim_status()

    async def incremental_vacuum(self, *, pages: int = 2048) -> int:
        return await self.storage.incremental_vacuum(pages=pages)

    async def record_storage_cleanup_run(
        self,
        *,
        plan_id: str,
        started_at: str,
        before_bytes: int,
        target_bytes: int,
        after_bytes: int,
        reclaimed_bytes: int,
        actions: list[dict[str, Any]],
        status: str,
        error: str | None = None,
    ) -> str:
        return await self.storage.record_storage_cleanup_run(
            plan_id=plan_id,
            started_at=started_at,
            before_bytes=before_bytes,
            target_bytes=target_bytes,
            after_bytes=after_bytes,
            reclaimed_bytes=reclaimed_bytes,
            actions=actions,
            status=status,
            error=error,
        )

    async def latest_storage_cleanup_run(self) -> dict[str, Any] | None:
        return await self.storage.latest_storage_cleanup_run()

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
        return await self.transcripts.list_session_messages(
            session_id,
            limit=limit,
            before=before,
            after=after,
            around=around,
            around_seq=around_seq,
            before_seq=before_seq,
            after_seq=after_seq,
            area_id=area_id,
        )

    async def messages_since(self, session_id: str, seq: int) -> list[dict]:
        return await self.transcripts.messages_since(session_id, seq)

    async def recent_session_scopes(self, limit: int) -> list[dict]:
        return await self.transcripts.recent_session_scopes(limit)

    async def session_scope(self, session_id: str) -> dict | None:
        return await self.transcripts.session_scope(session_id)

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
        return await self.transcripts.search_messages(
            query,
            limit=limit,
            offset=offset,
            session_id=session_id,
            since=since,
            until=until,
            area_id=area_id,
        )

    async def list_session_turns(self, session_id: str, limit: int = 100) -> list[dict]:
        return await self.transcripts.list_session_turns(session_id, limit)
