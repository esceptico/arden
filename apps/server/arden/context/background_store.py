import asyncio
import json
from datetime import UTC, datetime

import aiosqlite

from arden.context.store_rows import background_agent_event_payload, background_agent_payload
from arden.core.public_refs import is_public_ref


class BackgroundAgentStore:
    """Lifecycle events, results, and queries for background agents."""

    def __init__(
        self,
        conn: aiosqlite.Connection,
        read_conn: aiosqlite.Connection,
        event_lock: asyncio.Lock,
    ):
        self._conn = conn
        self._read_conn = read_conn
        self._event_lock = event_lock

    async def record_background_agent_event(
        self,
        *,
        task_id: str,
        session_id: str,
        status: str,
        detail: str | None = None,
        result_ref: str | None = None,
    ) -> int:
        terminal = status in {"completed", "failed", "cancelled", "interrupted"}
        now = datetime.now(UTC).isoformat()
        async with self._event_lock:
            rows = await self._conn.execute_fetchall(
                """
                SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq
                FROM background_agent_events
                WHERE session_id = ?
                """,
                (session_id,),
            )
            seq = int(rows[0]["next_seq"])
            await self._conn.execute(
                """
                INSERT INTO background_agent_events (
                    session_id, seq, task_id, status, detail, result_ref, terminal, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (session_id, seq, task_id, status, detail, result_ref, int(terminal), now),
            )
            await self._conn.execute(
                """
            UPDATE background_agent_runs
            SET detail = COALESCE(?, detail),
                result_ref = COALESCE(?, result_ref),
                updated_at = ?
            WHERE session_id = ? AND task_id = ?
            """,
                (detail, result_ref, now, session_id, task_id),
            )
            await self._conn.commit()
        return seq

    async def record_background_agent_finished(
        self,
        *,
        task_id: str,
        session_id: str,
        status: str,
        result_ref: str | None = None,
        detail: str | None = None,
        result_text: str | None = None,
    ) -> None:
        await self.claim_background_agent_completion(
            task_id=task_id,
            session_id=session_id,
            status=status,
            detail=detail,
            result_ref=result_ref,
            result_text=result_text,
            completion_id=f"bg:{task_id}:{status}",
        )

    async def claim_background_agent_completion(
        self,
        *,
        task_id: str,
        session_id: str,
        status: str,
        completion_id: str,
        result_ref: str | None = None,
        detail: str | None = None,
        result_text: str | None = None,
    ) -> dict:
        now = datetime.now(UTC).isoformat()
        async with self._event_lock:
            rows = await self._conn.execute_fetchall(
                "SELECT * FROM background_agent_runs WHERE session_id = ? AND task_id = ?",
                (session_id, task_id),
            )
            if not rows:
                raise KeyError(f"Unknown background task {session_id}/{task_id}")
            existing = rows[0]
            if existing["completion_id"]:
                return {
                    "claimed": False,
                    "delivered": existing["notified_at"] is not None,
                    "completion_id": existing["completion_id"],
                    "status": existing["status"],
                    "result_ref": existing["result_ref"],
                    "result_text": existing["result_text"],
                }

            seq_rows = await self._conn.execute_fetchall(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM background_agent_events WHERE session_id = ?",
                (session_id,),
            )
            seq = int(seq_rows[0]["next_seq"])
            await self._conn.execute(
                """
                UPDATE background_agent_runs
                SET status = ?, detail = COALESCE(?, detail), result_ref = COALESCE(?, result_ref),
                    result_text = COALESCE(?, result_text), completion_id = ?, updated_at = ?, ended_at = ?
                WHERE session_id = ? AND task_id = ? AND completion_id IS NULL
                """,
                (status, detail, result_ref, result_text, completion_id, now, now, session_id, task_id),
            )
            await self._conn.execute(
                """
                INSERT INTO background_agent_events (
                    session_id, seq, task_id, status, detail, result_ref, terminal, created_at, event_id
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (session_id, seq, task_id, status, detail, result_ref, now, completion_id),
            )
            if existing["wait"] and existing["parent_run_id"] and existing["suspension_id"]:
                resolution = json.dumps(
                    {"status": status, "result": result_text or ""},
                    sort_keys=True,
                    separators=(",", ":"),
                )
                await self._conn.execute(
                    """
                    UPDATE tool_approvals
                    SET status = ?, resolved_at = COALESCE(resolved_at, ?),
                        result_feedback = COALESCE(?, result_feedback),
                        resolution_json = COALESCE(?, resolution_json)
                    WHERE run_id = ? AND tool_call_id = ? AND status = 'pending'
                    """,
                    (
                        status,
                        now,
                        result_text,
                        resolution,
                        existing["parent_run_id"],
                        existing["suspension_id"],
                    ),
                )
            await self._conn.commit()
        return {
            "claimed": True,
            "delivered": False,
            "completion_id": completion_id,
            "status": status,
            "result_ref": result_ref,
            "result_text": result_text,
        }

    async def mark_background_completion_delivered(
        self,
        *,
        session_id: str,
        task_id: str,
        completion_id: str,
    ) -> bool:
        now = datetime.now(UTC).isoformat()
        cursor = await self._conn.execute(
            """
            UPDATE background_agent_runs SET notified_at = COALESCE(notified_at, ?), updated_at = ?
            WHERE session_id = ? AND task_id = ? AND completion_id = ?
            """,
            (now, now, session_id, task_id, completion_id),
        )
        await self._conn.execute(
            "UPDATE background_agent_events SET delivered_at = COALESCE(delivered_at, ?) WHERE event_id = ?",
            (now, completion_id),
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def list_undelivered_background_completions(self) -> list[dict]:
        rows = await self._read_conn.execute_fetchall(
            """
            SELECT * FROM background_agent_runs
            WHERE completion_id IS NOT NULL AND notified_at IS NULL
            ORDER BY ended_at ASC
            """
        )
        return [
            {
                **background_agent_payload(row),
                "result_text": row["result_text"],
            }
            for row in rows
        ]

    async def get_background_agent_result(self, session_id: str, task_id: str) -> str | None:
        rows = await self._read_conn.execute_fetchall(
            """
            SELECT result_text FROM background_agent_runs
            WHERE session_id = ? AND task_id = ?
            """,
            (session_id, task_id),
        )
        if not rows:
            return None
        value = rows[0]["result_text"]
        return value if isinstance(value, str) else None

    async def get_background_agent_result_by_ref(self, session_id: str, agent_ref: str) -> str | None:
        if not is_public_ref(agent_ref):
            return None
        rows = await self._read_conn.execute_fetchall(
            "SELECT result_text FROM background_agent_runs WHERE session_id = ? AND agent_ref = ?",
            (session_id, agent_ref),
        )
        if not rows:
            return None
        value = rows[0]["result_text"]
        return value if isinstance(value, str) else None

    async def get_background_agent_run(self, session_id: str, task_id: str) -> dict | None:
        rows = await self._read_conn.execute_fetchall(
            """
            SELECT * FROM background_agent_runs
            WHERE session_id = ? AND task_id = ?
            """,
            (session_id, task_id),
        )
        if not rows:
            return None
        row = rows[0]
        return {
            **background_agent_payload(row),
            "result_text": row["result_text"],
            "spawn_spec": row["spawn_spec"],
            "spawn_attempts": int(row["spawn_attempts"]),
        }

    async def list_respawnable_background_agent_runs(self, *, max_attempts: int) -> list[dict]:
        """Interrupted detached runs eligible for a boot-time respawn: a stored
        spec to re-dispatch, no completion to redeliver instead, and a spawn
        budget left. Awaited children recover via their parent run's resume."""
        rows = await self._read_conn.execute_fetchall(
            """
            SELECT * FROM background_agent_runs
            WHERE status = 'interrupted'
              AND wait = 0
              AND spawn_spec IS NOT NULL
              AND completion_id IS NULL
              AND spawn_attempts < ?
            ORDER BY updated_at ASC
            """,
            (max_attempts,),
        )
        return [
            {
                **background_agent_payload(row),
                "spawn_spec": row["spawn_spec"],
                "spawn_attempts": int(row["spawn_attempts"]),
            }
            for row in rows
        ]

    async def increment_background_agent_spawn_attempts(self, session_id: str, task_id: str) -> int:
        now = datetime.now(UTC).isoformat()
        await self._conn.execute(
            """
            UPDATE background_agent_runs
            SET spawn_attempts = spawn_attempts + 1, updated_at = ?
            WHERE session_id = ? AND task_id = ?
            """,
            (now, session_id, task_id),
        )
        await self._conn.commit()
        rows = await self._conn.execute_fetchall(
            "SELECT spawn_attempts FROM background_agent_runs WHERE session_id = ? AND task_id = ?",
            (session_id, task_id),
        )
        return int(rows[0]["spawn_attempts"]) if rows else 0

    async def list_background_agent_runs(
        self,
        session_id: str,
        *,
        include_terminal: bool = True,
    ) -> list[dict]:
        if include_terminal:
            rows = await self._read_conn.execute_fetchall(
                """
                SELECT * FROM background_agent_runs
                WHERE session_id = ?
                ORDER BY updated_at DESC
                """,
                (session_id,),
            )
        else:
            rows = await self._read_conn.execute_fetchall(
                """
                SELECT * FROM background_agent_runs
                WHERE session_id = ?
                  AND status NOT IN ('completed', 'failed', 'cancelled', 'interrupted')
                ORDER BY updated_at DESC
                """,
                (session_id,),
            )
        return [background_agent_payload(row) for row in rows]

    async def list_background_agent_events(
        self,
        session_id: str,
        *,
        after_seq: int = 0,
        limit: int = 10000,
    ) -> list[dict]:
        rows = await self._read_conn.execute_fetchall(
            """
            SELECT * FROM background_agent_events
            WHERE session_id = ? AND seq > ?
            ORDER BY seq ASC
            LIMIT ?
            """,
            (session_id, after_seq, limit),
        )
        return [background_agent_event_payload(row) for row in rows]

