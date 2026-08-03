"""Durable SQLite queue state for explicit wiki-edit curation."""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Self

import aiosqlite

from arden.database import connect

_SCHEMA = """
CREATE TABLE IF NOT EXISTS wiki_edit_curator_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wiki_edit_curator_jobs (
    commit_id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'done')),
    outcome TEXT CHECK (outcome IN ('ignored', 'no_change', 'committed')),
    plan_id TEXT,
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at TEXT NOT NULL,
    locked_at TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (
        (status = 'pending' AND outcome IS NULL AND locked_at IS NULL)
        OR (status = 'running' AND outcome IS NULL AND locked_at IS NOT NULL)
        OR (status = 'done' AND outcome IS NOT NULL AND locked_at IS NULL)
    ),
    CHECK (outcome != 'committed' OR plan_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_wiki_edit_curator_jobs_ready
    ON wiki_edit_curator_jobs(status, available_at, created_at);
"""


class WikiEditCuratorJobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"


class WikiEditCuratorJobOutcome(StrEnum):
    IGNORED = "ignored"
    NO_CHANGE = "no_change"
    COMMITTED = "committed"


class WikiEditCuratorQueueConflictError(RuntimeError):
    """A claimed queue job changed before its worker transition."""


@dataclass(frozen=True, slots=True)
class WikiEditCuratorJob:
    commit_id: str
    status: WikiEditCuratorJobStatus
    outcome: WikiEditCuratorJobOutcome | None
    plan_id: str | None
    attempts: int
    available_at: datetime
    locked_at: datetime | None
    last_error: str | None
    created_at: datetime
    updated_at: datetime


