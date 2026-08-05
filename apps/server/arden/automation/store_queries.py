AUTOMATION_COLUMNS = (
    "task_id, automation_ref, name, description, description_source, prompt, model, triggers, enabled, "
    "created_at, last_run_at, next_run_at, last_result, running_since, "
    "auto_approve, handler, builtin, cooldown_minutes, "
    "kind, max_iterations, iteration_count, stop_when, max_age_days, "
    "thread_id, read_history, parent_automation_id, idempotency_key, idempotency_scope, tool_scope, "
    "triggers_source"
)

SAVE = f"""
INSERT INTO scheduled_tasks ({AUTOMATION_COLUMNS})
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(task_id) DO UPDATE SET
    name = excluded.name,
    description = excluded.description,
    description_source = excluded.description_source,
    prompt = excluded.prompt,
    model = excluded.model,
    triggers = excluded.triggers,
    enabled = excluded.enabled,
    created_at = excluded.created_at,
    last_run_at = excluded.last_run_at,
    next_run_at = excluded.next_run_at,
    last_result = excluded.last_result,
    running_since = excluded.running_since,
    auto_approve = excluded.auto_approve,
    handler = excluded.handler,
    builtin = excluded.builtin,
    cooldown_minutes = excluded.cooldown_minutes,
    kind = excluded.kind,
    max_iterations = excluded.max_iterations,
    iteration_count = excluded.iteration_count,
    stop_when = excluded.stop_when,
    max_age_days = excluded.max_age_days,
    thread_id = excluded.thread_id,
    read_history = excluded.read_history,
    parent_automation_id = excluded.parent_automation_id,
    idempotency_key = excluded.idempotency_key,
    idempotency_scope = excluded.idempotency_scope,
    tool_scope = excluded.tool_scope,
    triggers_source = excluded.triggers_source
WHERE scheduled_tasks.automation_ref = excluded.automation_ref
"""

INSERT = f"""
INSERT INTO scheduled_tasks ({AUTOMATION_COLUMNS})
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

GET_BY_ID = f"SELECT {AUTOMATION_COLUMNS} FROM scheduled_tasks WHERE task_id = ?"
GET_BY_REF = f"SELECT {AUTOMATION_COLUMNS} FROM scheduled_tasks WHERE automation_ref = ?"

LIST_ALL = f"SELECT {AUTOMATION_COLUMNS} FROM scheduled_tasks ORDER BY created_at"

LIST_RUNNING = f"""
SELECT {AUTOMATION_COLUMNS} FROM scheduled_tasks
WHERE running_since IS NOT NULL
ORDER BY running_since
"""

LIST_DUE = f"""
SELECT {AUTOMATION_COLUMNS} FROM scheduled_tasks
WHERE enabled = 1 AND next_run_at <= ? AND running_since IS NULL
ORDER BY next_run_at
"""

LIST_EVENT_TRIGGERED = f"""
SELECT {AUTOMATION_COLUMNS} FROM scheduled_tasks
WHERE enabled = 1
  AND EXISTS (
    SELECT 1 FROM json_each(triggers)
    WHERE json_extract(value, '$.type') = 'event'
      AND json_extract(value, '$.event_type') = ?
  )
"""

LIST_BY_TRIGGER_TYPE = f"""
SELECT {AUTOMATION_COLUMNS} FROM scheduled_tasks
WHERE enabled = 1
  AND running_since IS NULL
  AND EXISTS (
    SELECT 1 FROM json_each(triggers)
    WHERE json_extract(value, '$.type') = ?
  )
"""

LIST_MESSAGE_TRIGGERED = f"""
SELECT {AUTOMATION_COLUMNS} FROM scheduled_tasks
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

LIST_WATCHED_SLACK_CHANNELS = """
SELECT DISTINCT json_extract(c.value, '$.id') AS channel_id
FROM scheduled_tasks, json_each(triggers) AS t, json_each(t.value, '$.channels') AS c
WHERE enabled = 1
  AND json_extract(t.value, '$.type') = 'message'
  AND json_extract(t.value, '$.source') = 'slack'
"""

UPDATE_LAST_RUN = """
UPDATE scheduled_tasks
SET last_run_at = ?, next_run_at = ?, last_result = ?
WHERE task_id = ?
"""

UPDATE_LAST_RUN_KEEP_RESULT = """
UPDATE scheduled_tasks
SET last_run_at = ?, next_run_at = ?
WHERE task_id = ?
"""

SET_NEXT_RUN = """
UPDATE scheduled_tasks SET next_run_at = ? WHERE task_id = ?
"""

