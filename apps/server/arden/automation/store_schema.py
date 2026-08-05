import sqlite3

import aiosqlite

from arden.automation.models import create_automation_ref
from arden.logging import get_logger

CURRENT_SCHEMA_VERSION = 17

_logger = get_logger(__name__)

_SCHEMA = """
CREATE TABLE scheduled_tasks (
    task_id TEXT PRIMARY KEY,
    automation_ref TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    description TEXT,
    description_source TEXT CHECK (description_source IN ('manual', 'generated') OR description_source IS NULL),
    prompt TEXT NOT NULL,
    model TEXT,
    triggers TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_run_at TEXT,
    next_run_at TEXT,
    notifiers TEXT,
    last_result TEXT,
    running_since TEXT,
    auto_approve INTEGER NOT NULL DEFAULT 0,
    handler TEXT,
    builtin INTEGER NOT NULL DEFAULT 0,
    cooldown_minutes INTEGER,
    kind TEXT NOT NULL DEFAULT 'automation',
    max_iterations INTEGER,
    iteration_count INTEGER NOT NULL DEFAULT 0,
    stop_when TEXT,
    max_age_days INTEGER,
    thread_id TEXT,
    read_history INTEGER NOT NULL DEFAULT 0,
    parent_automation_id TEXT,
    idempotency_key TEXT,
    idempotency_scope TEXT,
    tool_scope TEXT,
    triggers_source TEXT
);

CREATE INDEX idx_scheduled_tasks_next_run ON scheduled_tasks(next_run_at);
CREATE INDEX idx_scheduled_tasks_enabled ON scheduled_tasks(enabled);
CREATE INDEX idx_scheduled_tasks_parent ON scheduled_tasks(parent_automation_id);
CREATE INDEX idx_scheduled_tasks_thread_kind ON scheduled_tasks(thread_id, kind);
CREATE INDEX idx_scheduled_tasks_kind_thread ON scheduled_tasks(kind, thread_id);
CREATE INDEX idx_scheduled_tasks_idempotency
ON scheduled_tasks(idempotency_scope, idempotency_key)
WHERE idempotency_key IS NOT NULL;
CREATE UNIQUE INDEX idx_scheduled_tasks_automation_ref ON scheduled_tasks(automation_ref);

CREATE TABLE automation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    automation_ref TEXT NOT NULL,
    chat_run_id TEXT,
    chat_session_id TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    result TEXT,
    error TEXT
);
CREATE INDEX idx_automation_runs_task ON automation_runs(task_id, started_at);
CREATE INDEX idx_automation_runs_ref ON automation_runs(automation_ref, started_at);

CREATE TABLE automation_event_dedupe (
    task_id TEXT NOT NULL,
    event_key TEXT NOT NULL,
    seen_at TEXT NOT NULL,
    PRIMARY KEY (task_id, event_key)
);
CREATE INDEX idx_automation_event_dedupe_seen_at ON automation_event_dedupe(seen_at);

CREATE TABLE automation_event_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    event_key TEXT NOT NULL,
    context TEXT NOT NULL,
    created_at TEXT NOT NULL,
    claimed_at TEXT,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    next_attempt_at TEXT
);
CREATE INDEX idx_automation_event_queue_task_claimed_id
ON automation_event_queue(task_id, claimed_at, id);

CREATE TABLE automation_event_dead_letter (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_queue_id INTEGER NOT NULL,
    task_id TEXT NOT NULL,
    event_key TEXT NOT NULL,
    context TEXT NOT NULL,
    created_at TEXT NOT NULL,
    failed_at TEXT NOT NULL,
    attempt_count INTEGER NOT NULL,
    last_error TEXT NOT NULL
);
CREATE INDEX idx_automation_event_dead_letter_task_failed
ON automation_event_dead_letter(task_id, failed_at);

CREATE TABLE automation_count_state (
    task_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (task_id, session_id)
);
CREATE INDEX idx_automation_count_state_updated_at ON automation_count_state(updated_at);

CREATE TABLE automation_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE automation_idempotency_claims (
    claim_id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    key TEXT NOT NULL,
    parent_automation_id TEXT,
    parent_fire_at TEXT,
    attempt_n INTEGER,
    claimed_at TEXT NOT NULL,
    automation_task_id TEXT
);
CREATE INDEX idx_automation_idempotency_claims_parent
ON automation_idempotency_claims(parent_automation_id);
"""

