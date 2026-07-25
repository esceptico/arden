import json
from datetime import UTC, datetime

import aiosqlite

from arden.automation.models import Automation, IdempotencyClaim
from arden.automation.suggestions import AutomationSuggestion
from arden.automation.triggers import parse_triggers
from arden.logging import get_logger

_logger = get_logger(__name__)


def _parse_dt(raw: str | None) -> datetime | None:
    return datetime.fromisoformat(raw) if raw else None


_CONTROL_BYTES = ("\x1f", "\x00")


def _reject_control_bytes(name: str, value: str | None) -> None:
    if value is None:
        return
    if any(b in value for b in _CONTROL_BYTES):
        raise ValueError("idempotency claim fields must not contain control bytes \\x1f or \\x00")


def _validate_idempotency_claim(
    *,
    scope: str,
    key: str,
    parent_automation_id: str | None,
    parent_fire_at: str | None,
    attempt_n: int | None,
    automation_task_id: str | None = None,
) -> None:
    if scope == "global":
        if parent_automation_id is not None or parent_fire_at is not None or attempt_n is not None:
            raise ValueError("scope='global' must not set parent_automation_id, parent_fire_at, or attempt_n")
    elif scope == "run":
        if parent_automation_id is None or parent_fire_at is None:
            raise ValueError("scope='run' requires parent_automation_id and parent_fire_at")
        if attempt_n is not None:
            raise ValueError("scope='run' must not set attempt_n")
    elif scope == "attempt":
        if parent_automation_id is None or parent_fire_at is None or attempt_n is None:
            raise ValueError("scope='attempt' requires parent_automation_id, parent_fire_at, and attempt_n")
    else:
        raise ValueError(f"Unknown idempotency scope: {scope!r}")

    # PK is built by joining these fields with \x1f and using \x00 as NULL
    # sentinel. Any of those bytes in user input would let two distinct tuples
    # collide on the same PK, so we reject them outright.
    _reject_control_bytes("key", key)
    _reject_control_bytes("parent_automation_id", parent_automation_id)
    _reject_control_bytes("parent_fire_at", parent_fire_at)
    _reject_control_bytes("automation_task_id", automation_task_id)


def _build_claim_id(
    *,
    scope: str,
    key: str,
    parent_automation_id: str | None,
    parent_fire_at: str | None,
    attempt_n: int | None,
) -> str:
    # Deterministic PK string. \x1f (unit separator) won't appear in keys/ids,
    # and the sentinel \x00 marks "this field is NULL" so we don't collide
    # with literal strings.
    parts = [
        scope,
        key,
        parent_automation_id if parent_automation_id is not None else "\x00",
        parent_fire_at if parent_fire_at is not None else "\x00",
        str(attempt_n) if attempt_n is not None else "\x00",
    ]
    return "\x1f".join(parts)


