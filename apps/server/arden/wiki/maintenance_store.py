"""Durable review state and a checkpoint for Wiki Maintenance.

This store owns neither wiki history nor maintenance policy.  Callers supply
the ordered commits they reviewed; the store makes the resulting review rows
and checkpoint durable on its own autocommit SQLite connection.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from arden.database import connect

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from pathlib import Path

    import aiosqlite


CONSUMER_ID = "wiki.maintenance"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS wiki_maintenance_watermarks (
    consumer_id TEXT PRIMARY KEY,
    revision TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS wiki_maintenance_reviews (
    review_id TEXT PRIMARY KEY,
    blocking_commit_id TEXT NOT NULL,
    evidence_key TEXT NOT NULL,
    evidence_fingerprint TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK (generation >= 0),
    status TEXT NOT NULL CHECK (status IN ('needs_review', 'accepted', 'rejected', 'resolved_manual')),
    summary TEXT NOT NULL,
    proposal_json TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    resolved_at TEXT,
    decision_note TEXT,
    UNIQUE (blocking_commit_id, evidence_key),
    CHECK (
        (status = 'needs_review' AND resolved_at IS NULL AND decision_note IS NULL)
        OR (status IN ('accepted', 'rejected') AND resolved_at IS NOT NULL)
        OR (
            status = 'resolved_manual'
            AND resolved_at IS NOT NULL
            AND decision_note IS NOT NULL
            AND length(trim(decision_note)) > 0
        )
    ),
    CHECK (decision_note IS NULL OR length(trim(decision_note)) > 0)
);

CREATE INDEX IF NOT EXISTS idx_wiki_maintenance_reviews_pending
    ON wiki_maintenance_reviews(status, blocking_commit_id, created_at)
    WHERE status = 'needs_review';
"""


class WikiMaintenanceReviewStatus(StrEnum):
    NEEDS_REVIEW = "needs_review"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    RESOLVED_MANUAL = "resolved_manual"


