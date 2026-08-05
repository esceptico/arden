from datetime import UTC, datetime, timedelta

import aiosqlite

from arden.context.store_rows import chat_idempotency_payload

CHAT_IDEMPOTENCY_TTL_DAYS = 30
CHAT_IDEMPOTENCY_TERMINAL_STATUSES = ("completed", "cancelled", "error", "failed", "ingested", "interrupted")
CHAT_IDEMPOTENCY_CANCELLED_TOMBSTONE_HASH = "cancelled-before-submit"


class ChatIdempotencyStore:
    """Durable receipts for client-submitted chat messages."""

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

    async def cancel_chat_idempotency_key(
        self,
        *,
        session_id: str,
        client_id: str,
        run_id: str | None = None,
    ) -> str:
        """Make a client id terminal, unless its message was already consumed."""
        receipt = await self.get_chat_idempotency_key(session_id, client_id)
        if receipt is not None and receipt["status"] in {"ingested", "completed", "error", "failed", "interrupted"}:
            return "ingested"

        now_dt = datetime.now(UTC)
        now = now_dt.isoformat()
        expires_at = (now_dt + timedelta(days=CHAT_IDEMPOTENCY_TTL_DAYS)).isoformat()
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
            WHERE chat_idempotency_keys.status NOT IN ('ingested', 'completed', 'error', 'failed', 'interrupted')
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
        await self._conn.commit()
        receipt = await self.get_chat_idempotency_key(session_id, client_id)
        if receipt is None:
            raise RuntimeError("chat idempotency cancellation write failed")
        return "cancelled" if receipt["status"] == "cancelled" else "ingested"

    async def release_interrupted_queued_receipts(self) -> int:
        """Drop receipts for in-memory injections lost during a server restart."""
        cursor = await self._conn.execute(
            """
            DELETE FROM chat_idempotency_keys
            WHERE status = 'queued'
              AND run_id IN (
                  SELECT run_id FROM chat_runs WHERE status = 'interrupted'
              )
            """
        )
        await self._conn.commit()
        return cursor.rowcount
