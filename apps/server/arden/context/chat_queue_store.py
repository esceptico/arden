import asyncio
import json
from datetime import UTC, datetime, timedelta

import aiosqlite

from arden.context.store_rows import chat_idempotency_payload, chat_queued_message_payload

CHAT_IDEMPOTENCY_TTL_DAYS = 30
CHAT_IDEMPOTENCY_TERMINAL_STATUSES = ("completed", "cancelled", "error", "failed", "ingested", "interrupted")
CHAT_IDEMPOTENCY_CANCELLED_TOMBSTONE_HASH = "cancelled-before-submit"


class ChatQueueStore:
    """Idempotency receipts and durable queued chat messages."""

    def __init__(self, conn: aiosqlite.Connection, read_conn: aiosqlite.Connection):
        self._conn = conn
        self._read_conn = read_conn

    async def prune_expired_chat_idempotency_keys(self, now: datetime | None = None) -> int:
        now_iso = (now or datetime.now(UTC)).isoformat()
        cursor = await self._conn.execute(
            f"""
            DELETE FROM chat_idempotency_keys
            WHERE expires_at IS NOT NULL
              AND expires_at <= ?
              AND status IN ({", ".join("?" for _ in CHAT_IDEMPOTENCY_TERMINAL_STATUSES)})
            """,
            (now_iso, *CHAT_IDEMPOTENCY_TERMINAL_STATUSES),
        )
        await self._conn.commit()
        return cursor.rowcount

    async def claim_chat_idempotency_key(
        self,
        *,
        session_id: str,
        client_id: str,
        request_hash: str,
        status: str = "accepted",
        expires_at: str | None = None,
    ) -> tuple[bool, dict]:
        now_dt = datetime.now(UTC)
        now = now_dt.isoformat()
        expires_at = expires_at or (now_dt + timedelta(days=CHAT_IDEMPOTENCY_TTL_DAYS)).isoformat()
        await self._conn.execute(
            """
            INSERT OR IGNORE INTO chat_idempotency_keys (
                session_id, client_id, request_hash, status, created_at, updated_at, expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (session_id, client_id, request_hash, status, now, now, expires_at),
        )
        await self._conn.commit()
        row = await self.get_chat_idempotency_key(session_id, client_id)
        if row is None:
            raise RuntimeError("chat idempotency claim insert failed")
        return row["request_hash"] == request_hash and row["created_at"] == now, row

    async def get_chat_idempotency_key(self, session_id: str, client_id: str) -> dict | None:
        rows = await self._read_conn.execute_fetchall(
            """
            SELECT * FROM chat_idempotency_keys
            WHERE session_id = ? AND client_id = ?
            """,
            (session_id, client_id),
        )
        if not rows:
            return None
        return chat_idempotency_payload(rows[0])

    async def update_chat_idempotency_key(
        self,
        *,
        session_id: str,
        client_id: str,
        status: str,
        run_id: str | None = None,
        message_id: str | None = None,
    ) -> dict | None:
        now_dt = datetime.now(UTC)
        now = now_dt.isoformat()
        expires_at = (
            (now_dt + timedelta(days=CHAT_IDEMPOTENCY_TTL_DAYS)).isoformat()
            if status in CHAT_IDEMPOTENCY_TERMINAL_STATUSES
            else None
        )
        await self._conn.execute(
            """
            UPDATE chat_idempotency_keys
            SET status = ?,
                run_id = COALESCE(?, run_id),
                message_id = COALESCE(?, message_id),
                updated_at = ?,
                expires_at = COALESCE(?, expires_at)
            WHERE session_id = ? AND client_id = ?
            """,
            (status, run_id, message_id, now, expires_at, session_id, client_id),
        )
        await self._conn.commit()
        return await self.get_chat_idempotency_key(session_id, client_id)

    async def _upsert_cancelled_chat_idempotency(
        self,
        *,
        session_id: str,
        client_id: str,
        run_id: str | None,
        now: str,
        expires_at: str,
    ) -> None:
        """Durably make a client id terminal without replacing its request hash."""
        await self._conn.execute(
            """
            INSERT INTO chat_idempotency_keys (
                session_id, client_id, request_hash, run_id, status, created_at, updated_at, expires_at
            )
            VALUES (?, ?, ?, ?, 'cancelled', ?, ?, ?)
            ON CONFLICT(session_id, client_id) DO UPDATE SET
                status = 'cancelled',
                run_id = COALESCE(chat_idempotency_keys.run_id, excluded.run_id),
                updated_at = excluded.updated_at,
                expires_at = excluded.expires_at
            """,
            (
                session_id,
                client_id,
                CHAT_IDEMPOTENCY_CANCELLED_TOMBSTONE_HASH,
                run_id,
                now,
                now,
                expires_at,
            ),
        )

    async def cancel_chat_queued_message(
        self,
        *,
        session_id: str,
        client_id: str,
        run_id: str | None = None,
    ) -> str:
        """Cancel a queued message, or tombstone a client id before it queues.

        Returns ``cancelled`` when the client may discard its optimistic queue
        item and ``ingested`` once the agent has already consumed it.
        """
        now_dt = datetime.now(UTC)
        now = now_dt.isoformat()
        expires_at = (now_dt + timedelta(days=CHAT_IDEMPOTENCY_TTL_DAYS)).isoformat()
        rows = await self._conn.execute_fetchall(
            """
            SELECT status, run_id FROM chat_queued_messages
            WHERE session_id = ? AND client_id = ?
            """,
            (session_id, client_id),
        )
        if rows:
            row = rows[0]
            if row["status"] == "ingested":
                return "ingested"
            if row["status"] in {"queued", "failed_retryable", "cancelled"}:
                if row["status"] != "cancelled":
                    await self._conn.execute(
                        """
                        UPDATE chat_queued_messages
                        SET status = 'cancelled', updated_at = ?
                        WHERE session_id = ? AND client_id = ?
                          AND status IN ('queued', 'failed_retryable')
                        """,
                        (now, session_id, client_id),
                    )
                await self._upsert_cancelled_chat_idempotency(
                    session_id=session_id,
                    client_id=client_id,
                    run_id=row["run_id"],
                    now=now,
                    expires_at=expires_at,
                )
                await self._conn.commit()
                return "cancelled"
            return "ingested"

        idempotency = await self.get_chat_idempotency_key(session_id, client_id)
        if idempotency is not None:
            return "cancelled" if idempotency["status"] == "cancelled" else "ingested"

        await self._upsert_cancelled_chat_idempotency(
            session_id=session_id,
            client_id=client_id,
            run_id=run_id,
            now=now,
            expires_at=expires_at,
        )
        await self._conn.commit()
        return "cancelled"

    async def mark_interrupted_chat_queued_messages_retryable(self) -> int:
        now = datetime.now(UTC).isoformat()
        cursor = await self._conn.execute(
            """
            UPDATE chat_queued_messages
            SET status = 'failed_retryable',
                updated_at = ?
            WHERE status = 'queued'
              AND run_id IN (
                  SELECT run_id FROM chat_runs WHERE status = 'interrupted'
              )
            """,
            (now,),
        )
        await self._conn.commit()
        return cursor.rowcount

    async def record_chat_queued_message(
        self,
        *,
        client_id: str,
        session_id: str,
        run_id: str,
        message: dict,
        enqueued_seq: int | None = None,
    ) -> str:
        """Record a queue item without reopening a terminal client id.

        A cancellation can arrive while an earlier enqueue request is waiting
        on storage. Its tombstone must win; otherwise the late write recreates
        an invisible, never-drained queue row.
        """
        terminal_receipt = await self._conn.execute_fetchall(
            """
            SELECT status FROM chat_idempotency_keys
            WHERE session_id = ? AND client_id = ?
              AND status IN ('cancelled', 'ingested')
            """,
            (session_id, client_id),
        )
        if terminal_receipt:
            return str(terminal_receipt[0]["status"])

        now = datetime.now(UTC).isoformat()
        message_json = await asyncio.to_thread(lambda: json.dumps(message, default=str))
        cursor = await self._conn.execute(
            """
            INSERT INTO chat_queued_messages (
                client_id, session_id, run_id, status, message_json, enqueued_at, updated_at, enqueued_seq
            )
            VALUES (?, ?, ?, 'queued', ?, ?, ?, ?)
            ON CONFLICT(client_id) DO UPDATE SET
                session_id = excluded.session_id,
                run_id = excluded.run_id,
                status = excluded.status,
                message_json = excluded.message_json,
                updated_at = excluded.updated_at,
                enqueued_seq = excluded.enqueued_seq,
                ingested_at = NULL,
                ingested_seq = NULL
            WHERE chat_queued_messages.status NOT IN ('cancelled', 'ingested')
            """,
            (client_id, session_id, run_id, message_json, now, now, enqueued_seq),
        )
        await self._conn.commit()
        if cursor.rowcount > 0:
            return "queued"
        rows = await self._conn.execute_fetchall(
            "SELECT status FROM chat_queued_messages WHERE client_id = ?",
            (client_id,),
        )
        return str(rows[0]["status"]) if rows else "cancelled"

    async def mark_chat_queued_message_ingested(self, client_id: str, *, ingested_seq: int | None = None) -> None:
        now_dt = datetime.now(UTC)
        now = now_dt.isoformat()
        expires_at = (now_dt + timedelta(days=CHAT_IDEMPOTENCY_TTL_DAYS)).isoformat()
        await self._conn.execute(
            """
            UPDATE chat_queued_messages
            SET status = 'ingested', updated_at = ?, ingested_at = ?, ingested_seq = COALESCE(?, ingested_seq)
            WHERE client_id = ?
            """,
            (now, now, ingested_seq, client_id),
        )
        # The client id is a durable request receipt too. Leaving it at
        # ``queued`` makes a retry render a ghost queue item after the agent
        # has already consumed it.
        await self._conn.execute(
            """
            UPDATE chat_idempotency_keys
            SET status = 'ingested', updated_at = ?, expires_at = ?
            WHERE client_id = ?
              AND session_id = (
                  SELECT session_id FROM chat_queued_messages WHERE client_id = ?
              )
              AND status != 'cancelled'
            """,
            (now, expires_at, client_id, client_id),
        )
        await self._conn.commit()

    async def mark_chat_queued_message_cancelled(self, client_id: str) -> None:
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            """
            UPDATE chat_queued_messages
            SET status = 'cancelled', updated_at = ?
            WHERE client_id = ? AND status = 'queued'
            """,
            (now, client_id),
        )
        await self._conn.commit()

    async def list_chat_queued_messages(self, session_id: str, *, status: str | None = None) -> list[dict]:
        if status:
            rows = await self._read_conn.execute_fetchall(
                """
                SELECT * FROM chat_queued_messages
                WHERE session_id = ? AND status = ?
                ORDER BY enqueued_at ASC
                """,
                (session_id, status),
            )
        else:
            rows = await self._read_conn.execute_fetchall(
                """
                SELECT * FROM chat_queued_messages
                WHERE session_id = ?
                ORDER BY enqueued_at ASC
                """,
                (session_id,),
            )
        return [chat_queued_message_payload(row) for row in rows]

