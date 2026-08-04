import hashlib
from datetime import UTC, datetime

import aiosqlite

from arden.constants import OFFLOAD_THRESHOLD
from arden.core.raw_tool_results import persist_raw_tool_result
from arden.execution.models import (
    TERMINAL_STATUSES,
    InvocationConflictError,
    InvocationRecord,
    InvocationStatus,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tool_invocations (
    invocation_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    tool_call_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    placement TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    deadline_at TEXT,
    result_payload TEXT,
    result_sha256 TEXT,
    result_blob_ref TEXT,
    error_code TEXT
);

CREATE INDEX IF NOT EXISTS idx_tool_invocations_run
    ON tool_invocations(run_id);
CREATE INDEX IF NOT EXISTS idx_tool_invocations_open
    ON tool_invocations(status, updated_at)
    WHERE status IN ('requested', 'running');

CREATE TABLE IF NOT EXISTS execution_cancellation_scopes (
    session_id TEXT PRIMARY KEY,
    actor TEXT NOT NULL,
    cause TEXT NOT NULL,
    generation INTEGER NOT NULL,
    idempotency_key TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class InvocationStore:
    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn

    async def init_schema(self) -> None:
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()

    async def create(
        self,
        *,
        invocation_id: str,
        run_id: str,
        session_id: str,
        tool_call_id: str,
        tool_name: str,
        placement: str,
        arguments_json: str,
        deadline_at: datetime | None = None,
    ) -> InvocationRecord:
        """Idempotent by invocation_id: a duplicate create returns the existing record."""
        now = _now()
        await self._conn.execute(
            """
            INSERT OR IGNORE INTO tool_invocations (
                invocation_id, run_id, session_id, tool_call_id, tool_name,
                placement, arguments_json, status, created_at, updated_at, deadline_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                invocation_id,
                run_id,
                session_id,
                tool_call_id,
                tool_name,
                placement,
                arguments_json,
                InvocationStatus.REQUESTED,
                now,
                now,
                deadline_at.isoformat() if deadline_at else None,
            ),
        )
        await self._conn.commit()
        record = await self.get(invocation_id)
        assert record is not None
        return record

    async def get(self, invocation_id: str) -> InvocationRecord | None:
        cursor = await self._conn.execute(
            "SELECT * FROM tool_invocations WHERE invocation_id = ?",
            (invocation_id,),
        )
        cursor.row_factory = aiosqlite.Row
        row = await cursor.fetchone()
        return _record_from_row(row) if row else None

    async def mark_running(self, invocation_id: str) -> InvocationRecord:
        """CAS requested -> running; a duplicate mark on a running invocation is a no-op."""
        await self._conn.execute(
            """
            UPDATE tool_invocations
            SET status = ?, started_at = ?, updated_at = ?, attempts = attempts + 1
            WHERE invocation_id = ? AND status = ?
            """,
            (InvocationStatus.RUNNING, _now(), _now(), invocation_id, InvocationStatus.REQUESTED),
        )
        await self._conn.commit()
        record = await self._require(invocation_id)
        if record.status in TERMINAL_STATUSES:
            raise InvocationConflictError(invocation_id, record.status, InvocationStatus.RUNNING)
        return record

    async def request_cancel(self, invocation_id: str) -> InvocationRecord:
        """Record the cancel request; the terminal outcome still comes from the executor."""
        await self._conn.execute(
            """
            UPDATE tool_invocations
            SET cancel_requested = 1, updated_at = ?
            WHERE invocation_id = ? AND status IN (?, ?)
            """,
            (_now(), invocation_id, InvocationStatus.REQUESTED, InvocationStatus.RUNNING),
        )
        await self._conn.commit()
        return await self._require(invocation_id)

    async def complete(
        self,
        invocation_id: str,
        *,
        status: InvocationStatus,
        result_payload: str,
        error_code: str | None = None,
    ) -> InvocationRecord:
        """CAS to a terminal status.

        Duplicate submission of the identical outcome is accepted and returns
        the recorded state; a conflicting outcome raises. Oversized payloads
        spill to the content-addressed blob store and only the reference is
        kept inline.
        """
        if status not in TERMINAL_STATUSES:
            raise ValueError(f"complete() requires a terminal status, got {status}")

        result_sha256 = hashlib.sha256(result_payload.encode("utf-8")).hexdigest()
        inline_payload: str | None = result_payload
        blob_ref: str | None = None
        if len(result_payload.encode("utf-8")) > OFFLOAD_THRESHOLD:
            blob = persist_raw_tool_result(result_payload)
            inline_payload = None
            blob_ref = blob.blob_ref

        now = _now()
        cursor = await self._conn.execute(
            """
            UPDATE tool_invocations
            SET status = ?, result_payload = ?, result_sha256 = ?, result_blob_ref = ?,
                error_code = ?, completed_at = ?, updated_at = ?
            WHERE invocation_id = ? AND status IN (?, ?)
            """,
            (
                status,
                inline_payload,
                result_sha256,
                blob_ref,
                error_code,
                now,
                now,
                invocation_id,
                InvocationStatus.REQUESTED,
                InvocationStatus.RUNNING,
            ),
        )
        await self._conn.commit()
        record = await self._require(invocation_id)
        if cursor.rowcount == 0:
            if record.status == status and record.result_sha256 == result_sha256:
                return record
            raise InvocationConflictError(invocation_id, record.status, status)
        return record

    async def open_invocations(self, *, placement: str | None = None) -> list[InvocationRecord]:
        query = "SELECT * FROM tool_invocations WHERE status IN (?, ?)"
        params: list[object] = [InvocationStatus.REQUESTED, InvocationStatus.RUNNING]
        if placement is not None:
            query += " AND placement = ?"
            params.append(placement)
        cursor = await self._conn.execute(f"{query} ORDER BY created_at", params)
        cursor.row_factory = aiosqlite.Row
        rows = await cursor.fetchall()
        return [_record_from_row(row) for row in rows]

    async def cancellation_cause_for_session(self, session_id: str) -> str | None:
        cursor = await self._conn.execute(
            """
            SELECT cause FROM execution_cancellation_scopes WHERE session_id = ?
            """,
            (session_id,),
        )
        row = await cursor.fetchone()
        return str(row[0]) if row is not None else None

    async def record_session_cancellation(
        self,
        *,
        session_id: str,
        actor: str,
        cause: str,
        idempotency_key: str,
    ) -> None:
        await self._conn.execute(
            """
            INSERT INTO execution_cancellation_scopes (
                session_id, actor, cause, generation, idempotency_key, updated_at
            ) VALUES (?, ?, ?, 1, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                actor = excluded.actor,
                cause = excluded.cause,
                generation = execution_cancellation_scopes.generation + 1,
                idempotency_key = excluded.idempotency_key,
                updated_at = excluded.updated_at
            WHERE execution_cancellation_scopes.idempotency_key <> excluded.idempotency_key
            """,
            (session_id, actor, cause, idempotency_key, _now()),
        )
        await self._conn.commit()

    async def _require(self, invocation_id: str) -> InvocationRecord:
        record = await self.get(invocation_id)
        if record is None:
            raise KeyError(f"unknown invocation: {invocation_id}")
        return record


def _record_from_row(row: aiosqlite.Row) -> InvocationRecord:
    return InvocationRecord(
        invocation_id=row["invocation_id"],
        run_id=row["run_id"],
        session_id=row["session_id"],
        tool_call_id=row["tool_call_id"],
        tool_name=row["tool_name"],
        placement=row["placement"],
        arguments_json=row["arguments_json"],
        status=InvocationStatus(row["status"]),
        attempts=row["attempts"],
        cancel_requested=bool(row["cancel_requested"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
        started_at=_parse(row["started_at"]),
        completed_at=_parse(row["completed_at"]),
        deadline_at=_parse(row["deadline_at"]),
        result_payload=row["result_payload"],
        result_sha256=row["result_sha256"],
        result_blob_ref=row["result_blob_ref"],
        error_code=row["error_code"],
    )