SET_NEXT_RUN_IF_ENABLED = """
UPDATE scheduled_tasks SET next_run_at = ? WHERE task_id = ? AND enabled = 1
"""

SET_EARLIER_NEXT_RUN_IF_ENABLED = """
UPDATE scheduled_tasks
SET next_run_at = ?
WHERE task_id = ?
  AND enabled = 1
  AND (next_run_at IS NULL OR next_run_at > ?)
"""

COMPARE_AND_SET_NEXT_RUN = """
UPDATE scheduled_tasks
SET next_run_at = ?
WHERE task_id = ? AND next_run_at IS ?
"""

UPDATE_LAST_RUN_IF_NEXT_RUN = """
UPDATE scheduled_tasks
SET last_run_at = ?,
    next_run_at = CASE WHEN next_run_at IS ? THEN ? ELSE next_run_at END,
    last_result = ?
WHERE task_id = ?
"""

UPDATE_LAST_RUN_KEEP_RESULT_IF_NEXT_RUN = """
UPDATE scheduled_tasks
SET last_run_at = ?,
    next_run_at = CASE WHEN next_run_at IS ? THEN ? ELSE next_run_at END
WHERE task_id = ?
"""

RECORD_RUN_FAILURE = """
UPDATE scheduled_tasks
SET next_run_at = ?,
    last_result = ?
WHERE task_id = ? AND enabled = 1
"""

TRY_MARK_RUNNING = """
UPDATE scheduled_tasks
SET running_since = ?
WHERE task_id = ?
  AND enabled = 1
  AND running_since IS NULL
"""

CLEAR_RUNNING = "UPDATE scheduled_tasks SET running_since = NULL WHERE task_id = ?"

INSERT_RUN = """
INSERT INTO automation_runs (task_id, automation_ref, started_at, status)
SELECT task_id, automation_ref, ?, 'running'
FROM scheduled_tasks
WHERE task_id = ?
"""
DELETE_UNSTARTED_RUN = """
DELETE FROM automation_runs
WHERE id = ? AND task_id = ? AND status = 'running' AND chat_run_id IS NULL
"""
DEFER_TASK = """
UPDATE scheduled_tasks
SET running_since = NULL,
    next_run_at = CASE WHEN next_run_at IS ? THEN ? ELSE next_run_at END
WHERE task_id = ?
"""
BIND_DETACHED_CHAT_RUN = """
UPDATE automation_runs
SET chat_run_id = ?, chat_session_id = ?
WHERE id = ? AND task_id = ? AND status = 'running'
"""
BIND_INTERRUPTED_CHAT_RUN = """
UPDATE automation_runs
SET chat_run_id = ?, chat_session_id = ?
WHERE id = (
    SELECT id
    FROM automation_runs
    WHERE task_id = ? AND status = 'running' AND chat_run_id IS NULL
    ORDER BY id DESC
    LIMIT 1
)
  AND task_id = ?
  AND status = 'running'
  AND chat_run_id IS NULL
"""
REFRESH_DETACHED_CHAT_RUN = """
UPDATE automation_runs
SET started_at = ?
WHERE task_id = ?
  AND chat_run_id = ?
  AND chat_session_id = ?
  AND status = 'running'
"""
LATEST_RUNNING_RUN_ID = (
    "SELECT id FROM automation_runs WHERE task_id = ? AND status = 'running' ORDER BY id DESC LIMIT 1"
)
RUNNING_DETACHED_CHAT_RUN = """
SELECT chat_run_id, chat_session_id, started_at
FROM automation_runs
WHERE task_id = ? AND status = 'running' AND chat_run_id IS NOT NULL
ORDER BY id DESC
LIMIT 1
"""
HAS_UNBOUND_RUNNING_RUN = """
SELECT 1
FROM automation_runs
WHERE task_id = ? AND status = 'running' AND chat_run_id IS NULL
LIMIT 1
"""
SETTLE_RUN = """
UPDATE automation_runs
SET ended_at = ?, status = ?, result = ?, error = ?
WHERE id = ? AND task_id = ? AND status = 'running'
"""
FAIL_ORPHANED_RUNS = """
UPDATE automation_runs
SET status = 'failed', error = ?, ended_at = ?
WHERE status = 'running' AND chat_run_id IS NULL
"""
LIST_RUNS = (
    "SELECT id, task_id, chat_run_id, chat_session_id, started_at, ended_at, status, result, error "
    "FROM automation_runs WHERE task_id = ? ORDER BY started_at DESC, id DESC LIMIT ?"
)
RECENT_STATUSES = "SELECT status FROM automation_runs WHERE task_id = ? ORDER BY started_at DESC, id DESC LIMIT ?"
LATEST_RUNS = (
    "SELECT id, task_id, chat_run_id, chat_session_id, started_at, ended_at, status, result, error "
    "FROM automation_runs WHERE id IN (SELECT max(id) FROM automation_runs GROUP BY task_id)"
)