class WikiMaintenanceReviewAction(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    RESOLVE_MANUAL = "resolve_manual"


class WikiMaintenanceWatermarkConflictError(RuntimeError):
    """The checkpoint changed before this caller could advance it."""


class WikiMaintenanceReviewConflictError(RuntimeError):
    """A review changed generation or was already resolved."""


@dataclass(frozen=True, slots=True)
class WikiMaintenanceWatermark:
    consumer_id: str
    revision: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class WikiMaintenanceReview:
    review_id: str
    blocking_commit_id: str
    evidence_key: str
    evidence_fingerprint: str
    generation: int
    status: WikiMaintenanceReviewStatus
    summary: str
    proposal_json: str | None
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    decision_note: str | None


@dataclass(frozen=True, slots=True)
class WikiMaintenanceReviewInput:
    """One finding produced while reviewing ``blocking_commit_id``."""

    blocking_commit_id: str
    evidence_key: str
    evidence_fingerprint: str
    summary: str
    proposal_json: str | dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class WikiMaintenanceApplyResult:
    """The persisted findings and the checkpoint reached by one maintenance run."""

    reviews: tuple[WikiMaintenanceReview, ...]
    watermark: WikiMaintenanceWatermark | None


def _text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _revision(name: str, value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ValueError(f"{name} must be a 64-character lowercase revision ID")
    return value


def _fingerprint(value: object) -> str:
    return _revision("evidence_fingerprint", value)


def _proposal_json(value: str | dict[str, Any] | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("proposal_json must contain valid JSON") from exc
    elif isinstance(value, dict):
        decoded = value
    else:
        raise TypeError("proposal_json must be a JSON object, JSON object string, or None")
    if not isinstance(decoded, dict):
        raise ValueError("proposal_json must be a JSON object")
    try:
        return json.dumps(decoded, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("proposal_json must be JSON-serializable") from exc


def _timestamp(value: object, name: str) -> datetime:
    try:
        timestamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError(f"persisted wiki maintenance {name} is invalid") from exc
    if timestamp.tzinfo is None or timestamp.utcoffset() != timedelta(0):
        raise RuntimeError(f"persisted wiki maintenance {name} is not UTC")
    return timestamp


def _watermark_row(row: aiosqlite.Row) -> WikiMaintenanceWatermark:
    return WikiMaintenanceWatermark(
        consumer_id=_text("consumer_id", row["consumer_id"]),
        revision=_revision("revision", row["revision"]),
        updated_at=_timestamp(row["updated_at"], "watermark timestamp"),
    )


def _review_row(row: aiosqlite.Row) -> WikiMaintenanceReview:
    try:
        status = WikiMaintenanceReviewStatus(row["status"])
    except ValueError as exc:
        raise RuntimeError("persisted wiki maintenance review has an invalid status") from exc
    generation = row["generation"]
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 0:
        raise RuntimeError("persisted wiki maintenance review has an invalid generation")
    proposal_json = row["proposal_json"]
    if proposal_json is not None:
        _proposal_json(proposal_json)
    resolved_at = None if row["resolved_at"] is None else _timestamp(row["resolved_at"], "resolved_at")
    decision_note = row["decision_note"]
    if decision_note is not None:
        decision_note = _text("decision_note", decision_note)
    if status is WikiMaintenanceReviewStatus.NEEDS_REVIEW:
        if resolved_at is not None or decision_note is not None:
            raise RuntimeError("persisted open wiki maintenance review has a decision")
    elif resolved_at is None:
        raise RuntimeError("persisted resolved wiki maintenance review lacks a timestamp")
    elif status is WikiMaintenanceReviewStatus.RESOLVED_MANUAL and decision_note is None:
        raise RuntimeError("persisted manually resolved wiki maintenance review lacks a note")
    return WikiMaintenanceReview(
        review_id=_text("review_id", row["review_id"]),
        blocking_commit_id=_revision("blocking_commit_id", row["blocking_commit_id"]),
        evidence_key=_text("evidence_key", row["evidence_key"]),
        evidence_fingerprint=_fingerprint(row["evidence_fingerprint"]),
        generation=generation,
        status=status,
        summary=_text("summary", row["summary"]),
        proposal_json=proposal_json,
        created_at=_timestamp(row["created_at"], "created_at"),
        updated_at=_timestamp(row["updated_at"], "updated_at"),
        resolved_at=resolved_at,
        decision_note=decision_note,
    )


class WikiMaintenanceStore:
    """Durable Wiki Maintenance state on a dedicated autocommit connection."""

    def __init__(self, conn: aiosqlite.Connection) -> None:
        if conn.isolation_level is not None:
            raise ValueError("WikiMaintenanceStore requires an autocommit connection")
        self._conn = conn
        self._lock = asyncio.Lock()

    @classmethod
    async def open(cls, db_path: Path) -> WikiMaintenanceStore:
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

    async def get_review(self, review_id: str) -> WikiMaintenanceReview | None:
        review_id = _text("review_id", review_id)
        rows = await self._conn.execute_fetchall(
            "SELECT * FROM wiki_maintenance_reviews WHERE review_id = ?", (review_id,)
        )
        return None if not rows else _review_row(rows[0])

    async def get_by_evidence(
        self,
        blocking_commit_id: str,
        evidence_key: str,
    ) -> WikiMaintenanceReview | None:
        blocking_commit_id = _revision("blocking_commit_id", blocking_commit_id)
        evidence_key = _text("evidence_key", evidence_key)
        rows = await self._conn.execute_fetchall(
            "SELECT * FROM wiki_maintenance_reviews WHERE blocking_commit_id = ? AND evidence_key = ?",
            (blocking_commit_id, evidence_key),
        )
        return None if not rows else _review_row(rows[0])

    async def list_pending(self) -> list[WikiMaintenanceReview]:
        rows = await self._conn.execute_fetchall(
            "SELECT * FROM wiki_maintenance_reviews WHERE status = 'needs_review' ORDER BY created_at, review_id"
        )
        return [_review_row(row) for row in rows]

    async def list_history(self) -> list[WikiMaintenanceReview]:
        rows = await self._conn.execute_fetchall(
            "SELECT * FROM wiki_maintenance_reviews ORDER BY created_at, review_id"
        )
        return [_review_row(row) for row in rows]

    async def resolve(
        self,
        review_id: str,
        *,
        expected_generation: int,
        action: WikiMaintenanceReviewAction,
        decision_note: str | None = None,
    ) -> WikiMaintenanceReview:
        """Resolve an open generation; stale UI actions are rejected."""

        review_id = _text("review_id", review_id)
        if isinstance(expected_generation, bool) or not isinstance(expected_generation, int) or expected_generation < 0:
            raise ValueError("expected_generation must be a nonnegative integer")
        if not isinstance(action, WikiMaintenanceReviewAction):
            raise ValueError("action must be accept, reject, or resolve_manual")
        if action is WikiMaintenanceReviewAction.RESOLVE_MANUAL or decision_note is not None:
            decision_note = _text("decision_note", decision_note)
        now = datetime.now(UTC).isoformat()
        status = {
            WikiMaintenanceReviewAction.ACCEPT: WikiMaintenanceReviewStatus.ACCEPTED,
            WikiMaintenanceReviewAction.REJECT: WikiMaintenanceReviewStatus.REJECTED,
            WikiMaintenanceReviewAction.RESOLVE_MANUAL: WikiMaintenanceReviewStatus.RESOLVED_MANUAL,
        }[action]
        async with self._lock:
            cursor = await self._conn.execute(
                """
                UPDATE wiki_maintenance_reviews
                SET status = ?, decision_note = ?, resolved_at = ?, updated_at = ?
                WHERE review_id = ? AND generation = ? AND status = 'needs_review'
                """,
                (status.value, decision_note, now, now, review_id, expected_generation),
            )
            if cursor.rowcount != 1:
                raise WikiMaintenanceReviewConflictError("review changed before resolution")
            result = await self.get_review(review_id)
            assert result is not None
            return result

    async def apply_run(
        self,
        *,
        expected_revision: str | None,
        ordered_commit_ids: Sequence[str],
        reviewed_through: str | None,
        reviews: Sequence[WikiMaintenanceReviewInput] = (),
    ) -> WikiMaintenanceApplyResult:
        """Atomically save findings and a reviewed contiguous checkpoint.

        ``ordered_commit_ids`` is the caller-validated full chronological
        chain, including non-Markdown commits. ``reviewed_through`` may stop
        at any commit in that chain, so findings for later commits remain
        durable while only the prior safe prefix advances. For a run with no
        relevant Markdown commits, callers may pass an empty chain and its
        already-validated through revision.
        """

        if expected_revision is not None:
            expected_revision = _revision("expected_revision", expected_revision)
        if reviewed_through is not None:
            reviewed_through = _revision("reviewed_through", reviewed_through)
        commit_ids = tuple(_revision("ordered_commit_ids", commit_id) for commit_id in ordered_commit_ids)
        if len(set(commit_ids)) != len(commit_ids):
            raise ValueError("ordered_commit_ids must not repeat commit IDs")
        if expected_revision is not None and expected_revision in commit_ids:
            raise ValueError("ordered_commit_ids must contain only commits after expected_revision")
        if reviewed_through is not None and commit_ids and reviewed_through not in commit_ids:
            raise ValueError("reviewed_through must occur in ordered_commit_ids")
        if reviewed_through is None and expected_revision is not None and commit_ids:
            raise ValueError("reviewed_through is required when ordered_commit_ids are supplied")
        normalized = tuple(self._review_input(review) for review in reviews)
        if any(review.blocking_commit_id not in commit_ids for review in normalized):
            raise ValueError("review blocking_commit_id must occur in ordered_commit_ids")

        reviewed_prefix = self._reviewed_prefix(commit_ids, reviewed_through)

        async with self._lock, self._transaction():
            watermark = await self.get_watermark()
            actual_revision = None if watermark is None else watermark.revision
            if actual_revision != expected_revision:
                raise WikiMaintenanceWatermarkConflictError("wiki maintenance watermark changed before the run began")
            persisted = tuple([await self._create_or_refresh(review) for review in normalized])
            if reviewed_through is not None and reviewed_through != expected_revision:
                if not await self._has_open_review(reviewed_prefix):
                    watermark = await self._advance(expected_revision=expected_revision, revision=reviewed_through)
            return WikiMaintenanceApplyResult(persisted, watermark)

    def _review_input(self, review: WikiMaintenanceReviewInput) -> WikiMaintenanceReviewInput:
        if not isinstance(review, WikiMaintenanceReviewInput):
            raise TypeError("review must be a WikiMaintenanceReviewInput")
        return WikiMaintenanceReviewInput(
            blocking_commit_id=_revision("blocking_commit_id", review.blocking_commit_id),
            evidence_key=_text("evidence_key", review.evidence_key),
            evidence_fingerprint=_fingerprint(review.evidence_fingerprint),
            summary=_text("summary", review.summary),
            proposal_json=_proposal_json(review.proposal_json),
        )

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[None]:
        await self._begin_transaction()
        try:
            yield
        except BaseException:
            await self._rollback_uninterruptibly()
            raise
        else:
            commit = asyncio.create_task(self._conn.commit())
            try:
                await asyncio.shield(commit)
            except BaseException:
                with suppress(BaseException):
                    await self._finish_uninterruptibly(commit)
                if self._conn.in_transaction:
                    await self._rollback_uninterruptibly()
                raise

    async def _begin_transaction(self) -> None:
        begin = asyncio.create_task(self._conn.execute("BEGIN IMMEDIATE"))
        try:
            await asyncio.shield(begin)
        except BaseException:
            with suppress(BaseException):
                await self._finish_uninterruptibly(begin)
            if self._conn.in_transaction:
                await self._rollback_uninterruptibly()
            raise

    async def _rollback_uninterruptibly(self) -> None:
        rollback = asyncio.create_task(self._conn.rollback())
        await self._finish_uninterruptibly(rollback)

    @staticmethod
    async def _finish_uninterruptibly(task: asyncio.Task[Any]) -> Any:
        while True:
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                continue

    async def _create_or_refresh(self, review: WikiMaintenanceReviewInput) -> WikiMaintenanceReview:
        rows = await self._conn.execute_fetchall(
            "SELECT * FROM wiki_maintenance_reviews WHERE blocking_commit_id = ? AND evidence_key = ?",
            (review.blocking_commit_id, review.evidence_key),
        )
        now = datetime.now(UTC).isoformat()
        if not rows:
            review_id = uuid4().hex
            await self._conn.execute(
                """
                INSERT INTO wiki_maintenance_reviews (
                    review_id, blocking_commit_id, evidence_key, evidence_fingerprint,
                    generation, status, summary, proposal_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 0, 'needs_review', ?, ?, ?, ?)
                """,
                (
                    review_id,
                    review.blocking_commit_id,
                    review.evidence_key,
                    review.evidence_fingerprint,
                    review.summary,
                    review.proposal_json,
                    now,
                    now,
                ),
            )
            created = await self.get_review(review_id)
            assert created is not None
            return created

        existing = _review_row(rows[0])
        if existing.evidence_fingerprint == review.evidence_fingerprint:
            return existing
        await self._conn.execute(
            """
            UPDATE wiki_maintenance_reviews
            SET evidence_fingerprint = ?, generation = generation + 1,
                status = 'needs_review', summary = ?, proposal_json = ?,
                updated_at = ?, resolved_at = NULL, decision_note = NULL
            WHERE review_id = ?
            """,
            (
                review.evidence_fingerprint,
                review.summary,
                review.proposal_json,
                now,
                existing.review_id,
            ),
        )
        refreshed = await self.get_review(existing.review_id)
        assert refreshed is not None
        return refreshed

    @staticmethod
    def _reviewed_prefix(commit_ids: Sequence[str], reviewed_through: str | None) -> tuple[str, ...]:
        if reviewed_through is None:
            return ()
        if not commit_ids:
            return ()
        return commit_ids[: commit_ids.index(reviewed_through) + 1]

    async def _has_open_review(self, commit_ids: Sequence[str]) -> bool:
        if not commit_ids:
            return False
        placeholders = ", ".join("?" for _ in commit_ids)
        rows = await self._conn.execute_fetchall(
            "SELECT blocking_commit_id FROM wiki_maintenance_reviews "
            f"WHERE status = 'needs_review' AND blocking_commit_id IN ({placeholders}) LIMIT 1",
            tuple(commit_ids),
        )
        return bool(rows)

    async def _advance(
        self,
        *,
        expected_revision: str | None,
        revision: str,
    ) -> WikiMaintenanceWatermark:
        now = datetime.now(UTC).isoformat()
        if expected_revision is None:
            cursor = await self._conn.execute(
                """
                INSERT INTO wiki_maintenance_watermarks (consumer_id, revision, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(consumer_id) DO NOTHING
                """,
                (CONSUMER_ID, revision, now),
            )
        else:
            cursor = await self._conn.execute(
                """
                UPDATE wiki_maintenance_watermarks
                SET revision = ?, updated_at = ?
                WHERE consumer_id = ? AND revision = ?
                """,
                (revision, now, CONSUMER_ID, expected_revision),
            )
        if cursor.rowcount != 1:
            raise WikiMaintenanceWatermarkConflictError("wiki maintenance watermark changed before advance")
        return WikiMaintenanceWatermark(CONSUMER_ID, revision, datetime.fromisoformat(now))