def _revision(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError("commit_id must be a 64-character lowercase revision ID")
    return value


def _text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _timestamp(value: object, name: str) -> datetime:
    if not isinstance(value, str):
        raise RuntimeError(f"persisted wiki edit curator {name} is invalid")
    try:
        point = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError(f"persisted wiki edit curator {name} is invalid") from exc
    if point.tzinfo is None or point.utcoffset() != timedelta(0):
        raise RuntimeError(f"persisted wiki edit curator {name} is not UTC")
    return point.astimezone(UTC)


def _job(row: aiosqlite.Row) -> WikiEditCuratorJob:
    try:
        status = WikiEditCuratorJobStatus(row["status"])
        outcome = None if row["outcome"] is None else WikiEditCuratorJobOutcome(row["outcome"])
        attempts = row["attempts"]
        if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 0:
            raise RuntimeError("persisted wiki edit curator attempts are invalid")
        plan_id = None if row["plan_id"] is None else _text("plan_id", row["plan_id"])
        locked_at = None if row["locked_at"] is None else _timestamp(row["locked_at"], "locked_at")
        last_error = None if row["last_error"] is None else _text("last_error", row["last_error"])
        result = WikiEditCuratorJob(
            commit_id=_revision(row["commit_id"]),
            status=status,
            outcome=outcome,
            plan_id=plan_id,
            attempts=attempts,
            available_at=_timestamp(row["available_at"], "available_at"),
            locked_at=locked_at,
            last_error=last_error,
            created_at=_timestamp(row["created_at"], "created_at"),
            updated_at=_timestamp(row["updated_at"], "updated_at"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("persisted wiki edit curator job is invalid") from exc
    if status is WikiEditCuratorJobStatus.DONE:
        if outcome is None or locked_at is not None:
            raise RuntimeError("persisted completed wiki edit curator job is invalid")
        if outcome is WikiEditCuratorJobOutcome.COMMITTED and plan_id is None:
            raise RuntimeError("persisted committed wiki edit curator job lacks a plan")
    elif outcome is not None or (status is WikiEditCuratorJobStatus.RUNNING) != (locked_at is not None):
        raise RuntimeError("persisted open wiki edit curator job is invalid")
    return result


class WikiEditCuratorQueueStore:
    """One idempotent durable job per immutable wiki commit."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        if conn.isolation_level is not None:
            raise ValueError("WikiEditCuratorQueueStore requires an autocommit connection")
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

    async def enqueue(self, commit_id: str) -> bool:
        """Persist one exact commit without loading wiki or fact state."""

        commit_id = _revision(commit_id)
        now = datetime.now(UTC).isoformat()
        cursor = await self._conn.execute(
            """
            INSERT OR IGNORE INTO wiki_edit_curator_jobs (
                commit_id, status, available_at, created_at, updated_at
            )
            VALUES (?, 'pending', ?, ?, ?)
            """,
            (commit_id, now, now, now),
        )
        return cursor.rowcount == 1

    async def get_reconciliation_revision(self) -> str | None:
        rows = await self._conn.execute_fetchall(
            "SELECT value FROM wiki_edit_curator_meta WHERE key = 'reconciliation_revision'"
        )
        if not rows:
            return None
        return _revision(rows[0]["value"])

    async def record_reconciliation_revision(self, *, expected: str | None, revision: str) -> None:
        if expected is not None:
            expected = _revision(expected)
        revision = _revision(revision)
        async with self._lock:
            current = await self.get_reconciliation_revision()
            if current != expected:
                raise WikiEditCuratorQueueConflictError("wiki curator reconciliation revision changed")
            await self._conn.execute(
                """
                INSERT INTO wiki_edit_curator_meta(key, value)
                VALUES('reconciliation_revision', ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (revision,),
            )

    async def get(self, commit_id: str) -> WikiEditCuratorJob | None:
        commit_id = _revision(commit_id)
        rows = await self._conn.execute_fetchall(
            "SELECT * FROM wiki_edit_curator_jobs WHERE commit_id = ?",
            (commit_id,),
        )
        return None if not rows else _job(rows[0])

    async def claim_next(self) -> WikiEditCuratorJob | None:
        async with self._lock:
            now = datetime.now(UTC).isoformat()
            rows = await self._conn.execute_fetchall(
                """
                SELECT commit_id
                FROM wiki_edit_curator_jobs
                WHERE status = 'pending' AND available_at <= ?
                ORDER BY created_at, commit_id
                LIMIT 1
                """,
                (now,),
            )
            if not rows:
                return None
            commit_id = rows[0]["commit_id"]
            cursor = await self._conn.execute(
                """
                UPDATE wiki_edit_curator_jobs
                SET status = 'running',
                    attempts = attempts + 1,
                    locked_at = ?,
                    updated_at = ?
                WHERE commit_id = ? AND status = 'pending' AND available_at <= ?
                """,
                (now, now, commit_id, now),
            )
            if cursor.rowcount != 1:
                return None
            result = await self.get(commit_id)
            assert result is not None
            return result

    async def attach_plan(self, commit_id: str, plan_id: str) -> WikiEditCuratorJob:
        commit_id = _revision(commit_id)
        plan_id = _text("plan_id", plan_id)
        now = datetime.now(UTC).isoformat()
        cursor = await self._conn.execute(
            """
            UPDATE wiki_edit_curator_jobs
            SET plan_id = ?, updated_at = ?
            WHERE commit_id = ?
              AND status = 'running'
              AND (plan_id IS NULL OR plan_id = ?)
            """,
            (plan_id, now, commit_id, plan_id),
        )
        if cursor.rowcount != 1:
            raise WikiEditCuratorQueueConflictError("wiki edit curator job changed before attaching its plan")
        result = await self.get(commit_id)
        assert result is not None
        return result

    async def complete(
        self,
        commit_id: str,
        outcome: WikiEditCuratorJobOutcome,
        *,
        plan_id: str | None = None,
    ) -> WikiEditCuratorJob:
        commit_id = _revision(commit_id)
        if not isinstance(outcome, WikiEditCuratorJobOutcome):
            raise TypeError("outcome must be a WikiEditCuratorJobOutcome")
        if outcome is WikiEditCuratorJobOutcome.COMMITTED:
            plan_id = _text("plan_id", plan_id)
        elif plan_id is not None:
            raise ValueError("only committed jobs may retain a plan_id")
        now = datetime.now(UTC).isoformat()
        if outcome is WikiEditCuratorJobOutcome.COMMITTED:
            cursor = await self._conn.execute(
                """
                UPDATE wiki_edit_curator_jobs
                SET status = 'done',
                    outcome = ?,
                    locked_at = NULL,
                    last_error = NULL,
                    updated_at = ?
                WHERE commit_id = ? AND status = 'running' AND plan_id = ?
                """,
                (outcome.value, now, commit_id, plan_id),
            )
        else:
            cursor = await self._conn.execute(
                """
                UPDATE wiki_edit_curator_jobs
                SET status = 'done',
                    outcome = ?,
                    locked_at = NULL,
                    last_error = NULL,
                    updated_at = ?
                WHERE commit_id = ? AND status = 'running' AND plan_id IS NULL
                """,
                (outcome.value, now, commit_id),
            )
        if cursor.rowcount != 1:
            raise WikiEditCuratorQueueConflictError("wiki edit curator job changed before completion")
        result = await self.get(commit_id)
        assert result is not None
        return result

    async def retry(self, commit_id: str, error: str, *, available_at: datetime) -> WikiEditCuratorJob:
        commit_id = _revision(commit_id)
        error = _text("error", error)
        if available_at.tzinfo is None:
            raise ValueError("available_at must be timezone-aware")
        available_at = available_at.astimezone(UTC)
        now = datetime.now(UTC).isoformat()
        cursor = await self._conn.execute(
            """
            UPDATE wiki_edit_curator_jobs
            SET status = 'pending',
                available_at = ?,
                locked_at = NULL,
                last_error = ?,
                updated_at = ?
            WHERE commit_id = ? AND status = 'running'
            """,
            (available_at.isoformat(), error, now, commit_id),
        )
        if cursor.rowcount != 1:
            raise WikiEditCuratorQueueConflictError("wiki edit curator job changed before retry")
        result = await self.get(commit_id)
        assert result is not None
        return result

    async def release(self, commit_id: str) -> None:
        commit_id = _revision(commit_id)
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            """
            UPDATE wiki_edit_curator_jobs
            SET status = 'pending',
                available_at = ?,
                locked_at = NULL,
                updated_at = ?
            WHERE commit_id = ? AND status = 'running'
            """,
            (now, now, commit_id),
        )

    async def release_all_running(self) -> int:
        now = datetime.now(UTC).isoformat()
        cursor = await self._conn.execute(
            """
            UPDATE wiki_edit_curator_jobs
            SET status = 'pending',
                available_at = ?,
                locked_at = NULL,
                updated_at = ?
            WHERE status = 'running'
            """,
            (now, now),
        )
        return cursor.rowcount
