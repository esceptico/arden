import json
from datetime import UTC, datetime

import aiosqlite

from arden.events.internal import RunCompleted, RunFailed
from arden.outbox.events import (
    OUTBOX_AUTOMATION_SETTLED,
    OUTBOX_RUN_COMPLETED,
    OUTBOX_RUN_FAILED,
    AutomationSettled,
    automation_settled_payload,
    run_completed_payload,
    run_failed_payload,
)
from arden.outbox.models import OutboxEvent

_SCHEMA = """
CREATE TABLE IF NOT EXISTS outbox_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    aggregate_type TEXT,
    aggregate_id TEXT,
    payload TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at TEXT NOT NULL,
    locked_at TEXT,
    locked_by TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_outbox_events_ready
ON outbox_events(status, available_at, id);

CREATE INDEX IF NOT EXISTS idx_outbox_events_aggregate
ON outbox_events(aggregate_type, aggregate_id);
"""

_SQL_ENQUEUE = """
INSERT OR IGNORE INTO outbox_events (
    event_type,
    aggregate_type,
    aggregate_id,
    payload,
    idempotency_key,
    status,
    attempts,
    available_at,
    created_at,
    updated_at
)
VALUES (?, ?, ?, ?, ?, 'pending', 0, ?, ?, ?)
"""

_SQL_CLAIM_CANDIDATES = """
SELECT *
FROM outbox_events
WHERE status = 'pending'
  AND available_at <= ?
ORDER BY id
LIMIT ?
"""

_SQL_CLAIM = """
UPDATE outbox_events
SET status = 'running',
    attempts = attempts + 1,
    locked_at = ?,
    locked_by = ?,
    updated_at = ?
WHERE id = ?
  AND status = 'pending'
"""

_SQL_COMPLETE = """
UPDATE outbox_events
SET status = 'completed',
    locked_at = NULL,
    locked_by = NULL,
    updated_at = ?
WHERE id = ?
"""

_SQL_FAIL = """
UPDATE outbox_events
SET status = ?,
    available_at = ?,
    locked_at = NULL,
    locked_by = NULL,
    last_error = ?,
    updated_at = ?
WHERE id = ?
"""

_SQL_RELEASE_STALE = """
UPDATE outbox_events
SET status = 'pending',
    locked_at = NULL,
    locked_by = NULL,
    updated_at = ?
WHERE status = 'running'
  AND locked_at < ?
"""

_SQL_REPLAY_DEAD = """
UPDATE outbox_events
SET status = 'pending',
    attempts = 0,
    available_at = ?,
    locked_at = NULL,
    locked_by = NULL,
    last_error = NULL,
    updated_at = ?
WHERE status = 'dead'
  AND id IN ({placeholders})
"""

_SQL_PRUNE_COMPLETED = """
DELETE FROM outbox_events
WHERE id IN (
    SELECT id
    FROM outbox_events
    WHERE status = 'completed'
      AND updated_at < ?
    ORDER BY updated_at ASC, id ASC
    LIMIT ?
)
"""

_SQL_STATUS_COUNTS = """
SELECT status, COUNT(*) AS count
FROM outbox_events
GROUP BY status
"""

_SQL_STATUS_COUNTS_BY_TYPE = """
SELECT event_type, status, COUNT(*) AS count
FROM outbox_events
GROUP BY event_type, status
ORDER BY event_type, status
"""

_SQL_STATUS_SUMMARY = """
SELECT
    COUNT(*) AS total,
    SUM(CASE WHEN status = 'pending' AND available_at <= ? THEN 1 ELSE 0 END) AS ready,
    SUM(CASE WHEN status = 'pending' AND available_at > ? THEN 1 ELSE 0 END) AS scheduled,
    MIN(CASE WHEN status = 'pending' THEN created_at END) AS oldest_pending_created_at,
    MIN(CASE WHEN status = 'pending' THEN available_at END) AS next_pending_available_at,
    MIN(CASE WHEN status = 'running' THEN locked_at END) AS oldest_running_locked_at,
    MAX(CASE WHEN status = 'dead' THEN updated_at END) AS newest_dead_updated_at
FROM outbox_events
"""

_SQL_RECENT_DEAD = """
SELECT id, event_type, aggregate_type, aggregate_id, attempts, last_error, created_at, updated_at
FROM outbox_events
WHERE status = 'dead'
ORDER BY updated_at DESC, id DESC
LIMIT ?
"""

_STATUS_KEYS = ("pending", "running", "completed", "dead")


def _now() -> datetime:
    return datetime.now(UTC)


