"""Durable approval records for wiki rename plans.

This deliberately stores only the approval boundary.  Planning, revision
checks, and the actual commit stay in the wiki service.
"""

import asyncio
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import aiosqlite


class WikiRenameApprovalStatus(StrEnum):
    PENDING = "pending"
    APPLYING = "applying"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


@dataclass(frozen=True, slots=True)
class WikiRenameApproval:
    approval_id: str
    request_key: str
    request_fingerprint: str
    generation: int
    base_head: str | None
    plan_json: str
    old_path: str
    new_path: str
    link_count: int
    page_count: int
    status: WikiRenameApprovalStatus
    created_at: datetime
    resolved_at: datetime | None
    commit_id: str | None
    replacement_approval_id: str | None
    resolution: str | None
    claim_owner: str | None
    claim_expires_at: datetime | None


class PendingWikiRenameApprovalConflictError(ValueError):
    """A request already has an open approval for a different plan."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS wiki_rename_approvals (
    approval_id TEXT PRIMARY KEY,
    request_key TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    generation INTEGER NOT NULL CHECK (generation >= 0),
    base_head TEXT,
    plan_json TEXT NOT NULL,
    old_path TEXT NOT NULL,
    new_path TEXT NOT NULL,
    link_count INTEGER NOT NULL CHECK (link_count >= 0),
    page_count INTEGER NOT NULL CHECK (page_count >= 0),
    status TEXT NOT NULL CHECK (status IN ('pending', 'applying', 'accepted', 'rejected', 'superseded')),
    created_at TEXT NOT NULL,
    resolved_at TEXT,
    commit_id TEXT,
    replacement_approval_id TEXT REFERENCES wiki_rename_approvals(approval_id),
    resolution TEXT,
    claim_owner TEXT,
    claim_expires_at TEXT,
    CHECK (status != 'accepted' OR commit_id IS NOT NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_wiki_rename_approvals_open_request
    ON wiki_rename_approvals(request_key)
    WHERE status IN ('pending', 'applying');

CREATE INDEX IF NOT EXISTS idx_wiki_rename_approvals_open_created
    ON wiki_rename_approvals(status, created_at);
"""

_CLAIM_LEASE = timedelta(minutes=2)


