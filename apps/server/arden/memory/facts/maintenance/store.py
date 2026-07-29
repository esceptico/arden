"""Durable topic-normalization intents for Fact Maintenance."""

import asyncio
import json
from typing import Annotated

import aiosqlite
from pydantic import BaseModel, ConfigDict, Field, StrictStr

CONSUMER_ID = "memory.maintenance"

_INTENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS fact_maintenance_intents (
    consumer_id TEXT PRIMARY KEY,
    from_revision TEXT,
    through_revision TEXT NOT NULL,
    plan_id TEXT NOT NULL,
    bindings_json TEXT NOT NULL,
    normalized_facts_json TEXT NOT NULL
);
"""


class FactMaintenanceError(RuntimeError):
    """A maintenance decision could not be applied safely."""


type _NonEmptyText = Annotated[StrictStr, Field(min_length=1)]


class NormalizedFactIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fact_id: _NonEmptyText
    before_subjects: tuple[_NonEmptyText, ...] = Field(min_length=1)
    after_subjects: tuple[_NonEmptyText, ...] = Field(min_length=1)


class MaintenanceIntent(BaseModel):
    """Strict representation of one intent persisted in SQLite."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    from_revision: StrictStr | None
    through_revision: _NonEmptyText
    plan_id: _NonEmptyText
    bindings: tuple[tuple[_NonEmptyText, _NonEmptyText, _NonEmptyText], ...] = Field(min_length=1)
    normalized_facts: tuple[NormalizedFactIntent, ...] = Field(min_length=1)


class FactMaintenanceIntentStore:
    """One durable cross-ledger intent for the maintenance watermark."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn
        self._lock = asyncio.Lock()

    async def init_schema(self) -> None:
        async with self._lock:
            await self._conn.executescript(_INTENT_SCHEMA)
            await self._conn.commit()

    async def get(self) -> MaintenanceIntent | None:
        async with self._lock:
            rows = await self._conn.execute_fetchall(
                """
                SELECT from_revision, through_revision, plan_id, bindings_json, normalized_facts_json
                FROM fact_maintenance_intents
                WHERE consumer_id = ?
                """,
                (CONSUMER_ID,),
            )
        return None if not rows else self._decode(rows[0])

    async def save(self, intent: MaintenanceIntent) -> None:
        bindings = json.dumps(intent.bindings, ensure_ascii=True, separators=(",", ":"))
        normalized = json.dumps(
            [[item.fact_id, list(item.before_subjects), list(item.after_subjects)] for item in intent.normalized_facts],
            ensure_ascii=True,
            separators=(",", ":"),
        )
        async with self._lock:
            cursor = await self._conn.execute(
                """
                INSERT OR IGNORE INTO fact_maintenance_intents (
                    consumer_id, from_revision, through_revision, plan_id,
                    bindings_json, normalized_facts_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    CONSUMER_ID,
                    intent.from_revision,
                    intent.through_revision,
                    intent.plan_id,
                    bindings,
                    normalized,
                ),
            )
            await self._conn.commit()
        if cursor.rowcount:
            return
        if await self.get() != intent:
            raise FactMaintenanceError("another topic-normalization intent is already pending")

    async def delete(self, plan_id: str) -> None:
        async with self._lock:
            await self._conn.execute(
                "DELETE FROM fact_maintenance_intents WHERE consumer_id = ? AND plan_id = ?",
                (CONSUMER_ID, plan_id),
            )
            await self._conn.commit()

    @staticmethod
    def _decode(row: aiosqlite.Row) -> MaintenanceIntent:
        try:
            return MaintenanceIntent(
                from_revision=row["from_revision"],
                through_revision=row["through_revision"],
                plan_id=row["plan_id"],
                bindings=json.loads(row["bindings_json"]),
                normalized_facts=[
                    {
                        "fact_id": fact_id,
                        "before_subjects": before,
                        "after_subjects": after,
                    }
                    for fact_id, before, after in json.loads(row["normalized_facts_json"])
                ],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise FactMaintenanceError("persisted topic-normalization intent is invalid") from exc
