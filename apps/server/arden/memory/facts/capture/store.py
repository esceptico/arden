"""Durable per-session cursors for transcript-stream consumers."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import aiosqlite

from arden.database import connect

_SCHEMA = """
CREATE TABLE IF NOT EXISTS session_consumer_watermarks (
    consumer_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    last_seq INTEGER NOT NULL,
    pending_plan_id TEXT,
    pending_to_seq INTEGER,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (consumer_id, session_id)
);
"""


class SessionConsumerWatermarkError(RuntimeError):
    """A persisted transcript-consumer watermark is invalid or stale."""


@dataclass(frozen=True, slots=True)
class SessionConsumerWatermark:
    consumer_id: str
    session_id: str
    last_seq: int
    pending_plan_id: str | None
    pending_to_seq: int | None
    updated_at: datetime


def _text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _seq(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")
    return value


def _row(row: aiosqlite.Row) -> SessionConsumerWatermark:
    try:
        updated_at = datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError) as exc:
        raise SessionConsumerWatermarkError("persisted session consumer watermark is invalid") from exc
    if updated_at.tzinfo is None or updated_at.utcoffset() != timedelta(0):
        raise SessionConsumerWatermarkError("persisted session consumer watermark is not UTC")
    pending_plan_id = row["pending_plan_id"]
    pending_to_seq = row["pending_to_seq"]
    if (pending_plan_id is None) != (pending_to_seq is None):
        raise SessionConsumerWatermarkError("persisted pending plan is incomplete")
    return SessionConsumerWatermark(
        _text("consumer_id", row["consumer_id"]),
        _text("session_id", row["session_id"]),
        _seq("last_seq", row["last_seq"]),
        None if pending_plan_id is None else _text("pending_plan_id", pending_plan_id),
        None if pending_to_seq is None else _seq("pending_to_seq", pending_to_seq),
        updated_at,
    )


class SessionConsumerWatermarkStore:
    """Durable cursors on a dedicated autocommit connection."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        if conn.isolation_level is not None:
            raise ValueError("SessionConsumerWatermarkStore requires an autocommit connection")
        self._conn = conn

    @classmethod
    async def open(cls, db_path: Path) -> "SessionConsumerWatermarkStore":
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

    async def list_for_consumer(self, consumer_id: str) -> tuple[SessionConsumerWatermark, ...]:
        consumer_id = _text("consumer_id", consumer_id)
        rows = await self._conn.execute_fetchall(
            """
            SELECT consumer_id, session_id, last_seq, pending_plan_id, pending_to_seq, updated_at
            FROM session_consumer_watermarks
            WHERE consumer_id = ?
            ORDER BY session_id
            """,
            (consumer_id,),
        )
        return tuple(_row(row) for row in rows)

    async def set_pending(self, consumer_id: str, session_id: str, *, plan_id: str, to_seq: int) -> None:
        """Bind the next advance to one exact fact plan before committing it."""

        consumer_id = _text("consumer_id", consumer_id)
        session_id = _text("session_id", session_id)
        plan_id = _text("plan_id", plan_id)
        to_seq = _seq("to_seq", to_seq)
        updated_at = datetime.now(UTC).isoformat()
        cursor = await self._conn.execute(
            """
            INSERT INTO session_consumer_watermarks (
                consumer_id, session_id, last_seq, pending_plan_id, pending_to_seq, updated_at
            ) VALUES (?, ?, 0, ?, ?, ?)
            ON CONFLICT(consumer_id, session_id) DO UPDATE
            SET pending_plan_id = excluded.pending_plan_id,
                pending_to_seq = excluded.pending_to_seq,
                updated_at = excluded.updated_at
            WHERE session_consumer_watermarks.pending_plan_id IS NULL
                OR session_consumer_watermarks.pending_plan_id = excluded.pending_plan_id
            """,
            (consumer_id, session_id, plan_id, to_seq, updated_at),
        )
        if cursor.rowcount != 1:
            raise SessionConsumerWatermarkError("another capture plan is already pending for this session")

    async def advance(self, consumer_id: str, session_id: str, *, last_seq: int) -> None:
        """Advance the cursor and clear any pending plan bound at or below it."""

        consumer_id = _text("consumer_id", consumer_id)
        session_id = _text("session_id", session_id)
        last_seq = _seq("last_seq", last_seq)
        updated_at = datetime.now(UTC).isoformat()
        cursor = await self._conn.execute(
            """
            INSERT INTO session_consumer_watermarks (
                consumer_id, session_id, last_seq, pending_plan_id, pending_to_seq, updated_at
            ) VALUES (?, ?, ?, NULL, NULL, ?)
            ON CONFLICT(consumer_id, session_id) DO UPDATE
            SET last_seq = excluded.last_seq,
                pending_plan_id = NULL,
                pending_to_seq = NULL,
                updated_at = excluded.updated_at
            WHERE session_consumer_watermarks.last_seq < excluded.last_seq
            """,
            (consumer_id, session_id, last_seq, updated_at),
        )
        if cursor.rowcount != 1:
            raise SessionConsumerWatermarkError("session consumer watermark moved past this advance")

    async def close(self) -> None:
        await self._conn.close()