def _format_dt(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.isoformat()


def _parse_dt(raw: str | None) -> datetime | None:
    return datetime.fromisoformat(raw) if raw else None


def _row_data(row: aiosqlite.Row) -> dict:
    return dict(row)


def _placeholders(count: int) -> str:
    return ",".join("?" for _ in range(count))


def _event_from_data(data: dict) -> OutboxEvent:
    return OutboxEvent(
        id=int(data["id"]),
        event_type=data["event_type"],
        aggregate_type=data["aggregate_type"],
        aggregate_id=data["aggregate_id"],
        payload=json.loads(data["payload"]),
        idempotency_key=data["idempotency_key"],
        status=data["status"],
        attempts=int(data["attempts"]),
        available_at=_parse_dt(data["available_at"]),
        locked_at=_parse_dt(data["locked_at"]),
        locked_by=data["locked_by"],
        last_error=data["last_error"],
        created_at=_parse_dt(data["created_at"]),
        updated_at=_parse_dt(data["updated_at"]),
    )


class OutboxStore:
    def __init__(self, conn: aiosqlite.Connection):
        self.conn = conn

    async def init_schema(self) -> None:
        await self.conn.executescript(_SCHEMA)
        await self.conn.commit()

    async def enqueue(
        self,
        *,
        event_type: str,
        payload: dict,
        idempotency_key: str,
        aggregate_type: str | None = None,
        aggregate_id: str | None = None,
        available_at: datetime | None = None,
    ) -> bool:
        inserted = await self.enqueue_without_commit(
            event_type=event_type,
            payload=payload,
            idempotency_key=idempotency_key,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            available_at=available_at,
        )
        await self.conn.commit()
        return inserted

    async def enqueue_without_commit(
        self,
        *,
        event_type: str,
        payload: dict,
        idempotency_key: str,
        aggregate_type: str | None = None,
        aggregate_id: str | None = None,
        available_at: datetime | None = None,
    ) -> bool:
        now = _now()
        available = available_at or now
        cursor = await self.conn.execute(
            _SQL_ENQUEUE,
            (
                event_type,
                aggregate_type,
                aggregate_id,
                json.dumps(payload),
                idempotency_key,
                _format_dt(available),
                _format_dt(now),
                _format_dt(now),
            ),
        )
        return cursor.rowcount > 0

    async def enqueue_run_completed(self, event: RunCompleted) -> bool:
        return await self.enqueue(
            event_type=OUTBOX_RUN_COMPLETED,
            aggregate_type="run",
            aggregate_id=event.run_id,
            payload=run_completed_payload(event),
            idempotency_key=f"{OUTBOX_RUN_COMPLETED}:{event.run_id}",
        )

    async def enqueue_run_failed(self, event: RunFailed) -> bool:
        return await self.enqueue(
            event_type=OUTBOX_RUN_FAILED,
            aggregate_type="run",
            aggregate_id=event.run_id,
            payload=run_failed_payload(event),
            idempotency_key=f"{OUTBOX_RUN_FAILED}:{event.run_id}",
        )

    async def enqueue_automation_settled_in_transaction(
        self,
        *,
        automation_run_id: int,
        task_id: str,
        success: bool,
    ) -> bool:
        event = AutomationSettled(
            automation_run_id=automation_run_id,
            task_id=task_id,
            success=success,
        )
        return await self.enqueue_without_commit(
            event_type=OUTBOX_AUTOMATION_SETTLED,
            aggregate_type="automation_run",
            aggregate_id=str(automation_run_id),
            payload=automation_settled_payload(event),
            idempotency_key=f"{OUTBOX_AUTOMATION_SETTLED}:{automation_run_id}",
        )

    async def claim_batch(self, *, worker_id: str, limit: int, now: datetime | None = None) -> list[OutboxEvent]:
        claimed_at = now or _now()
        rows = await self.conn.execute_fetchall(
            _SQL_CLAIM_CANDIDATES,
            (_format_dt(claimed_at), limit),
        )

        events: list[OutboxEvent] = []
        for row in rows:
            data = _row_data(row)
            cursor = await self.conn.execute(
                _SQL_CLAIM,
                (
                    _format_dt(claimed_at),
                    worker_id,
                    _format_dt(claimed_at),
                    data["id"],
                ),
            )
            if cursor.rowcount <= 0:
                continue
            data["status"] = "running"
            data["attempts"] = int(data["attempts"]) + 1
            data["locked_at"] = _format_dt(claimed_at)
            data["locked_by"] = worker_id
            data["updated_at"] = _format_dt(claimed_at)
            events.append(_event_from_data(data))

        await self.conn.commit()
        return events

    async def mark_completed(self, event_id: int) -> None:
        now = _now()
        await self.conn.execute(_SQL_COMPLETE, (_format_dt(now), event_id))
        await self.conn.commit()

    async def mark_failed(
        self,
        event_id: int,
        *,
        error: str,
        retry_at: datetime | None,
        dead: bool = False,
    ) -> None:
        now = _now()
        status = "dead" if dead else "pending"
        available_at = retry_at or now
        await self.conn.execute(
            _SQL_FAIL,
            (
                status,
                _format_dt(available_at),
                error[:2000],
                _format_dt(now),
                event_id,
            ),
        )
        await self.conn.commit()

    async def release_stale_running(self, locked_before: datetime) -> int:
        now = _now()
        cursor = await self.conn.execute(
            _SQL_RELEASE_STALE,
            (_format_dt(now), _format_dt(locked_before)),
        )
        await self.conn.commit()
        return cursor.rowcount

    async def replay_dead(self, event_ids: list[int], *, now: datetime | None = None) -> dict:
        ids = list(dict.fromkeys(event_ids))
        if not ids:
            return {"requested": [], "replayed": [], "missing": [], "skipped": []}

        rows = await self.conn.execute_fetchall(
            f"SELECT id, status FROM outbox_events WHERE id IN ({_placeholders(len(ids))})",
            tuple(ids),
        )
        statuses = {int(row["id"]): row["status"] for row in rows}
        replay_ids = [event_id for event_id in ids if statuses.get(event_id) == "dead"]
        if replay_ids:
            replayed_at = now or _now()
            await self.conn.execute(
                _SQL_REPLAY_DEAD.format(placeholders=_placeholders(len(replay_ids))),
                (
                    _format_dt(replayed_at),
                    _format_dt(replayed_at),
                    *replay_ids,
                ),
            )
            await self.conn.commit()

        return {
            "requested": ids,
            "replayed": replay_ids,
            "missing": [event_id for event_id in ids if event_id not in statuses],
            "skipped": [
                {"id": event_id, "status": statuses[event_id]}
                for event_id in ids
                if event_id in statuses and statuses[event_id] != "dead"
            ],
        }

    async def prune_completed(self, *, before: datetime, limit: int) -> int:
        cursor = await self.conn.execute(
            _SQL_PRUNE_COMPLETED,
            (_format_dt(before), limit),
        )
        await self.conn.commit()
        return cursor.rowcount

    async def get_status(self, *, now: datetime | None = None, recent_dead_limit: int = 10) -> dict:
        observed_at = now or _now()
        status_rows = await self.conn.execute_fetchall(_SQL_STATUS_COUNTS)
        type_rows = await self.conn.execute_fetchall(_SQL_STATUS_COUNTS_BY_TYPE)
        summary_row = (
            await self.conn.execute_fetchall(
                _SQL_STATUS_SUMMARY,
                (_format_dt(observed_at), _format_dt(observed_at)),
            )
        )[0]
        dead_rows = await self.conn.execute_fetchall(_SQL_RECENT_DEAD, (recent_dead_limit,))

        by_status = dict.fromkeys(_STATUS_KEYS, 0)
        for row in status_rows:
            by_status[row["status"]] = int(row["count"])

        by_event_type: dict[str, dict[str, int]] = {}
        for row in type_rows:
            event_counts = by_event_type.setdefault(row["event_type"], dict.fromkeys(_STATUS_KEYS, 0))
            event_counts[row["status"]] = int(row["count"])

        return {
            "observed_at": _format_dt(observed_at),
            "total": int(summary_row["total"] or 0),
            "ready": int(summary_row["ready"] or 0),
            "scheduled": int(summary_row["scheduled"] or 0),
            "by_status": by_status,
            "by_event_type": by_event_type,
            "oldest_pending_created_at": summary_row["oldest_pending_created_at"],
            "next_pending_available_at": summary_row["next_pending_available_at"],
            "oldest_running_locked_at": summary_row["oldest_running_locked_at"],
            "newest_dead_updated_at": summary_row["newest_dead_updated_at"],
            "recent_dead": [
                {
                    "id": int(row["id"]),
                    "event_type": row["event_type"],
                    "aggregate_type": row["aggregate_type"],
                    "aggregate_id": row["aggregate_id"],
                    "attempts": int(row["attempts"]),
                    "last_error": row["last_error"],
                    "created_at": row["created_at"],
                    "updated_at": row["updated_at"],
                }
                for row in dead_rows
            ],
        }