DELETE = "DELETE FROM scheduled_tasks WHERE task_id = ?"
DELETE_GLOBAL_IDEMPOTENCY_CLAIMS_BY_TASK = """
DELETE FROM automation_idempotency_claims
WHERE automation_task_id = ? AND scope = 'global'
"""
RECLAIM_ORPHAN_GLOBAL_IDEMPOTENCY_CLAIM = """
DELETE FROM automation_idempotency_claims
WHERE claim_id = ?
  AND scope = 'global'
  AND automation_task_id = ?
  AND NOT EXISTS (SELECT 1 FROM scheduled_tasks WHERE task_id = ?)
"""

SET_ENABLED = "UPDATE scheduled_tasks SET enabled = ? WHERE task_id = ?"
DISABLE_AND_CLEAR_NEXT_RUN = "UPDATE scheduled_tasks SET enabled = 0, next_run_at = NULL WHERE task_id = ?"

DISABLE_BY_PARENT = """
UPDATE scheduled_tasks SET enabled = 0
WHERE parent_automation_id = ? AND enabled = 1
"""

SET_AUTO_APPROVE = "UPDATE scheduled_tasks SET auto_approve = ? WHERE task_id = ?"

UPDATE_METADATA = """
UPDATE scheduled_tasks
SET name = ?, description = ?, description_source = ?, prompt = ?, model = ?, triggers = ?,
    enabled = ?, next_run_at = ?, auto_approve = ?, handler = ?,
    cooldown_minutes = ?,
    max_iterations = ?, stop_when = ?,
    max_age_days = ?,
    thread_id = ?, read_history = ?,
    parent_automation_id = ?, idempotency_key = ?, idempotency_scope = ?, tool_scope = ?,
    triggers_source = ?
WHERE task_id = ?
"""

LIST_LOOPS_BY_SESSION = f"""
SELECT {AUTOMATION_COLUMNS} FROM scheduled_tasks
WHERE kind = 'loop' AND thread_id = ?
ORDER BY created_at
"""

# Kind-agnostic counterpart used by the scheduler's run-completed fast path.
# A session-bound automation is any row that targets a session via thread_id,
# regardless of kind. Channel automations created via
# `service.create(thread_id=...)` have kind="automation" and still need to
# fire through the post dispatcher.
LIST_SESSION_BOUND_BY_SESSION = f"""
SELECT {AUTOMATION_COLUMNS} FROM scheduled_tasks
WHERE thread_id = ? AND enabled = 1
ORDER BY created_at
"""

LIST_ALL_SESSION_BOUND_BY_SESSION = f"""
SELECT {AUTOMATION_COLUMNS} FROM scheduled_tasks
WHERE thread_id = ?
ORDER BY created_at
"""

LIST_BY_PARENT = f"""
SELECT {AUTOMATION_COLUMNS} FROM scheduled_tasks
WHERE parent_automation_id = ?
ORDER BY created_at, task_id
"""

INCREMENT_ITERATION = """
UPDATE scheduled_tasks
SET iteration_count = iteration_count + 1
WHERE task_id = ?
"""

CLEAR_ORPHANED_RUNNING = """
UPDATE scheduled_tasks
SET running_since = NULL
WHERE running_since IS NOT NULL
  AND task_id NOT IN (
      SELECT task_id
      FROM automation_runs
      WHERE status = 'running' AND chat_run_id IS NOT NULL
  )
"""

CLAIM_EVENT = """
INSERT OR IGNORE INTO automation_event_dedupe (task_id, event_key, seen_at)
VALUES (?, ?, ?)
"""

TRY_CLAIM_IDEMPOTENCY = """
INSERT OR IGNORE INTO automation_idempotency_claims (
    claim_id, scope, key, parent_automation_id, parent_fire_at,
    attempt_n, claimed_at, automation_task_id
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""

LIST_CLAIMS_FOR_PARENT = """
SELECT claim_id, scope, key, parent_automation_id, parent_fire_at, attempt_n,
       claimed_at, automation_task_id
