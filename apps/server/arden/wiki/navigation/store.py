"""Durable compare-and-swap checkpoint for the wiki navigation projection."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Self

import aiosqlite

from arden.database import connect

CONSUMER_ID = "wiki.navigation"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS wiki_navigation_watermarks (
    consumer_id TEXT PRIMARY KEY,
    revision TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class WikiNavigationWatermarkConflictError(RuntimeError):
    """The projection checkpoint changed before this run could advance it."""


@dataclass(frozen=True, slots=True)
class WikiNavigationWatermark:
    consumer_id: str
    revision: str
    updated_at: datetime


def _revision(value: object, name: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a 64-character lowercase revision ID")
    return value


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise RuntimeError("persisted wiki navigation watermark timestamp is invalid")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError("persisted wiki navigation watermark timestamp is invalid") from exc
    if result.tzinfo is None or result.utcoffset() != timedelta(0):
        raise RuntimeError("persisted wiki navigation watermark timestamp is not UTC")
    return result


class WikiNavigationStore:
    """Dedicated autocommit state for one deterministic wiki projection."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        if conn.isolation_level is not None:
            raise ValueError("WikiNavigationStore requires an autocommit connection")
        self._conn = conn

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

    async def get(self) -> WikiNavigationWatermark | None:
        rows = await self._conn.execute_fetchall(
            "SELECT consumer_id, revision, updated_at FROM wiki_navigation_watermarks WHERE consumer_id = ?",
            (CONSUMER_ID,),
        )
        if not rows:
            return None
        row = rows[0]
        if row["consumer_id"] != CONSUMER_ID:
            raise RuntimeError("persisted wiki navigation watermark has an invalid consumer")
        return WikiNavigationWatermark(
            CONSUMER_ID, _revision(row["revision"], "revision"), _timestamp(row["updated_at"])
        )

    async def advance(
        self,
        *,
        expected_revision: str | None,
        revision: str,
    ) -> WikiNavigationWatermark:
        """Advance exactly once from the checkpoint observed by this run."""

        if expected_revision is not None:
            expected_revision = _revision(expected_revision, "expected_revision")
        revision = _revision(revision, "revision")
        updated_at = datetime.now(UTC).isoformat()
        if expected_revision is None:
            cursor = await self._conn.execute(
                """
                INSERT INTO wiki_navigation_watermarks (consumer_id, revision, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(consumer_id) DO NOTHING
                """,
                (CONSUMER_ID, revision, updated_at),
            )
        else:
            cursor = await self._conn.execute(
                """
                UPDATE wiki_navigation_watermarks
                SET revision = ?, updated_at = ?
                WHERE consumer_id = ? AND revision = ?
                """,
                (revision, updated_at, CONSUMER_ID, expected_revision),
            )
        if cursor.rowcount != 1:
            raise WikiNavigationWatermarkConflictError("wiki navigation watermark changed before advance")
        return WikiNavigationWatermark(CONSUMER_ID, revision, datetime.fromisoformat(updated_at))
