import json
import re
from datetime import UTC, datetime

import aiosqlite

from ntrp.areas.work_models import AreaOutcome, AreaWorkEvent, AreaWorkItem, AreaWorkSnapshot

_KEY = re.compile(r"^[a-z0-9][a-z0-9_-]{0,79}$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS area_work_outcomes (
    outcome_id TEXT PRIMARY KEY,
    area_id TEXT NOT NULL REFERENCES areas(area_id) ON DELETE CASCADE,
    stable_key TEXT NOT NULL,
    title TEXT NOT NULL,
    success_criteria TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active', 'paused', 'completed', 'cancelled')),
    priority INTEGER NOT NULL CHECK(priority BETWEEN 1 AND 5),
    source TEXT NOT NULL CHECK(source IN ('inferred', 'user', 'migration')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(area_id, stable_key)
);
CREATE INDEX IF NOT EXISTS idx_area_work_outcomes_area_status
ON area_work_outcomes(area_id, status, priority DESC, updated_at DESC);

CREATE TABLE IF NOT EXISTS area_work_items (
    item_id TEXT PRIMARY KEY,
    area_id TEXT NOT NULL REFERENCES areas(area_id) ON DELETE CASCADE,
    stable_key TEXT NOT NULL,
    outcome_id TEXT REFERENCES area_work_outcomes(outcome_id) ON DELETE CASCADE,
    kind TEXT NOT NULL CHECK(kind IN ('loop', 'action', 'blocker')),
    text TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('active', 'in_progress', 'completed', 'cancelled')),
    owner TEXT NOT NULL CHECK(owner IN ('custodian', 'user', 'external')),
    due_at TEXT,
    next_attempt_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT,
    UNIQUE(area_id, stable_key)
);
CREATE INDEX IF NOT EXISTS idx_area_work_items_area_status
ON area_work_items(area_id, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS area_work_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    area_id TEXT NOT NULL REFERENCES areas(area_id) ON DELETE CASCADE,
    outcome_id TEXT REFERENCES area_work_outcomes(outcome_id) ON DELETE SET NULL,
    item_id TEXT REFERENCES area_work_items(item_id) ON DELETE SET NULL,
    run_ref TEXT,
    operation_index INTEGER,
    event_type TEXT NOT NULL,
    summary TEXT NOT NULL,
    source_refs TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    UNIQUE(run_ref, operation_index)
);
CREATE INDEX IF NOT EXISTS idx_area_work_events_area_created
ON area_work_events(area_id, created_at DESC);

