import json
import re
from datetime import UTC, datetime, timedelta

import aiosqlite

from arden.areas.work_models import (
    AreaOutcome,
    AreaWorkEvent,
    AreaWorkItem,
    AreaWorkSnapshot,
    EvidenceDraft,
    OutcomeChange,
    WorkChange,
)

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


class AreaWorkReportError(RuntimeError):
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
            "SELECT * FROM area_work_outcomes WHERE area_id = ? ORDER BY priority DESC, updated_at DESC",
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

    async def brief(self, area_ids: set[str], *, now: datetime | None = None) -> dict:
        if not area_ids:
            return {"done": [], "in_progress": []}
        placeholders = ", ".join("?" for _ in area_ids)
        ids = tuple(sorted(area_ids))
        cutoff = ((now or datetime.now(UTC)) - timedelta(hours=72)).isoformat()
        completed = await self.read_conn.execute_fetchall(
            f"SELECT events.area_id, CASE WHEN events.item_id IS NOT NULL THEN 'work' "
            f"ELSE 'outcome' END AS type, "
            f"COALESCE(items.stable_key, outcomes.stable_key, 'event-' || events.event_id) AS stable_key, "
            f"events.summary AS text, events.created_at AS completed_at, events.created_at AS updated_at "
            f"FROM area_work_events events "
            f"LEFT JOIN area_work_items items ON items.item_id = events.item_id "
            f"LEFT JOIN area_work_outcomes outcomes ON outcomes.outcome_id = events.outcome_id "
            f"WHERE events.area_id IN ({placeholders}) AND events.event_type = 'completed' "
            f"AND events.run_ref IS NOT NULL AND events.created_at >= ? "
            f"ORDER BY events.created_at DESC, events.event_id DESC LIMIT 6",
            (*ids, cutoff),
        )
        active = await self.read_conn.execute_fetchall(
            f"WITH ranked AS ("
            f"SELECT wi.*, outcomes.title AS outcome_title, ROW_NUMBER() OVER (PARTITION BY wi.area_id ORDER BY "
            f"CASE wi.status WHEN 'in_progress' THEN 0 ELSE 1 END, "
            f"CASE wi.owner WHEN 'custodian' THEN 0 ELSE 1 END, wi.updated_at DESC) AS rank "
            f"FROM area_work_items wi LEFT JOIN area_work_outcomes outcomes "
            f"ON outcomes.outcome_id = wi.outcome_id WHERE wi.area_id IN ({placeholders}) "
            f"AND wi.status IN ('active', 'in_progress')) "
            f"SELECT * FROM ranked WHERE rank = 1 ORDER BY updated_at DESC LIMIT 6",
            ids,
        )
        return {
            "done": [dict(row) for row in completed],
            "in_progress": [
                {
                    **AreaWorkItem.model_validate(dict(row)).model_dump(mode="json"),
                    "outcome_title": row["outcome_title"],
                }
                for row in active
            ],
        }

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

    async def apply_report(self, area_id: str, run_ref: str, report) -> bool:
        prior = await self.read_conn.execute_fetchall(
            "SELECT 1 FROM area_work_reports WHERE run_ref = ?",
            (run_ref,),
        )
        if prior:
            return False

        outcome_changes: list[OutcomeChange] = list(report.outcome_changes)
        work_changes: list[WorkChange] = list(report.work_changes)
        evidence: list[EvidenceDraft] = list(report.evidence)
        snapshot = await self.snapshot(area_id)
        outcomes = {row.stable_key: row for row in snapshot.outcomes}
        work_items = {row.stable_key: row for row in snapshot.work_items}
        effective_outcome_changes = []
        for change in outcome_changes:
            if change.op != "update" or change.key not in outcomes:
                effective_outcome_changes.append(change)
                continue
            current = outcomes[change.key]
            if (
                (change.title is not None and change.title != current.title)
                or (change.success_criteria is not None and change.success_criteria != current.success_criteria)
                or (change.priority is not None and change.priority != current.priority)
            ):
                effective_outcome_changes.append(change)
        outcome_changes = effective_outcome_changes

        effective_work_changes = []
        for change in work_changes:
            if change.op != "update" or change.key not in work_items:
                effective_work_changes.append(change)
                continue
            current = work_items[change.key]
            outcome_id = f"outcome:{area_id}:{change.outcome_key}" if change.outcome_key else None
            if (
                (outcome_id is not None and outcome_id != current.outcome_id)
                or (change.text is not None and change.text != current.text)
                or (change.owner is not None and change.owner != current.owner)
                or (change.due_at is not None and change.due_at != current.due_at)
                or (change.next_attempt_at is not None and change.next_attempt_at != current.next_attempt_at)
            ):
                effective_work_changes.append(change)
        work_changes = effective_work_changes
        self._validate_report(outcomes, work_items, outcome_changes, work_changes, evidence)

        now = _now()
        await self.conn.execute("SAVEPOINT area_work_report")
        try:
            await self.conn.execute(
                "INSERT INTO area_work_reports (run_ref, area_id, applied_at) VALUES (?, ?, ?)",
                (run_ref, area_id, now),
            )
            for change in outcome_changes:
                await self._apply_outcome_change(area_id, change, now)
            for change in work_changes:
                await self._apply_work_change(area_id, change, now)
            for index, draft in enumerate(evidence):
                await self._append_evidence(area_id, run_ref, index, draft, now)
            await self.conn.execute("RELEASE SAVEPOINT area_work_report")
            await self.conn.commit()
        except Exception:
            await self.conn.execute("ROLLBACK TO SAVEPOINT area_work_report")
            await self.conn.execute("RELEASE SAVEPOINT area_work_report")
            raise
        return True

    @staticmethod
    def _validate_report(
        outcomes: dict[str, AreaOutcome],
        work_items: dict[str, AreaWorkItem],
        outcome_changes: list[OutcomeChange],
        work_changes: list[WorkChange],
        evidence: list[EvidenceDraft],
    ) -> None:
        outcome_keys = set(outcomes)
        work_keys = set(work_items)
        for change in outcome_changes:
            exists = change.key in outcome_keys
            if change.op == "create":
                if exists:
                    raise AreaWorkReportError(f"Outcome '{change.key}' already exists")
                outcome_keys.add(change.key)
            elif not exists:
                raise AreaWorkReportError(f"Unknown outcome '{change.key}'")
            elif outcomes[change.key].status in {"completed", "cancelled"}:
                raise AreaWorkReportError(f"Terminal outcome '{change.key}' cannot be changed by an agent")
            elif outcomes[change.key].updated_at != change.expected_updated_at:
                raise AreaWorkReportError(f"Outcome '{change.key}' changed since the run started")
        for change in work_changes:
            exists = change.key in work_keys
            if change.op == "create":
                if exists:
                    raise AreaWorkReportError(f"Work item '{change.key}' already exists")
                work_keys.add(change.key)
            elif not exists:
                raise AreaWorkReportError(f"Unknown work item '{change.key}'")
            elif work_items[change.key].status in {"completed", "cancelled"}:
                raise AreaWorkReportError(f"Terminal work item '{change.key}' cannot be changed by an agent")
            elif work_items[change.key].updated_at != change.expected_updated_at:
                raise AreaWorkReportError(f"Work item '{change.key}' changed since the run started")
            if change.outcome_key is not None and change.outcome_key not in outcome_keys:
                raise AreaWorkReportError(f"Unknown outcome '{change.outcome_key}'")
        for draft in evidence:
            if draft.target_type == "outcome" and draft.target_key not in outcome_keys:
                raise AreaWorkReportError(f"Unknown evidence outcome '{draft.target_key}'")
            if draft.target_type == "work" and draft.target_key not in work_keys:
                raise AreaWorkReportError(f"Unknown evidence work item '{draft.target_key}'")

    async def _apply_outcome_change(self, area_id: str, change: OutcomeChange, now: str) -> None:
        if change.op == "create":
            await self.conn.execute(
                "INSERT INTO area_work_outcomes "
                "(outcome_id, area_id, stable_key, title, success_criteria, status, priority, source, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, 'active', ?, 'inferred', ?, ?)",
                (
                    f"outcome:{area_id}:{change.key}",
                    area_id,
                    change.key,
                    change.title,
                    change.success_criteria,
                    change.priority,
                    now,
                    now,
                ),
            )
            return
        if change.op in {"complete", "cancel"}:
            status = "completed" if change.op == "complete" else "cancelled"
            await self.conn.execute(
                "UPDATE area_work_outcomes SET status = ?, updated_at = ?, completed_at = ? "
                "WHERE area_id = ? AND stable_key = ?",
                (status, now, now, area_id, change.key),
            )
            return
        values = {
            name: value
            for name, value in {
                "title": change.title,
                "success_criteria": change.success_criteria,
                "priority": change.priority,
            }.items()
            if value is not None
        }
        values["updated_at"] = now
        await self.conn.execute(
            f"UPDATE area_work_outcomes SET {', '.join(f'{name} = ?' for name in values)} "
            "WHERE area_id = ? AND stable_key = ?",
            (*values.values(), area_id, change.key),
        )

    async def _apply_work_change(self, area_id: str, change: WorkChange, now: str) -> None:
        if change.op == "create":
            outcome_id = f"outcome:{area_id}:{change.outcome_key}" if change.outcome_key else None
            await self.conn.execute(
                "INSERT INTO area_work_items "
                "(item_id, area_id, stable_key, outcome_id, kind, text, status, owner, due_at, next_attempt_at, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)",
                (
                    f"work:{area_id}:{change.key}",
                    area_id,
                    change.key,
                    outcome_id,
                    change.kind,
                    change.text,
                    change.owner,
                    change.due_at,
                    change.next_attempt_at,
                    now,
                    now,
                ),
            )
            return
        if change.op in {"complete", "cancel"}:
            status = "completed" if change.op == "complete" else "cancelled"
            await self.conn.execute(
                "UPDATE area_work_items SET status = ?, updated_at = ?, completed_at = ? "
                "WHERE area_id = ? AND stable_key = ?",
                (status, now, now, area_id, change.key),
            )
            return
        values = {
            name: value
            for name, value in {
                "outcome_id": f"outcome:{area_id}:{change.outcome_key}" if change.outcome_key else None,
                "text": change.text,
                "owner": change.owner,
                "due_at": change.due_at,
                "next_attempt_at": change.next_attempt_at,
            }.items()
            if value is not None
        }
        values["updated_at"] = now
        await self.conn.execute(
            f"UPDATE area_work_items SET {', '.join(f'{name} = ?' for name in values)} "
            "WHERE area_id = ? AND stable_key = ?",
            (*values.values(), area_id, change.key),
        )

    async def _append_evidence(
        self,
        area_id: str,
        run_ref: str,
        index: int,
        draft: EvidenceDraft,
        now: str,
    ) -> None:
        outcome_id = f"outcome:{area_id}:{draft.target_key}" if draft.target_type == "outcome" else None
        item_id = f"work:{area_id}:{draft.target_key}" if draft.target_type == "work" else None
        await self.conn.execute(
            "INSERT INTO area_work_events "
            "(area_id, outcome_id, item_id, run_ref, operation_index, event_type, summary, source_refs, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                area_id,
                outcome_id,
                item_id,
                run_ref,
                index,
                draft.event_type,
                draft.summary,
                json.dumps(draft.source_refs),
                now,
            ),
        )

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
