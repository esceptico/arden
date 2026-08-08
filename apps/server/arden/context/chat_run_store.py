import asyncio
import json
from datetime import UTC, datetime

import aiosqlite

from arden.context.store_rows import chat_run_payload
from arden.events.internal import RunCompleted, RunFailed
from arden.outbox.store import OutboxStore


class ChatRunStore:
    """Durable chat-run lifecycle and terminal outbox transactions."""

    def __init__(
        self,
        conn: aiosqlite.Connection,
        read_conn: aiosqlite.Connection,
        completion_conn: aiosqlite.Connection | None,
    ):
        self._conn = conn
        self._read_conn = read_conn
        self._completion_conn = completion_conn

    async def record_chat_run_started(
        self,
        run_id: str,
        session_id: str,
        *,
        metadata: dict | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        metadata = dict(metadata or {})
        client_id = metadata.get("client_id") if isinstance(metadata.get("client_id"), str) else None
        metadata_json = await asyncio.to_thread(lambda: json.dumps(metadata))
        try:
            await self._conn.execute(
                """
                INSERT INTO chat_runs (
                    run_id, session_id, status, started_at, updated_at, metadata_json, client_id
                )
                VALUES (?, ?, 'pending', ?, ?, ?, ?)
                """,
                (run_id, session_id, now, now, metadata_json, client_id),
            )
            await self._conn.execute(
                """
                UPDATE chat_runs
                SET status = 'interrupted',
                    stop_reason = 'superseded',
                    error_code = 'run_superseded',
                    error_message = 'Run was superseded by a newer run in the same session.',
                    updated_at = ?,
                    ended_at = ?
                WHERE session_id = ?
                  AND run_id != ?
                  AND status IN ('pending', 'running')
                """,
                (now, now, session_id, run_id),
            )
            await self._conn.commit()
        except BaseException:
            await self._conn.rollback()
            raise

    async def record_chat_run_status(
        self,
        run_id: str,
        status: str,
        *,
        stop_reason: str | None = None,
        last_seq: int | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        ended_at = now if status in {"completed", "cancelled", "error", "failed", "interrupted"} else None
        await self._conn.execute(
            """
            UPDATE chat_runs
            SET status = ?,
                stop_reason = ?,
                updated_at = ?,
                ended_at = ?,
                last_seq = COALESCE(?, last_seq),
                error_code = ?,
                error_message = ?
            WHERE run_id = ?
            """,
            (status, stop_reason, now, ended_at, last_seq, error_code, error_message, run_id),
        )
        await self._conn.commit()

    async def record_chat_run_completed_with_outbox(
        self,
        event: RunCompleted,
        *,
        stop_reason: str | None,
        last_seq: int | None,
    ) -> None:
        if self._completion_conn is None:
            raise RuntimeError("chat completion connection is not configured")
        now = datetime.now(UTC).isoformat()
        conn = self._completion_conn
        await conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = await conn.execute(
                """
                UPDATE chat_runs
                SET status = 'completed',
                    stop_reason = ?,
                    updated_at = ?,
                    ended_at = ?,
                    last_seq = COALESCE(?, last_seq),
                    error_code = NULL,
                    error_message = NULL
                WHERE run_id = ?
                """,
                (stop_reason, now, now, last_seq, event.run_id),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown chat run: {event.run_id}")
            outbox = OutboxStore(conn)
            await outbox.enqueue_run_completed_in_transaction(event)
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise

    async def record_chat_run_failed_with_outbox(
        self,
        event: RunFailed,
        *,
        status: str,
        stop_reason: str,
        last_seq: int | None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        if self._completion_conn is None:
            raise RuntimeError("chat completion connection is not configured")
        now = datetime.now(UTC).isoformat()
        conn = self._completion_conn
        await conn.execute("BEGIN IMMEDIATE")
        try:
            cursor = await conn.execute(
                """
                UPDATE chat_runs
                SET status = ?,
                    stop_reason = ?,
                    updated_at = ?,
                    ended_at = ?,
                    last_seq = COALESCE(?, last_seq),
                    error_code = ?,
                    error_message = ?
                WHERE run_id = ?
                """,
                (
                    status,
                    stop_reason,
                    now,
                    now,
                    last_seq,
                    error_code,
                    error_message,
                    event.run_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(f"unknown chat run: {event.run_id}")
            outbox = OutboxStore(conn)
            await outbox.enqueue_run_failed_in_transaction(event)
            await conn.commit()
        except BaseException:
            await conn.rollback()
            raise

    async def get_chat_run(self, run_id: str) -> dict | None:
        rows = await self._read_conn.execute_fetchall("SELECT * FROM chat_runs WHERE run_id = ?", (run_id,))
        if not rows:
            return None
        return chat_run_payload(rows[0])

    async def get_latest_chat_run_for_session(self, session_id: str) -> dict | None:
        rows = await self._read_conn.execute_fetchall(
            """
            SELECT * FROM chat_runs
            WHERE session_id = ?
            ORDER BY updated_at DESC, started_at DESC
            LIMIT 1
            """,
            (session_id,),
        )
        if not rows:
            return None
        return chat_run_payload(rows[0])

    async def list_interrupted_chat_runs(self) -> list[dict]:
        rows = await self._read_conn.execute_fetchall(
            """
            SELECT * FROM chat_runs
            WHERE status = 'interrupted' AND stop_reason = 'server_restart'
            ORDER BY updated_at ASC
            """
        )
        return [chat_run_payload(row) for row in rows]

    async def mark_interrupted_chat_runs(self) -> int:
        now = datetime.now(UTC).isoformat()
        cursor = await self._conn.execute(
            """
            UPDATE chat_runs
            SET status = 'interrupted',
                stop_reason = 'server_restart',
                error_code = 'run_interrupted',
                error_message = 'Run was interrupted by server restart.',
                updated_at = ?,
                ended_at = ?
            WHERE status IN ('pending', 'running', 'backgrounded')
            """,
            (now, now),
        )
        await self._conn.commit()
        return cursor.rowcount
