"""Durable workflow state for fact plans, separate from canonical fact events."""

import asyncio
import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import aiosqlite

from arden.memory.facts.models import FactEvent, FactPlan

_PLAN_SCHEMA_VERSION = 1
_EVENT_FIELDS = frozenset(
    {
        "schema_version",
        "event_id",
        "plan_id",
        "fact_id",
        "op",
        "occurred_at",
        "actor",
        "origin",
        "plan_reason",
        "payload",
    }
)
_SCOPE_KINDS = frozenset({"user", "area", "global", "project", "integration"})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS fact_plans (
    plan_id TEXT PRIMARY KEY,
    owner_id TEXT NOT NULL,
    request_key TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    scope_json TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    plan_fingerprint TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('planned', 'committing', 'committed')),
    created_at TEXT NOT NULL,
    committed_at TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_fact_plans_owner_request
    ON fact_plans(owner_id, request_key);
"""


class FactPlanStatus(StrEnum):
    PLANNED = "planned"
    COMMITTING = "committing"
    COMMITTED = "committed"


class FactPlanStoreError(ValueError):
    """Base error for durable fact-plan workflow state."""


class FactPlanRequestConflictError(FactPlanStoreError):
    """A caller reused one request key for a different plan request."""


class FactPlanOwnershipError(FactPlanStoreError):
    """The selected plan belongs to another principal."""


class FactPlanCorruptionError(FactPlanStoreError):
    """Persisted plan state is malformed or was altered outside the store."""


@dataclass(frozen=True, slots=True)
class StoredFactPlan:
    plan_id: str
    owner_id: str
    request_key: str
    request_fingerprint: str
    scopes: tuple[tuple[str, str | None], ...]
    plan_json: str
    plan_fingerprint: str
    status: FactPlanStatus
    created_at: datetime
    committed_at: datetime | None

    def plan(self) -> FactPlan:
        try:
            scope_json = _scope_json(self.scopes)
        except FactPlanStoreError as exc:
            raise FactPlanCorruptionError("persisted fact plan scopes are invalid") from exc
        if _binding_fingerprint(self.plan_json, scope_json) != self.plan_fingerprint:
            raise FactPlanCorruptionError("persisted fact plan binding fingerprint does not match")
        return deserialize_fact_plan(self.plan_json)


def _required_text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise FactPlanStoreError(f"{name} must be a non-empty string")
    return value


def _canonical(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise FactPlanStoreError("fact plan data must be JSON-safe") from exc


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise FactPlanCorruptionError("fact plan JSON has duplicate object keys")
        result[key] = value
    return result


def _fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _binding_fingerprint(plan_json: str, scope_json: str) -> str:
    return _fingerprint(f"{plan_json}\0{scope_json}")


def _utc(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise FactPlanCorruptionError(f"{field} must be an RFC3339 timestamp")
    try:
        point = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FactPlanCorruptionError(f"invalid {field}") from exc
    if point.tzinfo is None or point.utcoffset() != timedelta(0):
        raise FactPlanCorruptionError(f"{field} must be UTC")
    return point.astimezone(UTC)


def _plan_value(plan: FactPlan) -> dict[str, Any]:
    if not isinstance(plan, FactPlan):
        raise TypeError("plan must be a FactPlan")
    if not isinstance(plan.plan_id, str) or not plan.plan_id:
        raise FactPlanStoreError("plan_id must be a non-empty string")
    if type(plan.events) is not tuple or not plan.events:
        raise FactPlanStoreError("fact plan events must be a non-empty tuple")
    events: list[dict[str, Any]] = []
    for event in plan.events:
        if not isinstance(event, FactEvent) or not isinstance(event.record, dict):
            raise FactPlanStoreError("fact plan contains an invalid event")
        events.append(dict(event.record))
    if not isinstance(plan.dependencies, dict) or not isinstance(plan.duplicate_windows, dict):
        raise FactPlanStoreError("fact plan maps must be dictionaries")
    return {
        "plan_id": plan.plan_id,
        "events": events,
        "dependencies": dict(plan.dependencies),
        "duplicate_windows": dict(plan.duplicate_windows),
    }


def serialize_fact_plan(plan: FactPlan) -> str:
    """The only durable representation accepted for a fact plan."""

    return _canonical({"version": _PLAN_SCHEMA_VERSION, "plan": _plan_value(plan)})


def deserialize_fact_plan(plan_json: str) -> FactPlan:
    """Decode canonical durable plan bytes; ledger commit remains final validator."""

    if not isinstance(plan_json, str):
        raise FactPlanCorruptionError("fact plan JSON must be a string")
    try:
        value = json.loads(plan_json, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, FactPlanCorruptionError) as exc:
        raise FactPlanCorruptionError("fact plan JSON is invalid") from exc
    if _canonical(value) != plan_json:
        raise FactPlanCorruptionError("fact plan JSON is not canonical")
    if not isinstance(value, dict) or set(value) != {"version", "plan"} or value["version"] != _PLAN_SCHEMA_VERSION:
        raise FactPlanCorruptionError("unsupported fact plan schema")
    plan = value["plan"]
    if not isinstance(plan, dict) or set(plan) != {"plan_id", "events", "dependencies", "duplicate_windows"}:
        raise FactPlanCorruptionError("invalid fact plan fields")
    plan_id = _required_text("plan_id", plan["plan_id"])
    if not isinstance(plan["events"], list) or not plan["events"]:
        raise FactPlanCorruptionError("fact plan events must be a non-empty list")
    events = tuple(_event(record, plan_id) for record in plan["events"])
    if not isinstance(plan["dependencies"], dict) or not isinstance(plan["duplicate_windows"], dict):
        raise FactPlanCorruptionError("fact plan maps must be objects")
    _string_map(plan["dependencies"], "dependencies")
    _string_map(plan["duplicate_windows"], "duplicate_windows")
    return FactPlan(plan_id, events, dict(plan["dependencies"]), dict(plan["duplicate_windows"]))


def _string_map(value: dict[str, Any], field: str) -> None:
    if any(not isinstance(key, str) or not isinstance(item, str) for key, item in value.items()):
        raise FactPlanCorruptionError(f"fact plan {field} must map strings to strings")


def _event(record: object, plan_id: str) -> FactEvent:
    if not isinstance(record, dict) or set(record) != _EVENT_FIELDS:
        raise FactPlanCorruptionError("invalid fact event fields")
    if record.get("plan_id") != plan_id:
        raise FactPlanCorruptionError("fact event plan_id does not match its plan")
    for field in ("event_id", "fact_id", "op", "actor", "origin", "plan_reason"):
        _required_text(field, record.get(field))
    if not isinstance(record["payload"], dict):
        raise FactPlanCorruptionError("fact event payload must be an object")
    point = _utc(record["occurred_at"], "occurred_at")
    return FactEvent(
        record["event_id"],
        plan_id,
        record["fact_id"],
        record["op"],
        point,
        record["actor"],
        record["origin"],
        record["plan_reason"],
        dict(record),
    )


def _scope_json(scopes: tuple[tuple[str, str | None], ...]) -> str:
    normalized = sorted(
        {_validated_scope(kind, key) for kind, key in scopes}, key=lambda item: (item[0], item[1] or "")
    )
    return _canonical([[kind, key] for kind, key in normalized])


def _decode_scopes(value: object) -> tuple[tuple[str, str | None], ...]:
    if not isinstance(value, list):
        raise FactPlanCorruptionError("persisted fact plan scopes are invalid")
    scopes: list[tuple[str, str | None]] = []
    for item in value:
        if not isinstance(item, list) or len(item) != 2:
            raise FactPlanCorruptionError("persisted fact plan scopes are invalid")
        try:
            scopes.append(_validated_scope(item[0], item[1]))
        except FactPlanStoreError as exc:
            raise FactPlanCorruptionError("persisted fact plan scope is invalid") from exc
    normalized = sorted(set(scopes), key=lambda item: (item[0], item[1] or ""))
    if _canonical([[kind, key] for kind, key in normalized]) != _canonical(value):
        raise FactPlanCorruptionError("persisted fact plan scopes are not canonical")
    return tuple(scopes)


def _validated_scope(kind: object, key: object) -> tuple[str, str | None]:
    kind = _required_text("scope kind", kind)
    if kind not in _SCOPE_KINDS:
        raise FactPlanStoreError("invalid scope kind")
    if kind in {"user", "global"}:
        if key is not None:
            raise FactPlanStoreError(f"{kind} scope key must be null")
        return kind, None
    if not isinstance(key, str) or not key or "\0" in key:
        raise FactPlanStoreError(f"{kind} scope key must be a non-empty string")
    return kind, key


def _row(row: aiosqlite.Row) -> StoredFactPlan:
    try:
        scopes = _decode_scopes(json.loads(row["scope_json"], object_pairs_hook=_unique_object))
        created_at = _utc(row["created_at"], "created_at")
        committed_at = _utc(row["committed_at"], "committed_at") if row["committed_at"] else None
        result = StoredFactPlan(
            _required_text("plan_id", row["plan_id"]),
            _required_text("owner_id", row["owner_id"]),
            _required_text("request_key", row["request_key"]),
            _required_text("request_fingerprint", row["request_fingerprint"]),
            scopes,
            _required_text("plan_json", row["plan_json"]),
            _required_text("plan_fingerprint", row["plan_fingerprint"]),
            FactPlanStatus(row["status"]),
            created_at,
            committed_at,
        )
        result.plan()
        return result
    except FactPlanCorruptionError:
        raise
    except (FactPlanStoreError, ValueError, json.JSONDecodeError) as exc:
        raise FactPlanCorruptionError("persisted fact plan is invalid") from exc


class FactPlanStore:
    """One durable, owner-scoped plan per caller request key."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self.conn = conn
        self._lock = asyncio.Lock()

    async def init_schema(self) -> None:
        async with self._lock:
            await self.conn.executescript(_SCHEMA)
            await self.conn.commit()

    async def get(self, plan_id: str, *, owner_id: str) -> StoredFactPlan:
        _required_text("plan_id", plan_id)
        _required_text("owner_id", owner_id)
        async with self._lock:
            rows = await self.conn.execute_fetchall("SELECT * FROM fact_plans WHERE plan_id = ?", (plan_id,))
        if not rows:
            raise KeyError(f"unknown fact plan: {plan_id}")
        stored = _row(rows[0])
        if stored.owner_id != owner_id:
            raise FactPlanOwnershipError("fact plan belongs to another principal")
        return stored

    async def get_by_request(self, *, owner_id: str, request_key: str) -> StoredFactPlan | None:
        _required_text("owner_id", owner_id)
        _required_text("request_key", request_key)
        async with self._lock:
            rows = await self.conn.execute_fetchall(
                "SELECT * FROM fact_plans WHERE owner_id = ? AND request_key = ?", (owner_id, request_key)
            )
        return _row(rows[0]) if rows else None

    async def plan_ids_for_owner(self, owner_id: str) -> tuple[str, ...]:
        """Return storage identities for exact owner-scoped lookup."""

        _required_text("owner_id", owner_id)
        async with self._lock:
            rows = await self.conn.execute_fetchall(
                "SELECT plan_id FROM fact_plans WHERE owner_id = ? ORDER BY created_at, plan_id",
                (owner_id,),
            )
        return tuple(_required_text("plan_id", row["plan_id"]) for row in rows)

    async def create_or_reuse(
        self,
        *,
        owner_id: str,
        request_key: str,
        request_fingerprint: str,
        scopes: tuple[tuple[str, str | None], ...],
        plan: FactPlan,
    ) -> StoredFactPlan:
        owner_id = _required_text("owner_id", owner_id)
        request_key = _required_text("request_key", request_key)
        request_fingerprint = _required_text("request_fingerprint", request_fingerprint)
        plan_json = serialize_fact_plan(plan)
        scope_json = _scope_json(scopes)
        plan_fingerprint = _binding_fingerprint(plan_json, scope_json)
        now = datetime.now(UTC).isoformat()
        async with self._lock:
            try:
                cursor = await self.conn.execute(
                    """
                    INSERT OR IGNORE INTO fact_plans (
                        plan_id, owner_id, request_key, request_fingerprint, scope_json,
                        plan_json, plan_fingerprint, status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, 'planned', ?)
                    """,
                    (
                        plan.plan_id,
                        owner_id,
                        request_key,
                        request_fingerprint,
                        scope_json,
                        plan_json,
                        plan_fingerprint,
                        now,
                    ),
                )
                await self.conn.commit()
            except sqlite3.IntegrityError as exc:
                await self.conn.rollback()
                raise FactPlanStoreError("fact plan persistence failed") from exc
            if cursor.rowcount:
                return StoredFactPlan(
                    plan.plan_id,
                    owner_id,
                    request_key,
                    request_fingerprint,
                    _decode_scopes(json.loads(scope_json)),
                    plan_json,
                    plan_fingerprint,
                    FactPlanStatus.PLANNED,
                    _utc(now, "created_at"),
                    None,
                )
            rows = await self.conn.execute_fetchall(
                "SELECT * FROM fact_plans WHERE owner_id = ? AND request_key = ?", (owner_id, request_key)
            )
        if not rows:
            raise FactPlanStoreError("fact plan request was not persisted")
        existing = _row(rows[0])
        if existing.request_fingerprint != request_fingerprint:
            raise FactPlanRequestConflictError("request_key was reused for a different fact plan request")
        return existing

    async def claim(self, plan_id: str, *, owner_id: str) -> tuple[StoredFactPlan, bool]:
        stored = await self.get(plan_id, owner_id=owner_id)
        if stored.status is FactPlanStatus.COMMITTED:
            return stored, False
        async with self._lock:
            cursor = await self.conn.execute(
                "UPDATE fact_plans SET status = 'committing' WHERE plan_id = ? AND status = 'planned'", (plan_id,)
            )
            await self.conn.commit()
        return await self.get(plan_id, owner_id=owner_id), bool(cursor.rowcount)

    async def mark_committed(self, plan_id: str, *, owner_id: str) -> StoredFactPlan:
        _required_text("plan_id", plan_id)
        _required_text("owner_id", owner_id)
        now = datetime.now(UTC).isoformat()
        async with self._lock:
            cursor = await self.conn.execute(
                """
                UPDATE fact_plans
                SET status = 'committed', committed_at = COALESCE(committed_at, ?)
                WHERE plan_id = ? AND owner_id = ? AND status IN ('committing', 'committed')
                """,
                (now, plan_id, owner_id),
            )
            await self.conn.commit()
        if not cursor.rowcount:
            raise FactPlanStoreError("fact plan was not claimed for commit")
        return await self.get(plan_id, owner_id=owner_id)

    async def release(self, plan_id: str, *, owner_id: str) -> StoredFactPlan:
        """Return a safely rejected commit attempt to its reusable plan state."""

        _required_text("plan_id", plan_id)
        _required_text("owner_id", owner_id)
        async with self._lock:
            await self.conn.execute(
                """
                UPDATE fact_plans
                SET status = 'planned'
                WHERE plan_id = ? AND owner_id = ? AND status = 'committing'
                """,
                (plan_id, owner_id),
            )
            await self.conn.commit()
        stored = await self.get(plan_id, owner_id=owner_id)
        if stored.status not in {FactPlanStatus.PLANNED, FactPlanStatus.COMMITTED}:
            raise FactPlanStoreError("fact plan commit claim could not be released")
        return stored