_V17_COLUMNS = {
    "scheduled_tasks": (
        "task_id",
        "automation_ref",
        "name",
        "description",
        "description_source",
        "prompt",
        "model",
        "triggers",
        "enabled",
        "created_at",
        "last_run_at",
        "next_run_at",
        "notifiers",
        "last_result",
        "running_since",
        "auto_approve",
        "handler",
        "builtin",
        "cooldown_minutes",
        "kind",
        "max_iterations",
        "iteration_count",
        "stop_when",
        "max_age_days",
        "thread_id",
        "read_history",
        "parent_automation_id",
        "idempotency_key",
        "idempotency_scope",
        "tool_scope",
        "triggers_source",
    ),
    "automation_runs": (
        "id",
        "task_id",
        "automation_ref",
        "chat_run_id",
        "chat_session_id",
        "started_at",
        "ended_at",
        "status",
        "result",
        "error",
    ),
    "automation_event_dedupe": ("task_id", "event_key", "seen_at"),
    "automation_event_queue": (
        "id",
        "task_id",
        "event_key",
        "context",
        "created_at",
        "claimed_at",
        "attempt_count",
        "last_error",
        "next_attempt_at",
    ),
    "automation_event_dead_letter": (
        "id",
        "original_queue_id",
        "task_id",
        "event_key",
        "context",
        "created_at",
        "failed_at",
        "attempt_count",
        "last_error",
    ),
    "automation_count_state": ("task_id", "session_id", "count", "updated_at"),
    "automation_meta": ("key", "value"),
    "automation_idempotency_claims": (
        "claim_id",
        "scope",
        "key",
        "parent_automation_id",
        "parent_fire_at",
        "attempt_n",
        "claimed_at",
        "automation_task_id",
    ),
}

_V16_COLUMNS = {
    table: tuple(column for column in columns if column != "automation_ref") for table, columns in _V17_COLUMNS.items()
}

_REQUIRED_INDEXES_V17 = frozenset(
    {
        "idx_scheduled_tasks_next_run",
        "idx_scheduled_tasks_enabled",
        "idx_scheduled_tasks_parent",
        "idx_scheduled_tasks_thread_kind",
        "idx_scheduled_tasks_kind_thread",
        "idx_scheduled_tasks_idempotency",
        "idx_scheduled_tasks_automation_ref",
        "idx_automation_runs_task",
        "idx_automation_runs_ref",
        "idx_automation_event_dedupe_seen_at",
        "idx_automation_event_queue_task_claimed_id",
        "idx_automation_event_dead_letter_task_failed",
        "idx_automation_count_state_updated_at",
        "idx_automation_idempotency_claims_parent",
    }
)


async def initialize(conn: aiosqlite.Connection) -> None:
    tables = {row["name"] for row in await conn.execute_fetchall("SELECT name FROM sqlite_master WHERE type = 'table'")}
    required_tables = frozenset(_V17_COLUMNS)
    owned_tables = tables.intersection(required_tables)
    if not owned_tables:
        await _create_fresh(conn)
        return
    if owned_tables != required_tables:
        missing = ", ".join(sorted(required_tables - owned_tables))
        raise RuntimeError(f"automation schema is incomplete; missing tables: {missing}")

    version = await _schema_version(conn)
    if version == CURRENT_SCHEMA_VERSION:
        await _validate_shape(conn, _V17_COLUMNS, _REQUIRED_INDEXES_V17)
        return
    if version != 16:
        if version < 16:
            raise RuntimeError(f"automation schema v{version} is unsupported; restore a v16 database")
        raise RuntimeError(f"automation schema v{version} is newer than supported v{CURRENT_SCHEMA_VERSION}")
    await _upgrade_v16(conn)


async def _create_fresh(conn: aiosqlite.Connection) -> None:
    script = (
        "BEGIN IMMEDIATE;\n"
        + _SCHEMA
        + f"\nINSERT INTO automation_meta (key, value) VALUES ('schema_version', '{CURRENT_SCHEMA_VERSION}');\n"
        + "COMMIT;"
    )
    try:
        await conn.executescript(script)
    except BaseException:
        await conn.rollback()
        raise


async def _schema_version(conn: aiosqlite.Connection) -> int:
    rows = await conn.execute_fetchall("SELECT value FROM automation_meta WHERE key = 'schema_version'")
    if not rows:
        raise RuntimeError("automation schema version is missing; restore a v16 or v17 database")
    try:
        return int(rows[0]["value"])
    except (TypeError, ValueError) as exc:
        raise RuntimeError("automation schema version is invalid") from exc