def _required_text(name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _optional_text(name: str, value: str | None) -> str | None:
    if value is not None:
        _required_text(name, value)
    return value


def _nonnegative(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a nonnegative integer")
    return value


def _canonical_plan_json(plan_json: str | dict[str, Any]) -> str:
    if isinstance(plan_json, str):
        try:
            value = json.loads(plan_json)
        except json.JSONDecodeError as exc:
            raise ValueError("plan_json must be valid JSON") from exc
    elif isinstance(plan_json, dict):
        value = plan_json
    else:
        raise TypeError("plan_json must be a JSON object or JSON object string")
    if not isinstance(value, dict):
        raise ValueError("plan_json must be a JSON object")
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("plan_json must be JSON-serializable") from exc


def _row_to_approval(row: aiosqlite.Row) -> WikiRenameApproval:
    return WikiRenameApproval(
        approval_id=row["approval_id"],
        request_key=row["request_key"],
        request_fingerprint=row["request_fingerprint"],
        generation=int(row["generation"]),
        base_head=row["base_head"],
        plan_json=row["plan_json"],
        old_path=row["old_path"],
        new_path=row["new_path"],
        link_count=int(row["link_count"]),
        page_count=int(row["page_count"]),
        status=WikiRenameApprovalStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        resolved_at=datetime.fromisoformat(row["resolved_at"]) if row["resolved_at"] else None,
        commit_id=row["commit_id"],
        replacement_approval_id=row["replacement_approval_id"],
        resolution=row["resolution"],
        claim_owner=row["claim_owner"],
        claim_expires_at=datetime.fromisoformat(row["claim_expires_at"]) if row["claim_expires_at"] else None,
    )


class WikiRenameApprovalStore:
    def __init__(self, conn: aiosqlite.Connection):
        self.conn = conn
        self._lock = asyncio.Lock()

    async def init_schema(self) -> None:
        async with self._lock:
            await self.conn.executescript(_SCHEMA)
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                columns = {
                    row["name"] for row in await self.conn.execute_fetchall("PRAGMA table_info(wiki_rename_approvals)")
                }
                if "claim_owner" not in columns:
                    await self.conn.execute("ALTER TABLE wiki_rename_approvals ADD COLUMN claim_owner TEXT")
                if "claim_expires_at" not in columns:
                    await self.conn.execute("ALTER TABLE wiki_rename_approvals ADD COLUMN claim_expires_at TEXT")
                await self.conn.commit()
            except BaseException:
                await self.conn.rollback()
                raise

    async def create_pending(
        self,
        *,
        approval_id: str,
        request_key: str,
        request_fingerprint: str,
        generation: int,
        base_head: str | None,
        plan_json: str | dict[str, Any],
        old_path: str,
        new_path: str,
        link_count: int,
        page_count: int,
    ) -> WikiRenameApproval:
        """Create or return the matching open approval for a request.

        A distinct fingerprint must first replace or resolve the current
        request; it is never overwritten here.
        """

        _required_text("approval_id", approval_id)
        _required_text("request_key", request_key)
        _required_text("request_fingerprint", request_fingerprint)
        _nonnegative("generation", generation)
        _optional_text("base_head", base_head)
        _required_text("old_path", old_path)
        _required_text("new_path", new_path)
        _nonnegative("link_count", link_count)
        _nonnegative("page_count", page_count)
        canonical_plan = _canonical_plan_json(plan_json)

        async with self._lock:
            return await self._create_pending(
                approval_id=approval_id,
                request_key=request_key,
                request_fingerprint=request_fingerprint,
                generation=generation,
                base_head=base_head,
                canonical_plan=canonical_plan,
                old_path=old_path,
                new_path=new_path,
                link_count=link_count,
                page_count=page_count,
            )

    async def _create_pending(
        self,
        *,
        approval_id: str,
        request_key: str,
        request_fingerprint: str,
        generation: int,
        base_head: str | None,
        canonical_plan: str,
        old_path: str,
        new_path: str,
        link_count: int,
        page_count: int,
    ) -> WikiRenameApproval:
        now = datetime.now(UTC).isoformat()
        cursor = await self.conn.execute(
            """
            INSERT OR IGNORE INTO wiki_rename_approvals (
                approval_id, request_key, request_fingerprint, generation,
                base_head, plan_json, old_path, new_path, link_count, page_count,
                status, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                approval_id,
                request_key,
                request_fingerprint,
                generation,
                base_head,
                canonical_plan,
                old_path,
                new_path,
                link_count,
                page_count,
                now,
            ),
        )
        await self.conn.commit()
        if cursor.rowcount:
            created = await self._get(approval_id)
            assert created is not None
            return created

        existing = await self._get_open_by_request_key(request_key)
        if existing is not None:
            if existing.request_fingerprint == request_fingerprint:
                return existing
            raise PendingWikiRenameApprovalConflictError(
                f"request_key {request_key!r} already has a different open approval"
            )

        same_id = await self._get(approval_id)
        if same_id is not None:
            raise ValueError(f"approval_id {approval_id!r} already exists")
        # A concurrent resolution may have removed the partial-index conflict
        # after INSERT OR IGNORE.  Retrying lets the caller create its next
        # generation without silently reusing a resolved approval.
        return await self._create_pending(
            approval_id=approval_id,
            request_key=request_key,
            request_fingerprint=request_fingerprint,
            generation=generation,
            base_head=base_head,
            canonical_plan=canonical_plan,
            old_path=old_path,
            new_path=new_path,
            link_count=link_count,
            page_count=page_count,
        )

    async def get(self, approval_id: str) -> WikiRenameApproval | None:
        _required_text("approval_id", approval_id)
        async with self._lock:
            return await self._get(approval_id)

    async def _get(self, approval_id: str) -> WikiRenameApproval | None:
        rows = await self.conn.execute_fetchall(
            "SELECT * FROM wiki_rename_approvals WHERE approval_id = ?", (approval_id,)
        )
        return _row_to_approval(rows[0]) if rows else None

    async def list_pending(self) -> list[WikiRenameApproval]:
        """Return open approvals, including crash-retryable applying rows."""

        async with self._lock:
            rows = await self.conn.execute_fetchall(
                """
                SELECT * FROM wiki_rename_approvals
                WHERE status IN ('pending', 'applying')
                ORDER BY created_at ASC, approval_id ASC
                """
            )
            return [_row_to_approval(row) for row in rows]

    async def claim_accept(self, approval_id: str, *, owner: str) -> bool:
        """Claim the right to apply a pending plan before its core commit."""

        _required_text("approval_id", approval_id)
        _required_text("owner", owner)
        async with self._lock:
            now = datetime.now(UTC)
            cursor = await self.conn.execute(
                """
                UPDATE wiki_rename_approvals
                SET status = 'applying', resolution = NULL, claim_owner = ?, claim_expires_at = ?
                WHERE approval_id = ?
                  AND (
                    status = 'pending'
                    OR (
                        status = 'applying'
                        AND (
                            claim_owner = ?
                            OR claim_expires_at IS NULL
                            OR claim_expires_at <= ?
                        )
                    )
                  )
                """,
                (owner, (now + _CLAIM_LEASE).isoformat(), approval_id, owner, now.isoformat()),
            )
            await self.conn.commit()
            return cursor.rowcount > 0

    async def renew_claim(self, approval_id: str, *, owner: str) -> bool:
        """Extend this coordinator's active apply claim."""

        _required_text("approval_id", approval_id)
        _required_text("owner", owner)
        async with self._lock:
            expires_at = (datetime.now(UTC) + _CLAIM_LEASE).isoformat()
            cursor = await self.conn.execute(
                """
                UPDATE wiki_rename_approvals
                SET claim_expires_at = ?
                WHERE approval_id = ? AND status = 'applying' AND claim_owner = ?
                """,
                (expires_at, approval_id, owner),
            )
            await self.conn.commit()
            return cursor.rowcount > 0

    async def release_accept(self, approval_id: str, *, owner: str, resolution: str) -> bool:
        """Return a failed apply claim to a user-resolvable pending state."""

        _required_text("approval_id", approval_id)
        _required_text("owner", owner)
        _required_text("resolution", resolution)
        async with self._lock:
            cursor = await self.conn.execute(
                """
                UPDATE wiki_rename_approvals
                SET status = 'pending', resolution = ?, claim_owner = NULL, claim_expires_at = NULL
                WHERE approval_id = ? AND status = 'applying' AND claim_owner = ?
                """,
                (resolution, approval_id, owner),
            )
            await self.conn.commit()
            return cursor.rowcount > 0

    async def retain_applying(self, approval_id: str, *, owner: str, resolution: str) -> bool:
        """Keep a possibly committed rename unrejectable until an idempotent retry finishes it."""

        _required_text("approval_id", approval_id)
        _required_text("owner", owner)
        _required_text("resolution", resolution)
        async with self._lock:
            cursor = await self.conn.execute(
                """
                UPDATE wiki_rename_approvals
                SET resolution = ?
                WHERE approval_id = ? AND status = 'applying' AND claim_owner = ?
                """,
                (resolution, approval_id, owner),
            )
            await self.conn.commit()
            return cursor.rowcount > 0

    async def accept(
        self,
        approval_id: str,
        *,
        owner: str,
        commit_id: str,
        resolution: str | None = None,
    ) -> bool:
        """Atomically record a completed core commit and accept its approval.

        Retrying with the same commit id is successful; a different decision
        or commit id cannot replace the terminal record.
        """

        _required_text("approval_id", approval_id)
        _required_text("owner", owner)
        _required_text("commit_id", commit_id)
        _optional_text("resolution", resolution)
        async with self._lock:
            now = datetime.now(UTC).isoformat()
            cursor = await self.conn.execute(
                """
                UPDATE wiki_rename_approvals
                SET status = 'accepted', resolved_at = ?, commit_id = ?, resolution = ?,
                    claim_owner = NULL, claim_expires_at = NULL
                WHERE approval_id = ? AND status = 'applying' AND claim_owner = ?
                """,
                (now, commit_id, resolution, approval_id, owner),
            )
            await self.conn.commit()
            if cursor.rowcount:
                return True
            existing = await self._get(approval_id)
            return bool(
                existing and existing.status is WikiRenameApprovalStatus.ACCEPTED and existing.commit_id == commit_id
            )

    async def reject(self, approval_id: str, *, resolution: str | None = None) -> bool:
        async with self._lock:
            return await self._resolve_pending(
                approval_id,
                status=WikiRenameApprovalStatus.REJECTED,
                resolution=resolution,
                replacement_approval_id=None,
            )

    async def supersede(
        self,
        approval_id: str,
        *,
        replacement_approval_id: str,
        resolution: str | None = None,
    ) -> bool:
        _required_text("approval_id", approval_id)
        _required_text("replacement_approval_id", replacement_approval_id)
        if approval_id == replacement_approval_id:
            raise ValueError("replacement_approval_id must differ from approval_id")
        async with self._lock:
            if await self._get(replacement_approval_id) is None:
                raise ValueError("replacement_approval_id must exist")
            return await self._resolve_pending(
                approval_id,
                status=WikiRenameApprovalStatus.SUPERSEDED,
                resolution=resolution,
                replacement_approval_id=replacement_approval_id,
            )

    async def replace_pending(
        self,
        old_approval_id: str,
        *,
        approval_id: str,
        request_key: str,
        request_fingerprint: str,
        generation: int,
        base_head: str | None,
        plan_json: str | dict[str, Any],
        old_path: str,
        new_path: str,
        link_count: int,
        page_count: int,
        owner: str | None = None,
        resolution: str | None = None,
    ) -> WikiRenameApproval | None:
        """Atomically supersede a pending approval with its same-request successor.

        ``None`` means the old approval lost the pending-state compare-and-swap.
        Any insert conflict rolls back the supersede and leaves the old approval
        pending.
        """

        _required_text("old_approval_id", old_approval_id)
        _required_text("approval_id", approval_id)
        if old_approval_id == approval_id:
            raise ValueError("approval_id must differ from old_approval_id")
        _required_text("request_key", request_key)
        _required_text("request_fingerprint", request_fingerprint)
        _nonnegative("generation", generation)
        _optional_text("base_head", base_head)
        _required_text("old_path", old_path)
        _required_text("new_path", new_path)
        _nonnegative("link_count", link_count)
        _nonnegative("page_count", page_count)
        _optional_text("owner", owner)
        _optional_text("resolution", resolution)
        canonical_plan = _canonical_plan_json(plan_json)

        async with self._lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                old = await self._get(old_approval_id)
                if old is None:
                    await self.conn.rollback()
                    return None
                if old.status is WikiRenameApprovalStatus.APPLYING and old.claim_owner != owner:
                    await self.conn.rollback()
                    return None
                if old.status not in {
                    WikiRenameApprovalStatus.PENDING,
                    WikiRenameApprovalStatus.APPLYING,
                }:
                    await self.conn.rollback()
                    return None
                if request_key != old.request_key:
                    raise ValueError("replacement request_key must match the pending approval")
                if generation <= old.generation:
                    raise ValueError("replacement generation must increase")

                now = datetime.now(UTC).isoformat()
                cursor = await self.conn.execute(
                    """
                    UPDATE wiki_rename_approvals
                    SET status = 'superseded', resolved_at = ?, resolution = ?,
                        claim_owner = NULL, claim_expires_at = NULL
                    WHERE approval_id = ? AND status IN ('pending', 'applying')
                    """,
                    (now, resolution, old_approval_id),
                )
                if cursor.rowcount != 1:
                    await self.conn.rollback()
                    return None

                await self.conn.execute(
                    """
                    INSERT INTO wiki_rename_approvals (
                        approval_id, request_key, request_fingerprint, generation,
                        base_head, plan_json, old_path, new_path, link_count, page_count,
                        status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                    """,
                    (
                        approval_id,
                        request_key,
                        request_fingerprint,
                        generation,
                        base_head,
                        canonical_plan,
                        old_path,
                        new_path,
                        link_count,
                        page_count,
                        now,
                    ),
                )
                await self.conn.execute(
                    """
                    UPDATE wiki_rename_approvals
                    SET replacement_approval_id = ?
                    WHERE approval_id = ? AND status = 'superseded'
                    """,
                    (approval_id, old_approval_id),
                )
                replacement = await self._get(approval_id)
                assert replacement is not None
                await self.conn.commit()
                return replacement
            except sqlite3.IntegrityError as exc:
                raise PendingWikiRenameApprovalConflictError(
                    "replacement approval conflicts with an existing approval"
                ) from exc
            finally:
                if self.conn.in_transaction:
                    await self.conn.rollback()

    async def _get_open_by_request_key(self, request_key: str) -> WikiRenameApproval | None:
        rows = await self.conn.execute_fetchall(
            """
            SELECT * FROM wiki_rename_approvals
            WHERE request_key = ? AND status IN ('pending', 'applying')
            """,
            (request_key,),
        )
        return _row_to_approval(rows[0]) if rows else None

    async def _resolve_pending(
        self,
        approval_id: str,
        *,
        status: WikiRenameApprovalStatus,
        resolution: str | None,
        replacement_approval_id: str | None,
    ) -> bool:
        _required_text("approval_id", approval_id)
        _optional_text("resolution", resolution)
        now = datetime.now(UTC).isoformat()
        cursor = await self.conn.execute(
            """
            UPDATE wiki_rename_approvals
            SET status = ?, resolved_at = ?, resolution = ?, replacement_approval_id = ?
            WHERE approval_id = ? AND status = 'pending'
            """,
            (status, now, resolution, replacement_approval_id, approval_id),
        )
        await self.conn.commit()
        return cursor.rowcount > 0
