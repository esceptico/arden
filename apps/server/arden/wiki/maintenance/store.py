"""Durable checkpoints for Wiki Maintenance and its derived projection."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Self

import aiosqlite

from arden.database import connect
from arden.wiki.constants import WIKI_MAINTENANCE_ORIGIN, WIKI_PROJECTION_CONSUMER_ID

CONSUMER_ID = WIKI_MAINTENANCE_ORIGIN
_SCHEMA = """
CREATE TABLE IF NOT EXISTS wiki_maintenance_watermarks (
    consumer_id TEXT PRIMARY KEY,
    revision TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class WikiMaintenanceWatermarkConflictError(RuntimeError):
    """The checkpoint changed before this caller could advance it."""


class WikiProjectionWatermarkConflictError(RuntimeError):
    """The derived wiki projection checkpoint changed before advance."""


@dataclass(frozen=True, slots=True)
class WikiMaintenanceWatermark:
    consumer_id: str
    revision: str
    updated_at: datetime


def _revision(name: str, value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a 64-character lowercase revision ID")
    return value


def _timestamp(value: object, name: str) -> datetime:
    try:
        timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError(f"persisted wiki maintenance {name} is invalid") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
        raise RuntimeError(f"persisted wiki maintenance {name} is not UTC")
    return timestamp


def _watermark_row(row: aiosqlite.Row) -> WikiMaintenanceWatermark:
    consumer_id = row["consumer_id"]
    if not isinstance(consumer_id, str) or not consumer_id:
        raise RuntimeError("persisted wiki maintenance consumer ID is invalid")
    return WikiMaintenanceWatermark(
        consumer_id=consumer_id,
        revision=_revision("revision", row["revision"]),
        updated_at=_timestamp(row["updated_at"], "watermark timestamp"),
    )


class WikiMaintenanceStore:
    """Durable Wiki Maintenance state on a dedicated autocommit connection."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        if conn.isolation_level is not None:
            raise ValueError("WikiMaintenanceStore requires an autocommit connection")
        self._conn = conn
        self._lock = asyncio.Lock()

    @classmethod
    async def open(cls, db_path: Path) -> Self:
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

    async def close(self) -> None:
        await self._conn.close()

    async def get_watermark(self) -> WikiMaintenanceWatermark | None:
        rows = await self._conn.execute_fetchall(
            "SELECT consumer_id, revision, updated_at FROM wiki_maintenance_watermarks WHERE consumer_id = ?",
            (CONSUMER_ID,),
        )
        return None if not rows else _watermark_row(rows[0])

    async def get_projection_revision(self) -> str | None:
        rows = await self._conn.execute_fetchall(
            "SELECT revision FROM wiki_maintenance_watermarks WHERE consumer_id = ?",
            (WIKI_PROJECTION_CONSUMER_ID,),
        )
        return None if not rows else _revision("revision", rows[0]["revision"])

    async def record_projection_revision(
        self,
        *,
        expected_revision: str | None,
        revision: str,
    ) -> None:
        if expected_revision is not None:
            expected_revision = _revision("expected_revision", expected_revision)
        revision = _revision("revision", revision)
        await self._replace(
            consumer_id=WIKI_PROJECTION_CONSUMER_ID,
            expected_revision=expected_revision,
            revision=revision,
            conflict=WikiProjectionWatermarkConflictError,
        )

    async def advance(self, *, expected_revision: str | None, revision: str) -> WikiMaintenanceWatermark:
        if expected_revision is not None:
            expected_revision = _revision("expected_revision", expected_revision)
        revision = _revision("revision", revision)
        timestamp = await self._replace(
            consumer_id=CONSUMER_ID,
            expected_revision=expected_revision,
            revision=revision,
            conflict=WikiMaintenanceWatermarkConflictError,
        )
        return WikiMaintenanceWatermark(CONSUMER_ID, revision, timestamp)

    async def _replace(
        self,
        *,
        consumer_id: str,
        expected_revision: str | None,
        revision: str,
        conflict: type[RuntimeError],
    ) -> datetime:
        now = datetime.now(UTC)
        async with self._lock:
            if expected_revision is None:
                cursor = await self._conn.execute(
                    """
                    INSERT INTO wiki_maintenance_watermarks (consumer_id, revision, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(consumer_id) DO NOTHING
                    """,
                    (consumer_id, revision, now.isoformat()),
                )
            else:
                cursor = await self._conn.execute(
                    """
                    UPDATE wiki_maintenance_watermarks
                    SET revision = ?, updated_at = ?
                    WHERE consumer_id = ? AND revision = ?
                    """,
                    (revision, now.isoformat(), consumer_id, expected_revision),
                )
            if cursor.rowcount != 1:
                raise conflict("wiki maintenance watermark changed before advance")
        return now
