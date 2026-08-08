import asyncio
import json
from datetime import UTC, datetime

import aiosqlite


class BackgroundCancellationStore:
    """Durable background-agent cancellation and restart recovery."""

    def __init__(self, conn: aiosqlite.Connection, event_lock: asyncio.Lock):
        self._conn = conn
        self._event_lock = event_lock

    async def request_background_agent_cancel(self, session_id: str, task_id: str) -> bool:
        cancelled = await self.request_background_agent_cancel_cascade(
            session_id=session_id,
            task_id=task_id,
            actor="user",
            cause="user_cancelled",
            idempotency_key=f"user_cancelled:{session_id}:{task_id}",
        )
        return bool(cancelled)

    async def request_background_agent_cancel_cascade(
        self,
        *,
        session_id: str,
        task_id: str,
        actor: str,
        cause: str,
        idempotency_key: str,
    ) -> list[tuple[str, str]]:
        """Persist one cancellation intent across the stored spawn subtree.

        The recursive edge is `parent.child_session_id -> child.session_id`.
        One transaction updates every open descendant and appends an event in
        each affected session, so restart recovery sees the same cause even if
        the in-memory task cancellation is interrupted.
        """
        now = datetime.now(UTC).isoformat()
        async with self._event_lock:
            rows = await self._conn.execute_fetchall(
                """
                WITH RECURSIVE descendants(session_id, task_id, child_session_id) AS (
                    SELECT session_id, task_id, child_session_id
                    FROM background_agent_runs
                    WHERE session_id = ? AND task_id = ?
                    UNION ALL
                    SELECT child.session_id, child.task_id, child.child_session_id
                    FROM background_agent_runs AS child
                    JOIN descendants AS parent ON child.session_id = parent.child_session_id
                )
                SELECT run.session_id, run.task_id, run.child_session_id
                FROM background_agent_runs AS run
                JOIN descendants AS node
                  ON run.session_id = node.session_id AND run.task_id = node.task_id
                WHERE run.status NOT IN ('completed', 'failed', 'cancelled', 'interrupted')
                  AND COALESCE(run.cancel_idempotency_key, '') <> ?
                """,
                (session_id, task_id, idempotency_key),
            )
            cancelled = [(str(row["session_id"]), str(row["task_id"])) for row in rows]
            for row in rows:
                cancelled_session = str(row["session_id"])
                cancelled_task = str(row["task_id"])
                await self._conn.execute(
                    """
                    UPDATE background_agent_runs
                    SET status = 'cancel_requested',
                        cancel_requested_at = COALESCE(cancel_requested_at, ?),
                        cancel_actor = ?, terminal_cause = ?,
                        cancel_generation = cancel_generation + 1,
                        cancel_idempotency_key = ?, detail = ?, updated_at = ?
                    WHERE session_id = ? AND task_id = ?
                    """,
                    (
                        now,
                        actor,
                        cause,
                        idempotency_key,
                        cause,
                        now,
                        cancelled_session,
                        cancelled_task,
                    ),
                )
                if row["child_session_id"]:
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
                        (row["child_session_id"], actor, cause, idempotency_key, now),
                    )
                seq_row = await self._conn.execute_fetchall(
                    """
                    SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq
                    FROM background_agent_events WHERE session_id = ?
                    """,
                    (cancelled_session,),
                )
                await self._conn.execute(
                    """
                    INSERT INTO background_agent_events (
                        session_id, seq, task_id, status, detail, terminal, created_at, event_id
                    ) VALUES (?, ?, ?, 'cancel_requested', ?, 0, ?, ?)
                    """,
                    (
                        cancelled_session,
                        int(seq_row[0]["next_seq"]),
                        cancelled_task,
                        cause,
                        now,
                        f"cancel:{idempotency_key}:{cancelled_session}:{cancelled_task}",
                    ),
                )
            await self._conn.commit()
        return cancelled

    async def mark_interrupted_background_agent_runs(self) -> int:
        now = datetime.now(UTC).isoformat()
        async with self._event_lock:
            rows = await self._conn.execute_fetchall(
                """
                SELECT * FROM background_agent_runs
                WHERE status IN ('running', 'activity', 'cancel_requested')
                """,
            )
            if not rows:
                return 0
            for row in rows:
                was_cancel_requested = row["status"] == "cancel_requested"
                status = "cancelled" if was_cancel_requested else "interrupted"
                if was_cancel_requested:
                    detail = row["terminal_cause"] or "user_cancelled"
                    event_detail = detail
                else:
                    detail = row["detail"] or "server_restart"
                    event_detail = "server_restart"
                completion_id = (
                    row["completion_id"]
                    or f"restart-cancel:{row['session_id']}:{row['task_id']}:{int(row['cancel_generation'])}"
                    if was_cancel_requested
                    else None
                )
                await self._conn.execute(
                    """
                    UPDATE background_agent_runs
                    SET status = ?, detail = ?, updated_at = ?, ended_at = COALESCE(ended_at, ?),
                        completion_id = COALESCE(completion_id, ?)
                    WHERE session_id = ? AND task_id = ?
                    """,
                    (
                        status,
                        detail,
                        now,
                        now,
                        completion_id,
                        row["session_id"],
                        row["task_id"],
                    ),
                )
                seq_rows = await self._conn.execute_fetchall(
                    "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM background_agent_events WHERE session_id = ?",
                    (row["session_id"],),
                )
                await self._conn.execute(
                    """
                    INSERT OR IGNORE INTO background_agent_events (
                        session_id, seq, task_id, status, detail, terminal, created_at, event_id
                    ) VALUES (?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        row["session_id"],
                        int(seq_rows[0]["next_seq"]),
                        row["task_id"],
                        status,
                        event_detail,
                        now,
                        completion_id,
                    ),
                )
                if was_cancel_requested and row["wait"] and row["parent_run_id"] and row["suspension_id"]:
                    resolution = json.dumps(
                        {"status": "cancelled", "result": detail},
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    await self._conn.execute(
                        """
                        UPDATE tool_approvals
                        SET status = 'cancelled', resolved_at = COALESCE(resolved_at, ?),
                            result_feedback = COALESCE(?, result_feedback),
                            resolution_json = COALESCE(?, resolution_json)
                        WHERE run_id = ? AND tool_call_id = ? AND status = 'pending'
                        """,
                        (now, detail, resolution, row["parent_run_id"], row["suspension_id"]),
                    )
            await self._conn.commit()
        return len(rows)

    async def mark_interrupted_agent_sessions(self) -> int:
        """A subagent session left 'running' after a restart can never resume —
        its run died with the process — so the status is a lie that strands the
        UI on a spinner until a manual reload. Flip orphaned agent sessions to
        'interrupted' so a history load resolves them. Mirrors
        mark_interrupted_background_agent_runs for the foreground/session path."""
        rows = await self._conn.execute_fetchall(
            """
            SELECT session_id FROM sessions
            WHERE session_type = 'agent' AND agent_status = 'running'
            """,
        )
        if not rows:
            return 0
        await self._conn.execute(
            """
            UPDATE sessions SET agent_status = 'interrupted'
            WHERE session_type = 'agent' AND agent_status = 'running'
            """,
        )
        await self._conn.commit()
        return len(rows)
