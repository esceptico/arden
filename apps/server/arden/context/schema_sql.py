"""Canonical SQLite schema for a fresh v9 context database."""

import re
from dataclasses import dataclass

CANONICAL_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS session_store_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS storage_cleanup_runs (
    run_id TEXT PRIMARY KEY,
    plan_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    before_bytes INTEGER NOT NULL,
    target_bytes INTEGER NOT NULL,
    after_bytes INTEGER NOT NULL,
    reclaimed_bytes INTEGER NOT NULL,
    actions_json TEXT NOT NULL,
    status TEXT NOT NULL,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_storage_cleanup_runs_completed
    ON storage_cleanup_runs(completed_at DESC);

CREATE TABLE IF NOT EXISTS areas (
    area_id TEXT PRIMARY KEY,
    area_ref TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    name_key TEXT NOT NULL,
    default_cwds TEXT NOT NULL DEFAULT '[]',
    instructions TEXT,
    page_path TEXT,
    page_id TEXT,
    autonomy TEXT,
    attention TEXT NOT NULL DEFAULT 'ambient',
    interrupts TEXT NOT NULL DEFAULT 'asks',
    paused_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_areas_archived_updated
    ON areas(archived_at, updated_at DESC);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    public_ref TEXT NOT NULL UNIQUE,
    started_at TEXT NOT NULL,
    last_activity TEXT NOT NULL,
    last_accessed_at TEXT,
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
    chat_model TEXT,
    context_generation INTEGER NOT NULL DEFAULT 0,
    active_message_count INTEGER NOT NULL DEFAULT 0,
    storage_state TEXT NOT NULL DEFAULT 'hot' CHECK(storage_state IN ('hot', 'cold')),
    cold_bundle_path TEXT,
    cold_bundle_sha256 TEXT,
    cold_bundle_bytes INTEGER,
    cold_logical_bytes INTEGER,
    cold_message_count INTEGER,
    cold_prose_sha256 TEXT,
    cold_blob_hashes_json TEXT
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
CREATE INDEX IF NOT EXISTS idx_tool_results_expires
    ON tool_results(expires_at)
    WHERE expires_at IS NOT NULL;

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

CREATE TABLE IF NOT EXISTS session_event_retention_state (
    session_id TEXT PRIMARY KEY,
    writes_since_prune INTEGER NOT NULL DEFAULT 0 CHECK(writes_since_prune >= 0)
);

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
    agent_ref TEXT NOT NULL,
    session_id TEXT NOT NULL,
    parent_run_id TEXT,
    parent_tool_call_id TEXT,
    suspension_id TEXT,
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
    cancel_actor TEXT,
    terminal_cause TEXT,
    cancel_generation INTEGER NOT NULL DEFAULT 0,
    cancel_idempotency_key TEXT,
    notified_at TEXT,
    completion_id TEXT,
    spawn_spec TEXT,
    spawn_attempts INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (session_id, task_id)
);

CREATE INDEX IF NOT EXISTS idx_background_agent_runs_session_status
    ON background_agent_runs(session_id, status);

CREATE TABLE IF NOT EXISTS execution_cancellation_scopes (
    session_id TEXT PRIMARY KEY,
    actor TEXT NOT NULL,
    cause TEXT NOT NULL,
    generation INTEGER NOT NULL,
    idempotency_key TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

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
CREATE UNIQUE INDEX IF NOT EXISTS idx_background_agent_events_event_id
    ON background_agent_events(event_id) WHERE event_id IS NOT NULL;

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
CREATE UNIQUE INDEX idx_areas_active_name_key
    ON areas(name_key) WHERE archived_at IS NULL;
CREATE UNIQUE INDEX idx_areas_page_path
    ON areas(page_path) WHERE page_path IS NOT NULL;
CREATE UNIQUE INDEX idx_areas_page_id
    ON areas(page_id) WHERE page_id IS NOT NULL;
CREATE INDEX idx_sessions_area_activity ON sessions(area_id, last_activity DESC);
CREATE INDEX idx_sessions_parent_activity ON sessions(parent_session_id, started_at);
CREATE UNIQUE INDEX idx_background_agent_runs_session_agent_ref
    ON background_agent_runs(session_id, agent_ref);

CREATE VIRTUAL TABLE session_messages_fts USING fts5(
    search_text,
    content='session_messages',
    content_rowid='rowid'
);

CREATE TRIGGER session_messages_ai
AFTER INSERT ON session_messages BEGIN
    INSERT INTO session_messages_fts(rowid, search_text)
    VALUES (new.rowid, new.search_text);
END;

CREATE TRIGGER session_messages_ad
AFTER DELETE ON session_messages BEGIN
    INSERT INTO session_messages_fts(session_messages_fts, rowid, search_text)
    VALUES ('delete', old.rowid, old.search_text);
END;

CREATE TRIGGER session_messages_au
AFTER UPDATE ON session_messages BEGIN
    INSERT INTO session_messages_fts(session_messages_fts, rowid, search_text)
    VALUES ('delete', old.rowid, old.search_text);
    INSERT INTO session_messages_fts(rowid, search_text)
    VALUES (new.rowid, new.search_text);
END;

INSERT INTO session_store_meta(key, value) VALUES ('schema_version', '9');
"""


@dataclass(frozen=True, slots=True)
class CanonicalSqlObject:
    kind: str
    name: str
    table: str
    sql: str


def normalize_schema_sql(sql: str) -> str:
    """Normalize formatting while preserving every SQLite schema choice."""

    value = re.sub(r"\bIF\s+NOT\s+EXISTS\s+", "", sql, flags=re.IGNORECASE)
    value = value.strip().removesuffix(";")
    value = re.sub(r"\s+", " ", value)
    return re.sub(r"\s*([(),])\s*", r"\1", value)


def _canonical_sql_objects() -> tuple[CanonicalSqlObject, ...]:
    starts = list(re.finditer(r"(?m)^CREATE\s+", CANONICAL_SCHEMA_SQL))
    objects: list[CanonicalSqlObject] = []
    for index, start in enumerate(starts):
        end = starts[index + 1].start() if index + 1 < len(starts) else CANONICAL_SCHEMA_SQL.index(
            "INSERT INTO session_store_meta",
            start.start(),
        )
        sql = CANONICAL_SCHEMA_SQL[start.start() : end].strip()
        header = re.match(
            r"CREATE\s+(?:(UNIQUE)\s+)?(?:(VIRTUAL)\s+)?(TABLE|INDEX|TRIGGER)"
            r"(?:\s+IF\s+NOT\s+EXISTS)?\s+([a-zA-Z0-9_]+)",
            sql,
            flags=re.IGNORECASE,
        )
        if header is None:
            raise RuntimeError(f"invalid canonical schema statement: {sql[:80]}")
        object_type = header.group(3).lower()
        name = header.group(4)
        if object_type == "table":
            table = name
        else:
            table_match = re.search(r"\bON\s+([a-zA-Z0-9_]+)", sql, flags=re.IGNORECASE)
            if table_match is None:
                raise RuntimeError(f"canonical {object_type} {name} has no owner table")
            table = table_match.group(1)
        objects.append(
            CanonicalSqlObject(
                kind=object_type,
                name=name,
                table=table,
                sql=sql,
            )
        )
    return tuple(objects)


CANONICAL_SQL_OBJECTS = _canonical_sql_objects()
CANONICAL_TABLE_SQL = {item.name: item.sql for item in CANONICAL_SQL_OBJECTS if item.kind == "table"}
CANONICAL_INDEX_SQL = {item.name: item.sql for item in CANONICAL_SQL_OBJECTS if item.kind == "index"}
