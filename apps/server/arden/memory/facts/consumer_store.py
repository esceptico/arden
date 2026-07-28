"""Durable compare-and-swap watermarks for fact-feed consumers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from arden.database import connect

from .ledger import FactLedger
from .models import FactChangeFeed

if TYPE_CHECKING:
    from pathlib import Path

    import aiosqlite


_SCHEMA = """
CREATE TABLE IF NOT EXISTS fact_consumer_watermarks (
    consumer_id TEXT PRIMARY KEY,
    revision TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fact_retention_checkpoints (
    consumer_id TEXT PRIMARY KEY,
    revision TEXT,
    evaluated_at TEXT NOT NULL
);
"""


class FactConsumerWatermarkConflictError(RuntimeError):
    """A consumer tried to advance from a stale checkpoint."""


@dataclass(frozen=True, slots=True)
class FactConsumerWatermark:
    consumer_id: str
    revision: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class FactRetentionCheckpoint:
    revision: str | None
    evaluated_at: datetime


def _consumer_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ValueError("consumer_id must be a non-empty string")
    return value


def _revision(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a 64-character lowercase revision ID")
    return value


def _row(row) -> FactConsumerWatermark:
    try:
        updated_at = datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("persisted fact consumer watermark is invalid") from exc
    if updated_at.tzinfo is None or updated_at.utcoffset() != timedelta(0):
        raise RuntimeError("persisted fact consumer watermark is not UTC")
    return FactConsumerWatermark(_consumer_id(row["consumer_id"]), _revision(row["revision"], "revision"), updated_at)


def _checkpoint_row(row) -> FactRetentionCheckpoint:
    try:
        evaluated_at = datetime.fromisoformat(row["evaluated_at"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("persisted fact retention checkpoint is invalid") from exc
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() != timedelta(0):
        raise RuntimeError("persisted fact retention checkpoint is not UTC")
    revision = row["revision"]
    if revision is not None:
        revision = _revision(revision, "revision")
    return FactRetentionCheckpoint(revision, evaluated_at)


class FactConsumerStore:
    """Durable checkpoints on a dedicated autocommit connection."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        if conn.isolation_level is not None:
            raise ValueError("FactConsumerStore requires an autocommit connection")
        self._conn = conn

    @classmethod
    async def open(cls, db_path: Path) -> FactConsumerStore:
        """Open a transaction-independent connection to the shared fact database."""

        conn = await connect(db_path, autocommit=True)
        store = cls(conn)
        try:
            await store.init_schema()
        except BaseException:
            await conn.close()
            raise
        return store

    async def init_schema(self) -> None:
        await self._conn.executescript(_SCHEMA)

    async def get(self, consumer_id: str) -> FactConsumerWatermark | None:
        consumer_id = _consumer_id(consumer_id)
        rows = await self._conn.execute_fetchall(
            "SELECT consumer_id, revision, updated_at FROM fact_consumer_watermarks WHERE consumer_id = ?",
            (consumer_id,),
        )
        return None if not rows else _row(rows[0])

    async def advance(
        self,
        consumer_id: str,
        *,
        feed: FactChangeFeed,
        ledger: FactLedger,
    ) -> FactConsumerWatermark:
        """Advance only through the exact canonical feed already processed."""

        consumer_id = _consumer_id(consumer_id)
        await asyncio.to_thread(ledger.validate_change_feed, feed)
        expected_revision = feed.from_revision
        revision = feed.through_revision
        if revision is None:
            raise ValueError("an empty fact ledger has no consumer revision")
        if expected_revision is not None:
            expected_revision = _revision(expected_revision, "expected_revision")
        revision = _revision(revision, "revision")
        updated_at = datetime.now(UTC).isoformat()
        if expected_revision is None:
            cursor = await self._conn.execute(
                """
                INSERT INTO fact_consumer_watermarks (consumer_id, revision, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(consumer_id) DO NOTHING
                """,
                (consumer_id, revision, updated_at),
            )
        else:
            cursor = await self._conn.execute(
                """
                UPDATE fact_consumer_watermarks
                SET revision = ?, updated_at = ?
                WHERE consumer_id = ? AND revision = ?
                """,
                (revision, updated_at, consumer_id, expected_revision),
            )
        if cursor.rowcount != 1:
            raise FactConsumerWatermarkConflictError("fact consumer watermark changed before advance")
        return FactConsumerWatermark(consumer_id, revision, datetime.fromisoformat(updated_at))

    async def get_retention_checkpoint(self, consumer_id: str) -> FactRetentionCheckpoint | None:
        consumer_id = _consumer_id(consumer_id)
        rows = await self._conn.execute_fetchall(
            "SELECT revision, evaluated_at FROM fact_retention_checkpoints WHERE consumer_id = ?",
            (consumer_id,),
        )
        return None if not rows else _checkpoint_row(rows[0])

    async def record_retention_checkpoint(
        self,
        consumer_id: str,
        *,
        revision: str | None,
        evaluated_at: datetime,
    ) -> FactRetentionCheckpoint:
        consumer_id = _consumer_id(consumer_id)
        if revision is not None:
            revision = _revision(revision, "revision")
        if evaluated_at.tzinfo is None or evaluated_at.utcoffset() != timedelta(0):
            raise ValueError("evaluated_at must be UTC")
        encoded = evaluated_at.isoformat()
        await self._conn.execute(
            """
            INSERT INTO fact_retention_checkpoints (consumer_id, revision, evaluated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(consumer_id) DO UPDATE SET revision = excluded.revision, evaluated_at = excluded.evaluated_at
            """,
            (consumer_id, revision, encoded),
        )
        return FactRetentionCheckpoint(revision, datetime.fromisoformat(encoded))

    async def close(self) -> None:
        await self._conn.close()