async def _validate_shape(
    conn: aiosqlite.Connection,
    expected_columns: dict[str, tuple[str, ...]],
    required_indexes: frozenset[str] | None = None,
) -> None:
    for table, expected in expected_columns.items():
        actual = frozenset(row["name"] for row in await conn.execute_fetchall(f"PRAGMA table_info({table})"))
        if actual != frozenset(expected):
            raise RuntimeError(f"automation schema has unexpected columns in {table}: {sorted(actual)!r}")
    if required_indexes is None:
        return
    indexes = {
        row["name"] for row in await conn.execute_fetchall("SELECT name FROM sqlite_master WHERE type = 'index'")
    }
    missing = required_indexes - indexes
    if missing:
        raise RuntimeError(f"automation schema is missing indexes: {', '.join(sorted(missing))}")


async def _upgrade_v16(conn: aiosqlite.Connection) -> None:
    task_columns = {row["name"] for row in await conn.execute_fetchall("PRAGMA table_info(scheduled_tasks)")}
    run_columns = {row["name"] for row in await conn.execute_fetchall("PRAGMA table_info(automation_runs)")}
    if "automation_ref" in task_columns or "automation_ref" in run_columns:
        raise RuntimeError("automation schema v16 has a partial public-ref migration")
    await _validate_shape(conn, _V16_COLUMNS)

    try:
        await conn.execute("BEGIN IMMEDIATE")
        await _add_public_refs(conn)
        await conn.execute(
            "INSERT OR REPLACE INTO automation_meta (key, value) VALUES ('schema_version', ?)",
            (str(CURRENT_SCHEMA_VERSION),),
        )
        await _validate_shape(conn, _V17_COLUMNS, _REQUIRED_INDEXES_V17)
        await conn.commit()
    except sqlite3.OperationalError as exc:
        await conn.rollback()
        raise RuntimeError("automation schema v16 is incomplete") from exc
    except BaseException:
        await conn.rollback()
        raise
    _logger.info("Migrated automation store to v17 (stable model-facing refs)")


async def _add_public_refs(conn: aiosqlite.Connection) -> None:
    task_rows = await conn.execute_fetchall("SELECT task_id, name FROM scheduled_tasks")
    names_by_task = {row["task_id"]: row["name"] for row in task_rows}
    run_task_rows = await conn.execute_fetchall("SELECT DISTINCT task_id FROM automation_runs")
    for row in run_task_rows:
        names_by_task.setdefault(row["task_id"], "Deleted automation")

    refs_by_task: dict[str, str] = {}
    tasks_by_ref: dict[str, str] = {}
    for task_id, name in names_by_task.items():
        try:
            automation_ref = create_automation_ref(name, task_id)
        except ValueError as error:
            raise RuntimeError(f"automation {task_id!r} cannot receive a public ref") from error
        if other_task := tasks_by_ref.get(automation_ref):
            raise RuntimeError(
                f"automation public-ref collision between {other_task!r} and {task_id!r}: {automation_ref}"
            )
        refs_by_task[task_id] = automation_ref
        tasks_by_ref[automation_ref] = task_id

    await conn.execute("ALTER TABLE scheduled_tasks ADD COLUMN automation_ref TEXT")
    await conn.execute("ALTER TABLE automation_runs ADD COLUMN automation_ref TEXT")
    for task_id, automation_ref in refs_by_task.items():
        await conn.execute(
            "UPDATE scheduled_tasks SET automation_ref = ? WHERE task_id = ?",
            (automation_ref, task_id),
        )
        await conn.execute(
            "UPDATE automation_runs SET automation_ref = ? WHERE task_id = ?",
            (automation_ref, task_id),
        )
    missing_task_refs = await conn.execute_fetchall(
        "SELECT task_id FROM scheduled_tasks WHERE automation_ref IS NULL OR automation_ref = '' LIMIT 1"
    )
    missing_run_refs = await conn.execute_fetchall(
        "SELECT id FROM automation_runs WHERE automation_ref IS NULL OR automation_ref = '' LIMIT 1"
    )
    if missing_task_refs or missing_run_refs:
        raise RuntimeError("automation public-ref migration left an unaddressable row")
    await _create_v17_indexes(conn)


async def _create_v17_indexes(conn: aiosqlite.Connection) -> None:
    await conn.execute("CREATE UNIQUE INDEX idx_scheduled_tasks_automation_ref ON scheduled_tasks(automation_ref)")
    await conn.execute("CREATE INDEX idx_automation_runs_ref ON automation_runs(automation_ref, started_at)")