CREATE TABLE IF NOT EXISTS area_work_reports (
    run_ref TEXT PRIMARY KEY,
    area_id TEXT NOT NULL REFERENCES areas(area_id) ON DELETE CASCADE,
    applied_at TEXT NOT NULL
);
"""


class AreaWorkConflict(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _key(value: str) -> str:
    if not _KEY.fullmatch(value):
        raise ValueError("stable key must be lowercase alphanumeric with optional '-' or '_'")
    return value


class AreaWorkStore:
    def __init__(self, conn: aiosqlite.Connection, read_conn: aiosqlite.Connection | None = None) -> None:
        self.conn = conn
        self.read_conn = read_conn or conn

    async def init_schema(self) -> None:
        await self.conn.executescript(_SCHEMA)
        await self.conn.commit()

    async def snapshot(self, area_id: str) -> AreaWorkSnapshot:
        outcomes = await self.read_conn.execute_fetchall(
            "SELECT * FROM area_work_outcomes WHERE area_id = ? "
            "ORDER BY priority DESC, updated_at DESC",
            (area_id,),
        )
        items = await self.read_conn.execute_fetchall(
            "SELECT * FROM area_work_items WHERE area_id = ? "
            "ORDER BY CASE status WHEN 'in_progress' THEN 0 WHEN 'active' THEN 1 ELSE 2 END, updated_at DESC",
            (area_id,),
        )
        events = await self.read_conn.execute_fetchall(
            "SELECT * FROM area_work_events WHERE area_id = ? ORDER BY created_at DESC, event_id DESC LIMIT 50",
            (area_id,),
        )
        return AreaWorkSnapshot(
            outcomes=[AreaOutcome.model_validate(dict(row)) for row in outcomes],
            work_items=[AreaWorkItem.model_validate(dict(row)) for row in items],
            events=[self._event(row) for row in events],
        )

    async def create_outcome(
        self,
        area_id: str,
        *,
        key: str,
        title: str,
        success_criteria: str,
        priority: int,
        source: str,
    ) -> AreaOutcome:
        stable_key = _key(key)
        now = _now()
        outcome_id = f"outcome:{area_id}:{stable_key}"
        await self.conn.execute(
            "INSERT INTO area_work_outcomes "
            "(outcome_id, area_id, stable_key, title, success_criteria, status, priority, source, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)",
            (outcome_id, area_id, stable_key, title.strip(), success_criteria.strip(), priority, source, now, now),
        )
        await self.conn.commit()
        return await self._get_outcome(area_id, stable_key)

    async def update_outcome(
        self,
        area_id: str,
        key: str,
        *,
        expected_updated_at: str | None = None,
        **patch: object,
    ) -> AreaOutcome | None:
        stable_key = _key(key)
        current = await self._get_outcome(area_id, stable_key, required=False)
        if current is None:
            return None
        if expected_updated_at is not None and current.updated_at != expected_updated_at:
            raise AreaWorkConflict("Outcome changed since it was loaded")
        allowed = {"title", "success_criteria", "status", "priority"}
        unknown = set(patch) - allowed
        if unknown:
            raise ValueError(f"Unsupported outcome fields: {sorted(unknown)}")
        if not patch:
            return current
        values = dict(patch)
        now = _now()
        values["updated_at"] = now
        if values.get("status") in {"completed", "cancelled"}:
            values["completed_at"] = now
        elif "status" in values:
            values["completed_at"] = None
        assignments = ", ".join(f"{name} = ?" for name in values)
        await self.conn.execute(
            f"UPDATE area_work_outcomes SET {assignments} WHERE area_id = ? AND stable_key = ?",
            (*values.values(), area_id, stable_key),
        )
        await self.conn.commit()
        return await self._get_outcome(area_id, stable_key)

    async def create_work_item(
        self,
        area_id: str,
        *,
        key: str,
        outcome_key: str | None,
        kind: str,
        text: str,
        owner: str,
        due_at: str | None = None,
        next_attempt_at: str | None = None,
    ) -> AreaWorkItem:
        stable_key = _key(key)
        outcome_id = None
        if outcome_key is not None:
            outcome = await self._get_outcome(area_id, _key(outcome_key), required=False)
            if outcome is None:
                raise KeyError(f"Unknown outcome '{outcome_key}' in Area '{area_id}'")
            outcome_id = outcome.outcome_id
        now = _now()
        item_id = f"work:{area_id}:{stable_key}"
        await self.conn.execute(
            "INSERT INTO area_work_items "
            "(item_id, area_id, stable_key, outcome_id, kind, text, status, owner, due_at, next_attempt_at, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)",
            (item_id, area_id, stable_key, outcome_id, kind, text.strip(), owner, due_at, next_attempt_at, now, now),
        )
        await self.conn.commit()
        return await self._get_work_item(area_id, stable_key)

    async def update_work_item(
        self,
        area_id: str,
        key: str,
        *,
        expected_updated_at: str | None = None,
        **patch: object,
    ) -> AreaWorkItem | None:
        stable_key = _key(key)
        current = await self._get_work_item(area_id, stable_key, required=False)
        if current is None:
            return None
        if expected_updated_at is not None and current.updated_at != expected_updated_at:
            raise AreaWorkConflict("Work item changed since it was loaded")
        allowed = {"text", "status", "owner", "due_at", "next_attempt_at"}
        unknown = set(patch) - allowed
        if unknown:
            raise ValueError(f"Unsupported work item fields: {sorted(unknown)}")
        if not patch:
            return current
        values = dict(patch)
        now = _now()
        values["updated_at"] = now
        if values.get("status") in {"completed", "cancelled"}:
            values["completed_at"] = now
        elif "status" in values:
            values["completed_at"] = None
        assignments = ", ".join(f"{name} = ?" for name in values)
        await self.conn.execute(
            f"UPDATE area_work_items SET {assignments} WHERE area_id = ? AND stable_key = ?",
            (*values.values(), area_id, stable_key),
        )
        await self.conn.commit()
        return await self._get_work_item(area_id, stable_key)

    async def _get_outcome(self, area_id: str, key: str, *, required: bool = True) -> AreaOutcome | None:
        rows = await self.read_conn.execute_fetchall(
            "SELECT * FROM area_work_outcomes WHERE area_id = ? AND stable_key = ?",
            (area_id, key),
        )
        if not rows:
            if required:
                raise KeyError(key)
            return None
        return AreaOutcome.model_validate(dict(rows[0]))

    async def _get_work_item(self, area_id: str, key: str, *, required: bool = True) -> AreaWorkItem | None:
        rows = await self.read_conn.execute_fetchall(
            "SELECT * FROM area_work_items WHERE area_id = ? AND stable_key = ?",
            (area_id, key),
        )
        if not rows:
            if required:
                raise KeyError(key)
            return None
        return AreaWorkItem.model_validate(dict(rows[0]))

    @staticmethod
    def _event(row: aiosqlite.Row) -> AreaWorkEvent:
        data = dict(row)
        data["source_refs"] = json.loads(data["source_refs"] or "[]")
        return AreaWorkEvent.model_validate(data)