FROM automation_idempotency_claims
WHERE parent_automation_id = ?
ORDER BY claimed_at
"""

EVICT_EVENT_CLAIMS = "DELETE FROM automation_event_dedupe WHERE seen_at < ?"

ENQUEUE_EVENT = """
INSERT INTO automation_event_queue (task_id, event_key, context, created_at)
VALUES (?, ?, ?, ?)
"""

LIST_TASKS_WITH_PENDING_EVENTS = """
SELECT DISTINCT task_id
FROM automation_event_queue
WHERE claimed_at IS NULL
"""

CLAIM_NEXT_EVENT_CANDIDATE = """
SELECT id, context, attempt_count
FROM automation_event_queue
WHERE task_id = ?
  AND claimed_at IS NULL
  AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
ORDER BY id
LIMIT 1
"""

CLAIM_EVENT_QUEUE_ROW = """
UPDATE automation_event_queue
SET claimed_at = ?
WHERE id = ? AND claimed_at IS NULL
"""

GET_CLAIMED_EVENT = """
SELECT id, attempt_count
FROM automation_event_queue
WHERE task_id = ? AND claimed_at IS NOT NULL
ORDER BY id
LIMIT 1
"""

COMPLETE_EVENT = "DELETE FROM automation_event_queue WHERE id = ?"

DEAD_LETTER_EVENT = """
INSERT INTO automation_event_dead_letter (
    original_queue_id, task_id, event_key, context, created_at, failed_at, attempt_count, last_error
)
SELECT id, task_id, event_key, context, created_at, ?, attempt_count + 1, ?
FROM automation_event_queue
WHERE id = ?
"""

FAIL_EVENT = """
UPDATE automation_event_queue
SET claimed_at = NULL,
    attempt_count = attempt_count + 1,
    last_error = ?,
    next_attempt_at = ?
WHERE id = ?
"""

DELETE_DEDUPE_BY_TASK = "DELETE FROM automation_event_dedupe WHERE task_id = ?"

DELETE_QUEUE_BY_TASK = "DELETE FROM automation_event_queue WHERE task_id = ?"

RELEASE_ALL_CLAIMED_EVENTS = "UPDATE automation_event_queue SET claimed_at = NULL WHERE claimed_at IS NOT NULL"

INCREMENT_COUNT = """
INSERT INTO automation_count_state (task_id, session_id, count, updated_at)
VALUES (?, ?, 1, ?)
ON CONFLICT(task_id, session_id) DO UPDATE SET
    count = count + 1,
    updated_at = excluded.updated_at
"""

GET_COUNT = "SELECT count FROM automation_count_state WHERE task_id = ? AND session_id = ?"

CLEAR_COUNT = "DELETE FROM automation_count_state WHERE task_id = ? AND session_id = ?"

DELETE_COUNTS_BY_TASK = "DELETE FROM automation_count_state WHERE task_id = ?"

STATUS_TASKS = """
SELECT
    COUNT(*) AS total,
    COALESCE(SUM(CASE WHEN enabled = 1 THEN 1 ELSE 0 END), 0) AS enabled,
    COALESCE(SUM(CASE WHEN enabled = 0 THEN 1 ELSE 0 END), 0) AS disabled,
    COALESCE(SUM(CASE WHEN running_since IS NOT NULL THEN 1 ELSE 0 END), 0) AS running,
    COALESCE(SUM(
        CASE
            WHEN enabled = 1
              AND running_since IS NULL
              AND next_run_at IS NOT NULL
              AND next_run_at <= ?
            THEN 1 ELSE 0
        END
    ), 0) AS due,
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

STATUS_EVENT_QUEUE = """
SELECT
    COUNT(*) AS total,
    COALESCE(SUM(
        CASE
            WHEN claimed_at IS NULL
              AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
            THEN 1 ELSE 0
        END
    ), 0) AS ready,
    COALESCE(SUM(
        CASE
            WHEN claimed_at IS NULL
              AND next_attempt_at > ?
            THEN 1 ELSE 0
        END
    ), 0) AS scheduled,
    COALESCE(SUM(CASE WHEN claimed_at IS NOT NULL THEN 1 ELSE 0 END), 0) AS claimed,
    MIN(CASE WHEN claimed_at IS NULL THEN created_at END) AS oldest_pending_created_at,
    MIN(CASE WHEN claimed_at IS NULL AND next_attempt_at > ? THEN next_attempt_at END) AS next_attempt_at,
    MIN(CASE WHEN claimed_at IS NOT NULL THEN claimed_at END) AS oldest_claimed_at
FROM automation_event_queue
"""

STATUS_COUNT_STATE = """
SELECT
    COUNT(*) AS total,
    MIN(updated_at) AS oldest_updated_at
FROM automation_count_state
"""

STATUS_DEAD_LETTER = """
SELECT
    COUNT(*) AS total,
    MIN(failed_at) AS oldest_failed_at,
    MAX(failed_at) AS newest_failed_at
FROM automation_event_dead_letter
"""