def _row_to_automation(row: dict) -> Automation:
    return Automation(
        task_id=row["task_id"],
        name=row["name"],
        description=row["description"],
        description_source=row["description_source"],
        prompt=row["prompt"],
        model=row["model"],
        triggers=parse_triggers(row["triggers"]),
        enabled=bool(row["enabled"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        next_run_at=_parse_dt(row["next_run_at"]),
        last_run_at=_parse_dt(row["last_run_at"]),
        last_result=row["last_result"],
        running_since=_parse_dt(row["running_since"]),
        auto_approve=bool(row["auto_approve"]),
        handler=row["handler"],
        builtin=bool(row["builtin"]),
        cooldown_minutes=int(row["cooldown_minutes"]) if row["cooldown_minutes"] is not None else None,
        kind=row["kind"] or "automation",
        max_iterations=int(row["max_iterations"]) if row["max_iterations"] is not None else None,
        iteration_count=int(row["iteration_count"] or 0),
        stop_when=row["stop_when"],
        max_age_days=int(row["max_age_days"]) if row["max_age_days"] is not None else None,
        thread_id=row["thread_id"],
        read_history=bool(row["read_history"]),
        parent_automation_id=row["parent_automation_id"],
        idempotency_key=row["idempotency_key"],
        idempotency_scope=row["idempotency_scope"],
        tool_scope=json.loads(row["tool_scope"]) if dict(row).get("tool_scope") else None,
        output_schema=dict(row).get("output_schema"),
    )


def _serialize_triggers(triggers: list) -> str:
    return json.dumps([{"type": t.type, **t.params()} for t in triggers])


def _row_to_suggestion(row: dict) -> AutomationSuggestion:
    return AutomationSuggestion(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        prompt=row["prompt"],
        triggers=parse_triggers(row["triggers"]),
        rationale=row["rationale"],
        category=row["category"],
        evidence=json.loads(row["evidence"]) if row["evidence"] else [],
        icon=row["icon"],
        status=row["status"],
        created_at=datetime.fromisoformat(row["created_at"]),
        source_automation_id=row["source_automation_id"],
    )


_SCHEMA = """
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    task_id TEXT PRIMARY KEY,
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
    output_schema TEXT
);

CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_next_run ON scheduled_tasks(next_run_at);
CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_enabled ON scheduled_tasks(enabled);

-- Per-run history so the UI can show "did it fire, and what did it do?"
-- (scheduled_tasks only keeps the LAST run). Self-contained columns, so it
-- lives in _SCHEMA (CREATE TABLE before its INDEX) — no migration needed.
CREATE TABLE IF NOT EXISTS automation_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',
    result TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_automation_runs_task ON automation_runs(task_id, started_at);
-- thread_id-based indexes (idx_scheduled_tasks_kind_thread, thread_kind,
-- parent, idempotency_*) are created inside the v5 migration block instead
-- of here, since they reference columns that don't exist on pre-v5
-- databases. Putting them in _SCHEMA would fail on the upgrade path
-- (CREATE INDEX runs before ALTER TABLE adds the columns).

CREATE TABLE IF NOT EXISTS automation_event_dedupe (
    task_id TEXT NOT NULL,
    event_key TEXT NOT NULL,
    seen_at TEXT NOT NULL,
    PRIMARY KEY (task_id, event_key)
);

CREATE INDEX IF NOT EXISTS idx_automation_event_dedupe_seen_at
ON automation_event_dedupe(seen_at);

CREATE TABLE IF NOT EXISTS automation_event_queue (
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

CREATE INDEX IF NOT EXISTS idx_automation_event_queue_task_claimed_id
ON automation_event_queue(task_id, claimed_at, id);

CREATE TABLE IF NOT EXISTS automation_event_dead_letter (
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

CREATE INDEX IF NOT EXISTS idx_automation_event_dead_letter_task_failed
ON automation_event_dead_letter(task_id, failed_at);

CREATE TABLE IF NOT EXISTS automation_count_state (
    task_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (task_id, session_id)
);

CREATE INDEX IF NOT EXISTS idx_automation_count_state_updated_at
ON automation_count_state(updated_at);

CREATE TABLE IF NOT EXISTS automation_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS automation_idempotency_claims (
    claim_id TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    key TEXT NOT NULL,
    parent_automation_id TEXT,
    parent_fire_at TEXT,
    attempt_n INTEGER,
    claimed_at TEXT NOT NULL,
    automation_task_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_automation_idempotency_claims_parent
ON automation_idempotency_claims(parent_automation_id);
"""

_COLUMNS = (
    "task_id, name, description, description_source, prompt, model, triggers, enabled, "
    "created_at, last_run_at, next_run_at, last_result, running_since, "
    "auto_approve, handler, builtin, cooldown_minutes, "
    "kind, max_iterations, iteration_count, stop_when, max_age_days, "
    "thread_id, read_history, parent_automation_id, idempotency_key, idempotency_scope, tool_scope, output_schema"
)

_SQL_SAVE = f"""
INSERT OR REPLACE INTO scheduled_tasks ({_COLUMNS})
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_SQL_GET_BY_ID = f"SELECT {_COLUMNS} FROM scheduled_tasks WHERE task_id = ?"

_SQL_LIST_ALL = f"SELECT {_COLUMNS} FROM scheduled_tasks ORDER BY created_at"

_SQL_LIST_RUNNING = f"""
SELECT {_COLUMNS} FROM scheduled_tasks
WHERE running_since IS NOT NULL
ORDER BY running_since
"""

_SQL_LIST_DUE = f"""
SELECT {_COLUMNS} FROM scheduled_tasks
WHERE enabled = 1 AND next_run_at <= ? AND running_since IS NULL
ORDER BY next_run_at
"""

_SQL_LIST_EVENT_TRIGGERED = f"""
SELECT {_COLUMNS} FROM scheduled_tasks
WHERE enabled = 1
  AND EXISTS (
    SELECT 1 FROM json_each(triggers)
    WHERE json_extract(value, '$.type') = 'event'
      AND json_extract(value, '$.event_type') = ?
  )
"""

_SQL_LIST_BY_TRIGGER_TYPE = f"""
SELECT {_COLUMNS} FROM scheduled_tasks
WHERE enabled = 1
  AND running_since IS NULL
  AND EXISTS (
    SELECT 1 FROM json_each(triggers)
    WHERE json_extract(value, '$.type') = ?
  )
"""

_SQL_LIST_MESSAGE_TRIGGERED = f"""
SELECT {_COLUMNS} FROM scheduled_tasks
WHERE enabled = 1
  AND EXISTS (
    SELECT 1 FROM json_each(triggers) AS t
    WHERE json_extract(t.value, '$.type') = 'message'
      AND json_extract(t.value, '$.source') = ?
      AND EXISTS (
        SELECT 1 FROM json_each(t.value, '$.channels') AS c
        WHERE json_extract(c.value, '$.id') = ?
      )
  )
"""

_SQL_LIST_WATCHED_SLACK_CHANNELS = """
SELECT DISTINCT json_extract(c.value, '$.id') AS channel_id
FROM scheduled_tasks, json_each(triggers) AS t, json_each(t.value, '$.channels') AS c
WHERE enabled = 1
  AND json_extract(t.value, '$.type') = 'message'
  AND json_extract(t.value, '$.source') = 'slack'
"""

_SQL_UPDATE_LAST_RUN = """
UPDATE scheduled_tasks
SET last_run_at = ?, next_run_at = ?, last_result = ?
WHERE task_id = ?
"""

_SQL_UPDATE_LAST_RUN_KEEP_RESULT = """
UPDATE scheduled_tasks
SET last_run_at = ?, next_run_at = ?
WHERE task_id = ?
"""

_SQL_SET_NEXT_RUN = """
UPDATE scheduled_tasks SET next_run_at = ? WHERE task_id = ?
"""

_SQL_SET_LAST_RESULT = """
UPDATE scheduled_tasks SET last_result = ? WHERE task_id = ?
"""

_SQL_TRY_MARK_RUNNING = """
UPDATE scheduled_tasks
SET running_since = ?
WHERE task_id = ?
  AND enabled = 1
  AND running_since IS NULL
"""

_SQL_CLEAR_RUNNING = "UPDATE scheduled_tasks SET running_since = NULL WHERE task_id = ?"

_SQL_INSERT_RUN = "INSERT INTO automation_runs (task_id, started_at, status) VALUES (?, ?, 'running')"
_SQL_FINISH_RUN = "UPDATE automation_runs SET ended_at = ?, status = ?, result = ?, error = ? WHERE id = ?"
_SQL_FINALIZE_LATEST_RUN = (
    "UPDATE automation_runs SET status = 'completed', result = ?, ended_at = ? "
    "WHERE id = (SELECT id FROM automation_runs WHERE task_id = ? ORDER BY id DESC LIMIT 1) "
    "AND status = 'running'"
)
_SQL_FAIL_ORPHANED_RUNS = (
    "UPDATE automation_runs SET status = 'failed', error = ?, ended_at = ? WHERE status = 'running'"
)
_SQL_FAIL_LATEST_RUNNING_RUN = (
    "UPDATE automation_runs SET status = 'failed', error = ?, ended_at = ? WHERE task_id = ? AND status = 'running'"
)
_SQL_LIST_RUNS = (
    "SELECT id, task_id, started_at, ended_at, status, result, error "
    "FROM automation_runs WHERE task_id = ? ORDER BY started_at DESC, id DESC LIMIT ?"
)
_SQL_RECENT_STATUSES = "SELECT status FROM automation_runs WHERE task_id = ? ORDER BY started_at DESC, id DESC LIMIT ?"
_SQL_LATEST_RUNS = (
    "SELECT id, task_id, started_at, ended_at, status, result, error "
    "FROM automation_runs WHERE id IN (SELECT max(id) FROM automation_runs GROUP BY task_id)"
)

_SQL_DELETE = "DELETE FROM scheduled_tasks WHERE task_id = ?"

_SQL_SET_ENABLED = "UPDATE scheduled_tasks SET enabled = ? WHERE task_id = ?"

_SQL_DISABLE_BY_PARENT = """
UPDATE scheduled_tasks SET enabled = 0
WHERE parent_automation_id = ? AND enabled = 1
"""

_SQL_SET_AUTO_APPROVE = "UPDATE scheduled_tasks SET auto_approve = ? WHERE task_id = ?"

_SQL_UPDATE_METADATA = """
UPDATE scheduled_tasks
SET name = ?, description = ?, description_source = ?, prompt = ?, model = ?, triggers = ?,
    enabled = ?, next_run_at = ?, auto_approve = ?, handler = ?,
    cooldown_minutes = ?,
    max_iterations = ?, stop_when = ?,
    max_age_days = ?,
    thread_id = ?, read_history = ?,
    parent_automation_id = ?, idempotency_key = ?, idempotency_scope = ?, tool_scope = ?,
    output_schema = ?
WHERE task_id = ?
"""

_SQL_LIST_LOOPS_BY_SESSION = f"""
SELECT {_COLUMNS} FROM scheduled_tasks
WHERE kind = 'loop' AND thread_id = ?
ORDER BY created_at
"""

# Kind-agnostic counterpart used by the scheduler's run-completed fast path.
# A session-bound automation is any row that targets a session via thread_id,
# regardless of kind. Channel automations created via
# `service.create(thread_id=...)` have kind="automation" and still need to
# fire through the post dispatcher.
_SQL_LIST_SESSION_BOUND_BY_SESSION = f"""
SELECT {_COLUMNS} FROM scheduled_tasks
WHERE thread_id = ? AND enabled = 1
ORDER BY created_at
"""

_SQL_LIST_BY_PARENT = f"""
SELECT {_COLUMNS} FROM scheduled_tasks
WHERE parent_automation_id = ?
ORDER BY created_at, task_id
"""

_SQL_INCREMENT_ITERATION = """
UPDATE scheduled_tasks
SET iteration_count = iteration_count + 1
WHERE task_id = ?
"""

_SQL_CLEAR_ALL_RUNNING = "UPDATE scheduled_tasks SET running_since = NULL WHERE running_since IS NOT NULL"

_SQL_CLAIM_EVENT = """
INSERT OR IGNORE INTO automation_event_dedupe (task_id, event_key, seen_at)
VALUES (?, ?, ?)
"""

_SQL_TRY_CLAIM_IDEMPOTENCY = """
INSERT OR IGNORE INTO automation_idempotency_claims (
    claim_id, scope, key, parent_automation_id, parent_fire_at,
    attempt_n, claimed_at, automation_task_id
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""

_SQL_LIST_CLAIMS_FOR_PARENT = """
SELECT claim_id, scope, key, parent_automation_id, parent_fire_at, attempt_n,
       claimed_at, automation_task_id
FROM automation_idempotency_claims
WHERE parent_automation_id = ?
ORDER BY claimed_at
"""

_SQL_EVICT_EVENT_CLAIMS = "DELETE FROM automation_event_dedupe WHERE seen_at < ?"

_SQL_ENQUEUE_EVENT = """
INSERT INTO automation_event_queue (task_id, event_key, context, created_at)
VALUES (?, ?, ?, ?)
"""

_SQL_LIST_TASKS_WITH_PENDING_EVENTS = """
SELECT DISTINCT task_id
FROM automation_event_queue
WHERE claimed_at IS NULL
"""

_SQL_CLAIM_NEXT_EVENT_CANDIDATE = """
SELECT id, context, attempt_count
FROM automation_event_queue
WHERE task_id = ?
  AND claimed_at IS NULL
  AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
ORDER BY id
LIMIT 1
"""

_SQL_CLAIM_EVENT_QUEUE_ROW = """
UPDATE automation_event_queue
SET claimed_at = ?
WHERE id = ? AND claimed_at IS NULL
"""

_SQL_COMPLETE_EVENT = "DELETE FROM automation_event_queue WHERE id = ?"

_SQL_DEAD_LETTER_EVENT = """
INSERT INTO automation_event_dead_letter (
    original_queue_id, task_id, event_key, context, created_at, failed_at, attempt_count, last_error
)
SELECT id, task_id, event_key, context, created_at, ?, attempt_count + 1, ?
FROM automation_event_queue
WHERE id = ?
"""

_SQL_FAIL_EVENT = """
UPDATE automation_event_queue
SET claimed_at = NULL,
    attempt_count = attempt_count + 1,
    last_error = ?,
    next_attempt_at = ?
WHERE id = ?
"""

_SQL_DELETE_DEDUPE_BY_TASK = "DELETE FROM automation_event_dedupe WHERE task_id = ?"

_SQL_DELETE_QUEUE_BY_TASK = "DELETE FROM automation_event_queue WHERE task_id = ?"

_SQL_RELEASE_ALL_CLAIMED_EVENTS = "UPDATE automation_event_queue SET claimed_at = NULL WHERE claimed_at IS NOT NULL"

_SQL_INCREMENT_COUNT = """
INSERT INTO automation_count_state (task_id, session_id, count, updated_at)
VALUES (?, ?, 1, ?)
ON CONFLICT(task_id, session_id) DO UPDATE SET
    count = count + 1,
    updated_at = excluded.updated_at
"""

_SQL_GET_COUNT = "SELECT count FROM automation_count_state WHERE task_id = ? AND session_id = ?"

_SQL_CLEAR_COUNT = "DELETE FROM automation_count_state WHERE task_id = ? AND session_id = ?"

_SQL_DELETE_COUNTS_BY_TASK = "DELETE FROM automation_count_state WHERE task_id = ?"

_SQL_STATUS_TASKS = """
SELECT
    COUNT(*) AS total,
    SUM(CASE WHEN enabled = 1 THEN 1 ELSE 0 END) AS enabled,
    SUM(CASE WHEN enabled = 0 THEN 1 ELSE 0 END) AS disabled,
    SUM(CASE WHEN running_since IS NOT NULL THEN 1 ELSE 0 END) AS running,
    SUM(
        CASE
            WHEN enabled = 1
              AND running_since IS NULL
              AND next_run_at IS NOT NULL
              AND next_run_at <= ?
            THEN 1 ELSE 0
        END
    ) AS due,
    MIN(
        CASE
            WHEN enabled = 1
              AND running_since IS NULL
              AND next_run_at IS NOT NULL
            THEN next_run_at
        END
    ) AS next_run_at,
    MIN(CASE WHEN running_since IS NOT NULL THEN running_since END) AS oldest_running_since
FROM scheduled_tasks
"""

_SQL_STATUS_EVENT_QUEUE = """
SELECT
    COUNT(*) AS total,
    SUM(
        CASE
            WHEN claimed_at IS NULL
              AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
            THEN 1 ELSE 0
        END
    ) AS ready,
    SUM(
        CASE
            WHEN claimed_at IS NULL
              AND next_attempt_at > ?
            THEN 1 ELSE 0
        END
    ) AS scheduled,
    SUM(CASE WHEN claimed_at IS NOT NULL THEN 1 ELSE 0 END) AS claimed,
    MIN(CASE WHEN claimed_at IS NULL THEN created_at END) AS oldest_pending_created_at,
    MIN(CASE WHEN claimed_at IS NULL AND next_attempt_at > ? THEN next_attempt_at END) AS next_attempt_at,
    MIN(CASE WHEN claimed_at IS NOT NULL THEN claimed_at END) AS oldest_claimed_at
FROM automation_event_queue
"""

_SQL_STATUS_COUNT_STATE = """
SELECT
    COUNT(*) AS total,
    MIN(updated_at) AS oldest_updated_at
FROM automation_count_state
"""

_SQL_STATUS_DEAD_LETTER = """
SELECT
    COUNT(*) AS total,
    MIN(failed_at) AS oldest_failed_at,
    MAX(failed_at) AS newest_failed_at
FROM automation_event_dead_letter
"""

_SUGGESTION_COLUMNS = (
    "id, name, description, prompt, triggers, rationale, evidence, category, icon, status, created_at, source_automation_id"
)

_SQL_DELETE_ACTIVE_SUGGESTIONS = "DELETE FROM automation_suggestions WHERE status = 'active'"

_SQL_INSERT_SUGGESTION = f"""
INSERT INTO automation_suggestions ({_SUGGESTION_COLUMNS})
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_SQL_LIST_ACTIVE_SUGGESTIONS = f"""
SELECT {_SUGGESTION_COLUMNS} FROM automation_suggestions
WHERE status = 'active'
ORDER BY created_at DESC
"""

_SQL_DISMISS_SUGGESTION = "UPDATE automation_suggestions SET status = 'dismissed' WHERE id = ?"

_SQL_ACCEPT_SUGGESTION = """
UPDATE automation_suggestions
SET status = 'accepted', source_automation_id = ?
WHERE id = ?
"""

_SQL_LIST_EXCLUDED_SIGNATURES = """
SELECT name || ' — ' || prompt AS signature FROM automation_suggestions
WHERE status IN ('dismissed', 'accepted')
"""

# --- Migration ---

_MIGRATION_V1 = """
CREATE TABLE IF NOT EXISTS automation_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE scheduled_tasks_new (
    task_id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL,
    model TEXT,
    triggers TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_run_at TEXT,
    next_run_at TEXT,
    notifiers TEXT,
    last_result TEXT,
    running_since TEXT,
    writable INTEGER NOT NULL DEFAULT 0,
    handler TEXT,
    builtin INTEGER NOT NULL DEFAULT 0,
    cooldown_minutes INTEGER
);

INSERT INTO scheduled_tasks_new (
    task_id, name, description, model, triggers, enabled,
    created_at, last_run_at, next_run_at, notifiers, last_result, running_since,
    writable, handler, builtin, cooldown_minutes
)
SELECT
    task_id, name, description, model, json_array(json(trigger)), enabled,
    created_at, last_run_at, next_run_at, notifiers, last_result, running_since,
    writable, NULL, 0, NULL
FROM scheduled_tasks;

DROP TABLE scheduled_tasks;
ALTER TABLE scheduled_tasks_new RENAME TO scheduled_tasks;

CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_next_run ON scheduled_tasks(next_run_at);
CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_enabled ON scheduled_tasks(enabled);
"""

CURRENT_SCHEMA_VERSION = 14

_LOOP_COLUMNS: tuple[tuple[str, str], ...] = (
    ("kind", "TEXT NOT NULL DEFAULT 'automation'"),
    ("target_session_id", "TEXT"),
    ("loop_prompt", "TEXT"),
    ("max_iterations", "INTEGER"),
    ("iteration_count", "INTEGER NOT NULL DEFAULT 0"),
    ("stop_when", "TEXT"),
    ("max_age_days", "INTEGER"),
)

_V5_AUTOMATION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("thread_id", "TEXT"),
    ("read_history", "INTEGER NOT NULL DEFAULT 0"),
    ("parent_automation_id", "TEXT"),
    ("idempotency_key", "TEXT"),
    ("idempotency_scope", "TEXT"),
)

_DAY_INT_TO_NAME = {0: "mon", 1: "tue", 2: "wed", 3: "thu", 4: "fri", 5: "sat", 6: "sun"}


def _normalize_trigger(t: dict) -> dict:
    """Convert any legacy trigger format to the canonical string-based format.

    Handles:
    - asdict() dicts: {"hour":16,"minute":0}, {"days":[0,1,2,...]}, {"delta":{"seconds":...}}
    - Pre-v1 keys: "time_of_day"/"recurrence"/"repeat"
    - Stray null values from old params() serialization
    """
    typ = t.get("type", "time")

    if typ == "time":
        # Pre-v1 legacy: time_of_day / recurrence / repeat
        if "time_of_day" in t:
            at = t["time_of_day"]
            recurrence = t.get("recurrence") or t.get("repeat", "once")
            out = {"type": "time", "at": at}
            if recurrence != "once":
                out["days"] = recurrence
            return out

        out: dict = {"type": "time"}
        for key in ("at", "start", "end"):
            val = t.get(key)
            if isinstance(val, dict) and "hour" in val:
                out[key] = f"{val['hour']:02d}:{val['minute']:02d}"
            elif isinstance(val, str):
                out[key] = val

        val = t.get("days")
        if isinstance(val, dict) and "days" in val:
            days = sorted(val["days"])
            if days == list(range(7)):
                out["days"] = "daily"
            elif days == list(range(5)):
                out["days"] = "weekdays"
            else:
                out["days"] = ",".join(_DAY_INT_TO_NAME[d] for d in days)
        elif isinstance(val, str):
            out["days"] = val

        val = t.get("every")
        if isinstance(val, dict) and "delta" in val:
            td = val["delta"]
            secs = int(td.get("seconds", 0)) + int(td.get("hours", 0)) * 3600 + int(td.get("minutes", 0)) * 60
            h, m = divmod(secs // 60, 60)
            out["every"] = f"{h}h{m}m" if h and m else (f"{h}h" if h else f"{m}m")
        elif isinstance(val, str):
            out["every"] = val

        return out

    if typ == "event":
        out = {"type": "event", "event_type": t["event_type"]}
        if t.get("lead_minutes") is not None:
            out["lead_minutes"] = t["lead_minutes"]
        return out

    if typ == "idle":
        return {"type": "idle", "idle_minutes": t["idle_minutes"]}

    if typ == "count":
        return {"type": "count", "every_n": t["every_n"]}

    return t


async def _migrate_v2(conn: aiosqlite.Connection) -> None:
    rows = await conn.execute_fetchall("SELECT task_id, triggers FROM scheduled_tasks")
    for row in rows:
        items = json.loads(row["triggers"])
        normalized = [_normalize_trigger(t) for t in items]
        new_json = json.dumps(normalized)
        if new_json != row["triggers"]:
            await conn.execute(
                "UPDATE scheduled_tasks SET triggers = ? WHERE task_id = ?",
                (new_json, row["task_id"]),
            )
    _logger.info("Migrated triggers to v2 (canonical string format)")


async def _get_schema_version(conn: aiosqlite.Connection) -> int:
    try:
        rows = await conn.execute_fetchall("SELECT value FROM automation_meta WHERE key = 'schema_version'")
        if rows:
            return int(rows[0]["value"])
    except Exception:
        pass
    return 0


async def _set_schema_version(conn: aiosqlite.Connection, version: int) -> None:
    await conn.execute(
        "INSERT OR REPLACE INTO automation_meta (key, value) VALUES ('schema_version', ?)",
        (str(version),),
    )


async def _migrate_v12(conn: aiosqlite.Connection) -> None:
    rows = await conn.execute_fetchall("PRAGMA table_info(scheduled_tasks)")
    existing = {row["name"] for row in rows}
    if "tool_scope" not in existing:
        await conn.execute("ALTER TABLE scheduled_tasks ADD COLUMN tool_scope TEXT")


async def _migrate_v14(conn: aiosqlite.Connection) -> None:
    """Split executable instructions from concise display copy.

    Pre-v14 `description` contained the complete prompt. Preserve it verbatim
    in `prompt`; deliberately leave the display description pending rather
    than guessing or truncating it. The service exposes an explicit
    on-demand generation endpoint for those rows.
    """
    rows = await conn.execute_fetchall("PRAGMA table_info(scheduled_tasks)")
    existing = {row["name"] for row in rows}
    if "prompt" not in existing or "description_source" not in existing:
        await conn.executescript(
            """
            ALTER TABLE scheduled_tasks RENAME TO scheduled_tasks_v14_old;

            CREATE TABLE scheduled_tasks (
                task_id TEXT PRIMARY KEY,
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
                output_schema TEXT
            );

            INSERT INTO scheduled_tasks (
                task_id, name, description, description_source, prompt, model,
                triggers, enabled, created_at, last_run_at, next_run_at,
                last_result, running_since, auto_approve, handler, builtin,
                cooldown_minutes, kind, max_iterations, iteration_count,
                stop_when, max_age_days, thread_id, read_history,
                parent_automation_id, idempotency_key, idempotency_scope,
                tool_scope, output_schema
            )
            SELECT
                task_id, name, NULL, NULL, description, model,
                triggers, enabled, created_at, last_run_at, next_run_at,
                last_result, running_since, auto_approve, handler, builtin,
                cooldown_minutes, kind, max_iterations, iteration_count,
                stop_when, max_age_days, thread_id, read_history,
                parent_automation_id, idempotency_key, idempotency_scope,
                tool_scope, output_schema
            FROM scheduled_tasks_v14_old;

            DROP TABLE scheduled_tasks_v14_old;

            CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_next_run ON scheduled_tasks(next_run_at);
            CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_enabled ON scheduled_tasks(enabled);
            CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_parent ON scheduled_tasks(parent_automation_id);
            CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_thread_kind ON scheduled_tasks(thread_id, kind);
            CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_kind_thread ON scheduled_tasks(kind, thread_id);
            CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_idempotency
            ON scheduled_tasks(idempotency_scope, idempotency_key)
            WHERE idempotency_key IS NOT NULL;
            """
        )

    suggestion_rows = await conn.execute_fetchall("PRAGMA table_info(automation_suggestions)")
    suggestion_columns = {row["name"] for row in suggestion_rows}
    if suggestion_rows and "prompt" not in suggestion_columns:
        await conn.executescript(
            """
            ALTER TABLE automation_suggestions RENAME TO automation_suggestions_v14_old;

            CREATE TABLE automation_suggestions (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                prompt TEXT NOT NULL,
                triggers TEXT NOT NULL,
                rationale TEXT NOT NULL,
                evidence TEXT,
                category TEXT NOT NULL,
                icon TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                source_automation_id TEXT
            );

            INSERT INTO automation_suggestions (
                id, name, description, prompt, triggers, rationale, evidence,
                category, icon, status, created_at, source_automation_id
            )
            SELECT
                id, name, NULL, description, triggers, rationale, evidence,
                category, icon, status, created_at, source_automation_id
            FROM automation_suggestions_v14_old;

            DROP TABLE automation_suggestions_v14_old;

            CREATE INDEX IF NOT EXISTS idx_suggestions_status
            ON automation_suggestions(status, created_at);
            """
        )


async def _migrate(conn: aiosqlite.Connection) -> None:
    version = await _get_schema_version(conn)

    if version < 1:
        # Check if old schema exists (has 'trigger' column instead of 'triggers')
        try:
            rows = await conn.execute_fetchall("PRAGMA table_info(scheduled_tasks)")
            columns = {row["name"] for row in rows}
            if "trigger" in columns and "triggers" not in columns:
                _logger.info("Migrating automation store to v1 (trigger -> triggers)")
                await conn.executescript(_MIGRATION_V1)
                await _set_schema_version(conn, 1)
                await conn.commit()
            elif "triggers" in columns:
                # Already on new schema, just ensure meta table and version
                await conn.execute(
                    "CREATE TABLE IF NOT EXISTS automation_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
                )
                await _set_schema_version(conn, 1)
                await conn.commit()
            else:
                pass
        except Exception:
            pass

    if version < 2:
        try:
            await _migrate_v2(conn)
            await _set_schema_version(conn, 2)
            await conn.commit()
        except Exception:
            pass

    if version < 4:
        rows = await conn.execute_fetchall("PRAGMA table_info(scheduled_tasks)")
        existing = {row["name"] for row in rows}
        for col, definition in _LOOP_COLUMNS:
            if col not in existing:
                await conn.execute(f"ALTER TABLE scheduled_tasks ADD COLUMN {col} {definition}")
        await _set_schema_version(conn, 4)
        await conn.commit()
        _logger.info("Migrated automation store to v4 (loop fields incl. max_age_days)")

    if version < 5:
        rows = await conn.execute_fetchall("PRAGMA table_info(scheduled_tasks)")
        existing = {row["name"] for row in rows}
        for col, definition in _V5_AUTOMATION_COLUMNS:
            if col not in existing:
                await conn.execute(f"ALTER TABLE scheduled_tasks ADD COLUMN {col} {definition}")
        # v5 indexes — created here (not in _SCHEMA) so they only run after
        # the referenced columns exist. Idempotent via IF NOT EXISTS.
        await conn.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_parent
            ON scheduled_tasks(parent_automation_id);
            CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_thread_kind
            ON scheduled_tasks(thread_id, kind);
            CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_kind_thread
            ON scheduled_tasks(kind, thread_id);
            CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_idempotency
            ON scheduled_tasks(idempotency_scope, idempotency_key)
            WHERE idempotency_key IS NOT NULL;
            """
        )
        # Backfill loop rows: thread_id mirrors target_session_id, read_history=1.
        # Guarded by thread_id IS NULL so reruns don't clobber later edits.
        await conn.execute(
            """
            UPDATE scheduled_tasks
            SET thread_id = target_session_id,
                read_history = 1
            WHERE kind = 'loop'
              AND thread_id IS NULL
              AND target_session_id IS NOT NULL
            """
        )
        await _set_schema_version(conn, 5)
        await conn.commit()
        _logger.info("Migrated automation store to v5 (channel-aware automation fields)")

    if version < 6:
        # Idempotency claim table for channel-aware automations.
        # SQLite treats NULL != NULL in UNIQUE constraints, so we synthesize
        # a deterministic primary key from the (scope, key, parent_automation_id,
        # parent_fire_at, attempt_n) tuple. The PK string is built at insert
        # time via try_claim_idempotency() in the store layer.
        await conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS automation_idempotency_claims (
                claim_id TEXT PRIMARY KEY,
                scope TEXT NOT NULL,
                key TEXT NOT NULL,
                parent_automation_id TEXT,
                parent_fire_at TEXT,
                attempt_n INTEGER,
                claimed_at TEXT NOT NULL,
                automation_task_id TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_automation_idempotency_claims_parent
            ON automation_idempotency_claims(parent_automation_id);
            """
        )
        await _set_schema_version(conn, 6)
        await conn.commit()
        _logger.info("Migrated automation store to v6 (idempotency claim table)")

    if version < 7:
        await conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS automation_event_dead_letter (
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

            CREATE INDEX IF NOT EXISTS idx_automation_event_dead_letter_task_failed
            ON automation_event_dead_letter(task_id, failed_at);
            """
        )
        await _set_schema_version(conn, 7)
        await conn.commit()
        _logger.info("Migrated automation store to v7 (event dead-letter table)")

    if version < 8:
        rows = await conn.execute_fetchall("PRAGMA table_info(scheduled_tasks)")
        existing = {row["name"] for row in rows}
        if "writable" in existing and "auto_approve" not in existing:
            await conn.execute("ALTER TABLE scheduled_tasks RENAME COLUMN writable TO auto_approve")
        await _set_schema_version(conn, 8)
        await conn.commit()
        _logger.info("Migrated automation store to v8 (writable -> auto_approve)")

    if version < 9:
        rows = await conn.execute_fetchall("PRAGMA table_info(scheduled_tasks)")
        existing = {row["name"] for row in rows}
        if "loop_prompt" in existing:
            await conn.executescript(
                """
                CREATE TABLE scheduled_tasks_v9 (
                    task_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL,
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
                    target_session_id TEXT,
                    max_iterations INTEGER,
                    iteration_count INTEGER NOT NULL DEFAULT 0,
                    stop_when TEXT,
                    max_age_days INTEGER,
                    thread_id TEXT,
                    read_history INTEGER NOT NULL DEFAULT 0,
                    parent_automation_id TEXT,
                    idempotency_key TEXT,
                    idempotency_scope TEXT
                );

                INSERT INTO scheduled_tasks_v9 (
                    task_id, name, description, model, triggers, enabled,
                    created_at, last_run_at, next_run_at, notifiers, last_result,
                    running_since, auto_approve, handler, builtin, cooldown_minutes,
                    kind, target_session_id, max_iterations, iteration_count,
                    stop_when, max_age_days, thread_id, read_history,
                    parent_automation_id, idempotency_key, idempotency_scope
                )
                SELECT
                    task_id, name, description, model, triggers, enabled,
                    created_at, last_run_at, next_run_at, notifiers, last_result,
                    running_since, auto_approve, handler, builtin, cooldown_minutes,
                    kind, target_session_id, max_iterations, iteration_count,
                    stop_when, max_age_days, thread_id, read_history,
                    parent_automation_id, idempotency_key, idempotency_scope
                FROM scheduled_tasks;

                DROP TABLE scheduled_tasks;
                ALTER TABLE scheduled_tasks_v9 RENAME TO scheduled_tasks;

                CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_next_run
                ON scheduled_tasks(next_run_at);
                CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_enabled
                ON scheduled_tasks(enabled);
                CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_kind_session
                ON scheduled_tasks(kind, target_session_id);
                CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_parent
                ON scheduled_tasks(parent_automation_id);
                CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_thread_kind
                ON scheduled_tasks(thread_id, kind);
                CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_idempotency
                ON scheduled_tasks(idempotency_scope, idempotency_key)
                WHERE idempotency_key IS NOT NULL;
                """
            )
        await _set_schema_version(conn, 9)
        await conn.commit()
        _logger.info("Migrated automation store to v9 (dropped loop_prompt column)")

    if version < 10:
        rows = await conn.execute_fetchall("PRAGMA table_info(scheduled_tasks)")
        existing = {row["name"] for row in rows}
        if "target_session_id" in existing:
            # Belt-and-suspenders: backfill thread_id for any rows that were
            # missed by v5 (e.g. old code inserted between v5 and v10). This
            # runs and commits before executescript so the backfill lands in
            # its own transaction.
            await conn.execute(
                """
                UPDATE scheduled_tasks
                SET thread_id = target_session_id
                WHERE thread_id IS NULL AND target_session_id IS NOT NULL
                """
            )
            await conn.commit()
            await conn.executescript(
                """
                CREATE TABLE scheduled_tasks_v10 (
                    task_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL,
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
                    idempotency_scope TEXT
                );

                INSERT INTO scheduled_tasks_v10 (
                    task_id, name, description, model, triggers, enabled,
                    created_at, last_run_at, next_run_at, notifiers, last_result,
                    running_since, auto_approve, handler, builtin, cooldown_minutes,
                    kind, max_iterations, iteration_count, stop_when, max_age_days,
                    thread_id, read_history, parent_automation_id, idempotency_key,
                    idempotency_scope
                )
                SELECT
                    task_id, name, description, model, triggers, enabled,
                    created_at, last_run_at, next_run_at, notifiers, last_result,
                    running_since, auto_approve, handler, builtin, cooldown_minutes,
                    kind, max_iterations, iteration_count, stop_when, max_age_days,
                    thread_id, read_history, parent_automation_id, idempotency_key,
                    idempotency_scope
                FROM scheduled_tasks;

                DROP TABLE scheduled_tasks;
                ALTER TABLE scheduled_tasks_v10 RENAME TO scheduled_tasks;

                CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_next_run
                ON scheduled_tasks(next_run_at);
                CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_enabled
                ON scheduled_tasks(enabled);
                CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_kind_thread
                ON scheduled_tasks(kind, thread_id);
                CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_parent
                ON scheduled_tasks(parent_automation_id);
                CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_thread_kind
                ON scheduled_tasks(thread_id, kind);
                CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_idempotency
                ON scheduled_tasks(idempotency_scope, idempotency_key)
                WHERE idempotency_key IS NOT NULL;
                """
            )
        await _set_schema_version(conn, 10)
        await conn.commit()
        _logger.info("Migrated automation store to v10 (dropped target_session_id, rewrote kind index to thread_id)")

    if version < 11:
        await conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS automation_suggestions (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                prompt TEXT NOT NULL,
                triggers TEXT NOT NULL,
                rationale TEXT NOT NULL,
                evidence TEXT,
                category TEXT NOT NULL,
                icon TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                source_automation_id TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_suggestions_status
            ON automation_suggestions(status, created_at);
            """
        )
        await _set_schema_version(conn, 11)
        await conn.commit()
        _logger.info("Migrated automation store to v11 (automation_suggestions table)")

    if version < 12:
        await _migrate_v12(conn)
        await _set_schema_version(conn, 12)
        await conn.commit()
        _logger.info("Migrated automation store to v12 (tool_scope allowlist)")

    if version < 13:
        cursor = await conn.execute("PRAGMA table_info(scheduled_tasks)")
        existing = {row[1] for row in await cursor.fetchall()}
        if "output_schema" not in existing:
            await conn.execute("ALTER TABLE scheduled_tasks ADD COLUMN output_schema TEXT")
        await _set_schema_version(conn, 13)
        await conn.commit()
        _logger.info("Migrated automation store to v13 (output_schema)")

    if version < 14:
        await _migrate_v14(conn)
        await _set_schema_version(conn, 14)
        await conn.commit()
        _logger.info("Migrated automation store to v14 (prompt and display-description split)")


class AutomationStore:
    def __init__(self, conn: aiosqlite.Connection):
        self.conn = conn

    async def init_schema(self) -> None:
        # _SCHEMA must run first: it CREATEs tables (idempotent for both
        # fresh and existing DBs) so the migration's ALTER TABLE blocks
        # can target a guaranteed-existing scheduled_tasks. With the
        # previous order, fresh DBs hit "no such table" inside _migrate
        # because the table wasn't created yet. Now that v4/v5 migrations
        # no longer swallow exceptions, the ordering bug is loud.
        await self.conn.executescript(_SCHEMA)
        await _migrate(self.conn)
        await _set_schema_version(self.conn, CURRENT_SCHEMA_VERSION)
        await self.conn.commit()

    async def save(self, automation: Automation) -> None:
        await self.conn.execute(
            _SQL_SAVE,
            (
                automation.task_id,
                automation.name,
                automation.description,
                automation.description_source,
                automation.prompt,
                automation.model,
                _serialize_triggers(automation.triggers),
                int(automation.enabled),
                automation.created_at.isoformat(),
                automation.last_run_at.isoformat() if automation.last_run_at else None,
                automation.next_run_at.isoformat() if automation.next_run_at else None,
                automation.last_result,
                automation.running_since.isoformat() if automation.running_since else None,
                int(automation.auto_approve),
                automation.handler,
                int(automation.builtin),
                automation.cooldown_minutes,
                automation.kind,
                automation.max_iterations,
                int(automation.iteration_count),
                automation.stop_when,
                automation.max_age_days,
                automation.thread_id,
                int(automation.read_history),
                automation.parent_automation_id,
                automation.idempotency_key,
                automation.idempotency_scope,
                json.dumps(automation.tool_scope) if automation.tool_scope else None,
                automation.output_schema,
            ),
        )
        await self.conn.commit()

    async def get(self, task_id: str) -> Automation | None:
        rows = await self.conn.execute_fetchall(_SQL_GET_BY_ID, (task_id,))
        if not rows:
            return None
        return _row_to_automation(rows[0])

    async def list_all(self) -> list[Automation]:
        rows = await self.conn.execute_fetchall(_SQL_LIST_ALL)
        return [_row_to_automation(row) for row in rows]

    async def list_due(self, now: datetime) -> list[Automation]:
        rows = await self.conn.execute_fetchall(_SQL_LIST_DUE, (now.isoformat(),))
        return [_row_to_automation(row) for row in rows]

    async def list_running(self) -> list[Automation]:
        rows = await self.conn.execute_fetchall(_SQL_LIST_RUNNING)
        return [_row_to_automation(row) for row in rows]

    async def list_event_triggered(self, event_type: str) -> list[Automation]:
        rows = await self.conn.execute_fetchall(_SQL_LIST_EVENT_TRIGGERED, (event_type,))
        return [_row_to_automation(row) for row in rows]

    async def list_by_trigger_type(self, trigger_type: str) -> list[Automation]:
        rows = await self.conn.execute_fetchall(_SQL_LIST_BY_TRIGGER_TYPE, (trigger_type,))
        return [_row_to_automation(row) for row in rows]

    async def list_message_triggered(self, source: str, channel_id: str) -> list[Automation]:
        rows = await self.conn.execute_fetchall(_SQL_LIST_MESSAGE_TRIGGERED, (source, channel_id))
        return [_row_to_automation(row) for row in rows]

    async def list_watched_slack_channels(self) -> list[str]:
        rows = await self.conn.execute_fetchall(_SQL_LIST_WATCHED_SLACK_CHANNELS)
        return [row["channel_id"] for row in rows if row["channel_id"] is not None]

    async def try_mark_running(self, task_id: str, now: datetime) -> bool:
        cursor = await self.conn.execute(_SQL_TRY_MARK_RUNNING, (now.isoformat(), task_id))
        await self.conn.commit()
        return cursor.rowcount > 0

    async def clear_running(self, task_id: str) -> None:
        await self.conn.execute(_SQL_CLEAR_RUNNING, (task_id,))
        await self.conn.commit()

    async def update_last_run(
        self,
        task_id: str,
        last_run: datetime,
        next_run: datetime | None,
        result: str | None = None,
        *,
        preserve_result: bool = False,
    ) -> None:
        """`preserve_result` advances the clock without touching last_result —
        detached (fire-and-accept) runs have no result yet at this point."""
        if preserve_result:
            await self.conn.execute(
                _SQL_UPDATE_LAST_RUN_KEEP_RESULT,
                (last_run.isoformat(), next_run.isoformat() if next_run else None, task_id),
            )
        else:
            await self.conn.execute(
                _SQL_UPDATE_LAST_RUN,
                (last_run.isoformat(), next_run.isoformat() if next_run else None, result, task_id),
            )
        await self.conn.commit()

    async def record_run_start(self, task_id: str, started_at: datetime) -> int:
        cursor = await self.conn.execute(_SQL_INSERT_RUN, (task_id, started_at.isoformat()))
        await self.conn.commit()
        return int(cursor.lastrowid or 0)

    async def record_run_finish(
        self,
        run_id: int,
        *,
        status: str,
        result: str | None,
        error: str | None,
        ended_at: datetime,
    ) -> None:
        # Bound stored text so a chatty run can't bloat the history table.
        clip = lambda s: s if s is None or len(s) <= 4000 else s[:4000] + "…"  # noqa: E731
        await self.conn.execute(
            _SQL_FINISH_RUN,
            (ended_at.isoformat(), status, clip(result), clip(error), run_id),
        )
        await self.conn.commit()

    async def finalize_latest_run(self, task_id: str, result: str | None, ended_at: datetime) -> bool:
        """Settle the newest IN-FLIGHT run row with the run's real outcome.
        Iteration loops dispatch fire-and-accept, so their row stays
        status='running' until the detached run's RunCompleted arrives here.
        The status guard makes this idempotent — an already-settled (or
        restart-failed) row is never rewritten. Returns whether a row was
        settled."""
        clipped = result if result is None or len(result) <= 4000 else result[:4000] + "…"
        cursor = await self.conn.execute(_SQL_FINALIZE_LATEST_RUN, (clipped, ended_at.isoformat(), task_id))
        await self.conn.commit()
        return cursor.rowcount > 0

    async def fail_orphaned_runs(self, ended_at: datetime, error: str) -> int:
        """Boot-time sweep: any row still 'running' belongs to a run the
        previous process never settled — mark it failed so history stays
        terminal."""
        cursor = await self.conn.execute(_SQL_FAIL_ORPHANED_RUNS, (error, ended_at.isoformat()))
        await self.conn.commit()
        return cursor.rowcount

    async def fail_latest_running_run(self, task_id: str, ended_at: datetime, error: str) -> bool:
        cursor = await self.conn.execute(_SQL_FAIL_LATEST_RUNNING_RUN, (error, ended_at.isoformat(), task_id))
        await self.conn.commit()
        return cursor.rowcount > 0

    async def recent_run_statuses(self, task_ids: list[str], per_task: int = 4) -> dict[str, list[str]]:
        """Newest-first run statuses per task (for the card's sparkline/pip).
        One small indexed query per task — bounded by the automation count."""
        out: dict[str, list[str]] = {}
        for task_id in task_ids:
            cursor = await self.conn.execute(_SQL_RECENT_STATUSES, (task_id, per_task))
            rows = await cursor.fetchall()
            out[task_id] = [row["status"] for row in rows]
        return out

    async def latest_runs(self) -> dict[str, dict]:
        """Newest run per task in one query — the overview hydration path."""
        cursor = await self.conn.execute(_SQL_LATEST_RUNS)
        rows = await cursor.fetchall()
        return {
            row["task_id"]: {
                "id": row["id"],
                "task_id": row["task_id"],
                "started_at": row["started_at"],
                "ended_at": row["ended_at"],
                "status": row["status"],
                "result": row["result"],
                "error": row["error"],
            }
            for row in rows
        }

    async def list_runs(self, task_id: str, limit: int = 20) -> list[dict]:
        cursor = await self.conn.execute(_SQL_LIST_RUNS, (task_id, limit))
        rows = await cursor.fetchall()
        return [
            {
                "id": row["id"],
                "task_id": row["task_id"],
                "started_at": row["started_at"],
                "ended_at": row["ended_at"],
                "status": row["status"],
                "result": row["result"],
                "error": row["error"],
            }
            for row in rows
        ]

    async def delete(self, task_id: str) -> bool:
        cursor = await self.conn.execute(_SQL_DELETE, (task_id,))
        await self.conn.execute(_SQL_DELETE_DEDUPE_BY_TASK, (task_id,))
        await self.conn.execute(_SQL_DELETE_QUEUE_BY_TASK, (task_id,))
        await self.conn.execute(_SQL_DELETE_COUNTS_BY_TASK, (task_id,))
        await self.conn.commit()
        return cursor.rowcount > 0

    async def set_enabled(self, task_id: str, enabled: bool) -> None:
        await self.conn.execute(_SQL_SET_ENABLED, (int(enabled), task_id))
        await self.conn.commit()

    async def disable_by_parent(self, parent_id: str) -> int:
        cursor = await self.conn.execute(_SQL_DISABLE_BY_PARENT, (parent_id,))
        await self.conn.commit()
        return cursor.rowcount

    async def set_auto_approve(self, task_id: str, auto_approve: bool) -> None:
        await self.conn.execute(_SQL_SET_AUTO_APPROVE, (int(auto_approve), task_id))
        await self.conn.commit()

    async def update_metadata(self, automation: Automation) -> None:
        await self.conn.execute(
            _SQL_UPDATE_METADATA,
            (
                automation.name,
                automation.description,
                automation.description_source,
                automation.prompt,
                automation.model,
                _serialize_triggers(automation.triggers),
                int(automation.enabled),
                automation.next_run_at.isoformat() if automation.next_run_at else None,
                int(automation.auto_approve),
                automation.handler,
                automation.cooldown_minutes,
                automation.max_iterations,
                automation.stop_when,
                automation.max_age_days,
                automation.thread_id,
                int(automation.read_history),
                automation.parent_automation_id,
                automation.idempotency_key,
                automation.idempotency_scope,
                json.dumps(automation.tool_scope) if automation.tool_scope else None,
                automation.output_schema,
                automation.task_id,
            ),
        )
        await self.conn.commit()

    async def list_loops_by_session(self, session_id: str) -> list[Automation]:
        rows = await self.conn.execute_fetchall(_SQL_LIST_LOOPS_BY_SESSION, (session_id,))
        return [_row_to_automation(row) for row in rows]

    async def list_session_bound_by_session(self, session_id: str) -> list[Automation]:
        """Kind-agnostic: any automation that targets `session_id` via
        thread_id. Used by the scheduler's run-completed fast path to fire
        deferred session-bound work the moment the session goes idle."""
        rows = await self.conn.execute_fetchall(_SQL_LIST_SESSION_BOUND_BY_SESSION, (session_id,))
        return [_row_to_automation(row) for row in rows]

    async def list_by_parent(self, parent_automation_id: str) -> list[Automation]:
        rows = await self.conn.execute_fetchall(_SQL_LIST_BY_PARENT, (parent_automation_id,))
        return [_row_to_automation(row) for row in rows]

    async def increment_iteration(self, task_id: str) -> None:
        await self.conn.execute(_SQL_INCREMENT_ITERATION, (task_id,))
        await self.conn.commit()

    async def clear_all_running(self) -> int:
        cursor = await self.conn.execute(_SQL_CLEAR_ALL_RUNNING)
        await self.conn.commit()
        return cursor.rowcount

    async def set_next_run(self, task_id: str, next_run: datetime) -> None:
        await self.conn.execute(_SQL_SET_NEXT_RUN, (next_run.isoformat(), task_id))
        await self.conn.commit()

    async def set_last_result(self, task_id: str, result: str | None) -> None:
        # Bound the stored text so a chatty run can't bloat the row.
        clipped = result if result is None or len(result) <= 4000 else result[:4000] + "…"
        await self.conn.execute(_SQL_SET_LAST_RESULT, (clipped, task_id))
        await self.conn.commit()

    async def claim_and_enqueue_event(
        self,
        task_id: str,
        event_key: str,
        context: str,
        created_at: datetime,
    ) -> bool:
        await self.conn.execute("BEGIN")
        try:
            cursor = await self.conn.execute(_SQL_CLAIM_EVENT, (task_id, event_key, created_at.isoformat()))
            if cursor.rowcount == 0:
                await self.conn.rollback()
                return False
            await self.conn.execute(
                _SQL_ENQUEUE_EVENT,
                (task_id, event_key, context, created_at.isoformat()),
            )
            await self.conn.commit()
            return True
        except BaseException:
            await self.conn.rollback()
            raise

    async def try_claim_idempotency(
        self,
        *,
        scope: str,
        key: str,
        automation_task_id: str | None = None,
        parent_automation_id: str | None = None,
        parent_fire_at: str | None = None,
        attempt_n: int | None = None,
        claimed_at: datetime | None = None,
    ) -> bool:
        _validate_idempotency_claim(
            scope=scope,
            key=key,
            parent_automation_id=parent_automation_id,
            parent_fire_at=parent_fire_at,
            attempt_n=attempt_n,
            automation_task_id=automation_task_id,
        )
        claim_id = _build_claim_id(
            scope=scope,
            key=key,
            parent_automation_id=parent_automation_id,
            parent_fire_at=parent_fire_at,
            attempt_n=attempt_n,
        )
        cursor = await self.conn.execute(
            _SQL_TRY_CLAIM_IDEMPOTENCY,
            (
                claim_id,
                scope,
                key,
                parent_automation_id,
                parent_fire_at,
                attempt_n,
                (claimed_at or datetime.now(UTC)).isoformat(),
                automation_task_id,
            ),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def save_with_claim(
        self,
        automation: Automation,
        *,
        scope: str,
        key: str,
        parent_automation_id: str | None = None,
        parent_fire_at: str | None = None,
        attempt_n: int | None = None,
        claimed_at: datetime | None = None,
    ) -> bool:
        """Atomically claim idempotency and save the automation row.

        Returns True iff both rows were written. Returns False if the claim
        was already taken (no automation row inserted). Any exception during
        save rolls back the claim so a retry under the same key can succeed.
        """
        _validate_idempotency_claim(
            scope=scope,
            key=key,
            parent_automation_id=parent_automation_id,
            parent_fire_at=parent_fire_at,
            attempt_n=attempt_n,
            automation_task_id=automation.task_id,
        )
        claim_id = _build_claim_id(
            scope=scope,
            key=key,
            parent_automation_id=parent_automation_id,
            parent_fire_at=parent_fire_at,
            attempt_n=attempt_n,
        )
        # aiosqlite defaults to autocommit-ish behavior via implicit transactions
        # on DML. We open one explicit transaction so the claim and the row
        # share atomicity.
        await self.conn.execute("BEGIN")
        try:
            cursor = await self.conn.execute(
                _SQL_TRY_CLAIM_IDEMPOTENCY,
                (
                    claim_id,
                    scope,
                    key,
                    parent_automation_id,
                    parent_fire_at,
                    attempt_n,
                    (claimed_at or datetime.now(UTC)).isoformat(),
                    automation.task_id,
                ),
            )
            if cursor.rowcount == 0:
                await self.conn.rollback()
                return False
            await self.conn.execute(
                _SQL_SAVE,
                (
                    automation.task_id,
                    automation.name,
                    automation.description,
                    automation.description_source,
                    automation.prompt,
                    automation.model,
                    _serialize_triggers(automation.triggers),
                    int(automation.enabled),
                    automation.created_at.isoformat(),
                    automation.last_run_at.isoformat() if automation.last_run_at else None,
                    automation.next_run_at.isoformat() if automation.next_run_at else None,
                    automation.last_result,
                    automation.running_since.isoformat() if automation.running_since else None,
                    int(automation.auto_approve),
                    automation.handler,
                    int(automation.builtin),
                    automation.cooldown_minutes,
                    automation.kind,
                    automation.max_iterations,
                    int(automation.iteration_count),
                    automation.stop_when,
                    automation.max_age_days,
                    automation.thread_id,
                    int(automation.read_history),
                    automation.parent_automation_id,
                    automation.idempotency_key,
                    automation.idempotency_scope,
                    json.dumps(automation.tool_scope) if automation.tool_scope else None,
                    automation.output_schema,
                ),
            )
            await self.conn.commit()
            return True
        except BaseException:
            await self.conn.rollback()
            raise

    async def list_claims_for_parent(self, parent_automation_id: str) -> list[IdempotencyClaim]:
        rows = await self.conn.execute_fetchall(_SQL_LIST_CLAIMS_FOR_PARENT, (parent_automation_id,))
        return [
            IdempotencyClaim(
                claim_id=row["claim_id"],
                scope=row["scope"],
                key=row["key"],
                parent_automation_id=row["parent_automation_id"],
                parent_fire_at=row["parent_fire_at"],
                attempt_n=int(row["attempt_n"]) if row["attempt_n"] is not None else None,
                claimed_at=datetime.fromisoformat(row["claimed_at"]),
                automation_task_id=row["automation_task_id"],
            )
            for row in rows
        ]

    async def evict_event_claims_older_than(self, cutoff: datetime) -> None:
        await self.conn.execute(_SQL_EVICT_EVENT_CLAIMS, (cutoff.isoformat(),))
        await self.conn.commit()

    async def enqueue_event(self, task_id: str, event_key: str, context: str, created_at: datetime) -> None:
        await self.conn.execute(
            _SQL_ENQUEUE_EVENT,
            (task_id, event_key, context, created_at.isoformat()),
        )
        await self.conn.commit()

    async def list_tasks_with_pending_events(self) -> list[str]:
        rows = await self.conn.execute_fetchall(_SQL_LIST_TASKS_WITH_PENDING_EVENTS)
        return [row["task_id"] for row in rows]

    async def claim_next_event(self, task_id: str, claimed_at: datetime) -> tuple[int, str, int] | None:
        while True:
            rows = await self.conn.execute_fetchall(
                _SQL_CLAIM_NEXT_EVENT_CANDIDATE,
                (task_id, claimed_at.isoformat()),
            )
            if not rows:
                return None

            queue_id = int(rows[0]["id"])
            context = rows[0]["context"]
            attempt_count = int(rows[0]["attempt_count"] or 0)
            cursor = await self.conn.execute(
                _SQL_CLAIM_EVENT_QUEUE_ROW,
                (claimed_at.isoformat(), queue_id),
            )
            await self.conn.commit()
            if cursor.rowcount > 0:
                return queue_id, context, attempt_count

    async def complete_event(self, queue_id: int) -> None:
        await self.conn.execute(_SQL_COMPLETE_EVENT, (queue_id,))
        await self.conn.commit()

    async def fail_event(self, queue_id: int, error: str, next_attempt_at: datetime) -> None:
        await self.conn.execute(
            _SQL_FAIL_EVENT,
            (error, next_attempt_at.isoformat(), queue_id),
        )
        await self.conn.commit()

    async def release_event_claim(self, queue_id: int) -> None:
        await self.conn.execute(
            "UPDATE automation_event_queue SET claimed_at = NULL WHERE id = ?",
            (queue_id,),
        )
        await self.conn.commit()

    async def dead_letter_event(self, queue_id: int, error: str, failed_at: datetime) -> None:
        await self.conn.execute("BEGIN")
        try:
            await self.conn.execute(
                _SQL_DEAD_LETTER_EVENT,
                (failed_at.isoformat(), error, queue_id),
            )
            await self.conn.execute(_SQL_COMPLETE_EVENT, (queue_id,))
            await self.conn.commit()
        except BaseException:
            await self.conn.rollback()
            raise

    async def release_all_claimed_events(self) -> int:
        cursor = await self.conn.execute(_SQL_RELEASE_ALL_CLAIMED_EVENTS)
        await self.conn.commit()
        return cursor.rowcount

    async def increment_count(self, task_id: str, session_id: str, updated_at: datetime) -> int:
        await self.conn.execute(_SQL_INCREMENT_COUNT, (task_id, session_id, updated_at.isoformat()))
        rows = await self.conn.execute_fetchall(_SQL_GET_COUNT, (task_id, session_id))
        await self.conn.commit()
        return int(rows[0]["count"])

    async def clear_count(self, task_id: str, session_id: str) -> None:
        await self.conn.execute(_SQL_CLEAR_COUNT, (task_id, session_id))
        await self.conn.commit()

    async def get_status(self, now: datetime) -> dict:
        now_iso = now.isoformat()
        task_row = (await self.conn.execute_fetchall(_SQL_STATUS_TASKS, (now_iso,)))[0]
        queue_row = (await self.conn.execute_fetchall(_SQL_STATUS_EVENT_QUEUE, (now_iso, now_iso, now_iso)))[0]
        count_row = (await self.conn.execute_fetchall(_SQL_STATUS_COUNT_STATE))[0]
        dead_letter_row = (await self.conn.execute_fetchall(_SQL_STATUS_DEAD_LETTER))[0]

        return {
            "observed_at": now_iso,
            "tasks": {
                "total": int(task_row["total"] or 0),
                "enabled": int(task_row["enabled"] or 0),
                "disabled": int(task_row["disabled"] or 0),
                "running": int(task_row["running"] or 0),
                "due": int(task_row["due"] or 0),
                "next_run_at": task_row["next_run_at"],
                "oldest_running_since": task_row["oldest_running_since"],
            },
            "event_queue": {
                "total": int(queue_row["total"] or 0),
                "ready": int(queue_row["ready"] or 0),
                "scheduled": int(queue_row["scheduled"] or 0),
                "claimed": int(queue_row["claimed"] or 0),
                "oldest_pending_created_at": queue_row["oldest_pending_created_at"],
                "next_attempt_at": queue_row["next_attempt_at"],
                "oldest_claimed_at": queue_row["oldest_claimed_at"],
            },
            "count_state": {
                "total": int(count_row["total"] or 0),
                "oldest_updated_at": count_row["oldest_updated_at"],
            },
            "dead_letters": {
                "total": int(dead_letter_row["total"] or 0),
                "oldest_failed_at": dead_letter_row["oldest_failed_at"],
                "newest_failed_at": dead_letter_row["newest_failed_at"],
            },
        }

    async def replace_active_suggestions(self, items: list[AutomationSuggestion]) -> None:
        await self.conn.execute("BEGIN")
        try:
            await self.conn.execute(_SQL_DELETE_ACTIVE_SUGGESTIONS)
            for item in items:
                await self.conn.execute(
                    _SQL_INSERT_SUGGESTION,
                    (
                        item.id,
                        item.name,
                        item.description,
                        item.prompt,
                        _serialize_triggers(item.triggers),
                        item.rationale,
                        json.dumps(item.evidence),
                        item.category,
                        item.icon,
                        item.status,
                        item.created_at.isoformat(),
                        item.source_automation_id,
                    ),
                )
            await self.conn.commit()
        except BaseException:
            await self.conn.rollback()
            raise

    async def list_active_suggestions(self) -> list[AutomationSuggestion]:
        rows = await self.conn.execute_fetchall(_SQL_LIST_ACTIVE_SUGGESTIONS)
        return [_row_to_suggestion(row) for row in rows]

    async def mark_suggestion_dismissed(self, suggestion_id: str) -> None:
        await self.conn.execute(_SQL_DISMISS_SUGGESTION, (suggestion_id,))
        await self.conn.commit()

    async def mark_suggestion_accepted(self, suggestion_id: str, source_automation_id: str) -> None:
        await self.conn.execute(_SQL_ACCEPT_SUGGESTION, (source_automation_id, suggestion_id))
        await self.conn.commit()

    async def list_excluded_signatures(self) -> list[str]:
        rows = await self.conn.execute_fetchall(_SQL_LIST_EXCLUDED_SIGNATURES)
        return [row["signature"] for row in rows]
