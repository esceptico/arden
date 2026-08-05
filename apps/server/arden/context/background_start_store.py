import asyncio
import json
from datetime import UTC, datetime

import aiosqlite

from arden.context.models import BackgroundStartDisposition
from arden.core.public_refs import is_public_ref


class BackgroundStartStore:
    """Atomic creation of durable background-agent runs."""

    def __init__(self, conn: aiosqlite.Connection, event_lock: asyncio.Lock):
        self._conn = conn
        self._event_lock = event_lock

    async def record_background_agent_started(
        self,
        *,
        task_id: str,
        agent_ref: str,
        session_id: str,
        parent_run_id: str | None,
        command: str,
        parent_tool_call_id: str | None = None,
        suspension_id: str | None = None,
        child_session_id: str | None = None,
        agent_type: str = "background_research",
        wait: bool = False,
        spawn_spec: str | None = None,
    ) -> BackgroundStartDisposition:
        now = datetime.now(UTC).isoformat()
        if not is_public_ref(agent_ref):
            raise ValueError("background agent_ref is invalid")
        async with self._event_lock:
            existing_rows = await self._conn.execute_fetchall(
                "SELECT agent_ref FROM background_agent_runs WHERE session_id = ? AND task_id = ?",
                (session_id, task_id),
            )
            if existing_rows and existing_rows[0]["agent_ref"] != agent_ref:
                raise ValueError("background agent_ref is immutable")
            if child_session_id is not None:
                child_rows = await self._conn.execute_fetchall(
                    "SELECT public_ref FROM sessions WHERE session_id = ?", (child_session_id,)
                )
                if not child_rows:
                    raise KeyError(f"unknown background child session: {child_session_id}")
                if child_rows[0]["public_ref"] != agent_ref:
                    raise ValueError("background agent_ref must equal its child session public_ref")
            scope_rows = await self._conn.execute_fetchall(
                "SELECT * FROM execution_cancellation_scopes WHERE session_id = ?",
                (session_id,),
            )
            cancellation = scope_rows[0] if scope_rows else None
            cursor = await self._conn.execute(
                """
                INSERT INTO background_agent_runs (
                    task_id, agent_ref, session_id, parent_run_id, parent_tool_call_id, suspension_id, child_session_id,
                    agent_type, wait, status, command, spawn_spec,
                    created_at, started_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, task_id) DO UPDATE SET
                    session_id = excluded.session_id,
                    parent_run_id = excluded.parent_run_id,
                    parent_tool_call_id = excluded.parent_tool_call_id,
                    suspension_id = excluded.suspension_id,
                    child_session_id = excluded.child_session_id,
                    agent_type = excluded.agent_type,
                    wait = excluded.wait,
                    status = 'running',
                    command = excluded.command,
                    spawn_spec = COALESCE(excluded.spawn_spec, spawn_spec),
                    detail = NULL,
                    result_ref = NULL,
                    result_text = NULL,
                    updated_at = excluded.updated_at,
                    ended_at = NULL,
                    cancel_requested_at = NULL,
                    cancel_actor = NULL,
                    terminal_cause = NULL,
                    cancel_idempotency_key = NULL,
                    notified_at = NULL,
                    completion_id = NULL
                WHERE background_agent_runs.completion_id IS NULL
                """,
                (
                    task_id,
                    agent_ref,
                    session_id,
                    parent_run_id,
                    parent_tool_call_id,
                    suspension_id,
                    child_session_id,
                    agent_type,
                    int(wait),
                    command,
                    spawn_spec,
                    now,
                    now,
                    now,
                ),
            )
            if cursor.rowcount <= 0:
                await self._conn.commit()
                return BackgroundStartDisposition.CANCELLED

            disposition = BackgroundStartDisposition.CANCELLED if cancellation else BackgroundStartDisposition.STARTED
            event_id = None
            detail = None
            terminal = 0
            if cancellation is not None:
                actor = str(cancellation["actor"])
                cause = str(cancellation["cause"])
                generation = int(cancellation["generation"])
                idempotency_key = str(cancellation["idempotency_key"])
                completion_id = f"cancel-before-start:{idempotency_key}:{session_id}:{task_id}"
                await self._conn.execute(
                    """
                    UPDATE background_agent_runs
                    SET status = 'cancelled', detail = ?, ended_at = ?, updated_at = ?,
                        cancel_requested_at = ?, cancel_actor = ?, terminal_cause = ?,
                        cancel_generation = MAX(cancel_generation, ?), cancel_idempotency_key = ?,
                        completion_id = ?
                    WHERE session_id = ? AND task_id = ?
                    """,
                    (
                        cause,
                        now,
                        now,
                        now,
                        actor,
                        cause,
                        generation,
                        idempotency_key,
                        completion_id,
                        session_id,
                        task_id,
                    ),
                )
                if child_session_id:
                    await self._conn.execute(
                        """
                        INSERT INTO execution_cancellation_scopes (
                            session_id, actor, cause, generation, idempotency_key, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        ON CONFLICT(session_id) DO UPDATE SET
                            actor = excluded.actor,
                            cause = excluded.cause,
                            generation = MAX(execution_cancellation_scopes.generation, excluded.generation),
                            idempotency_key = excluded.idempotency_key,
                            updated_at = excluded.updated_at
                        WHERE execution_cancellation_scopes.idempotency_key <> excluded.idempotency_key
                        """,
                        (child_session_id, actor, cause, generation, idempotency_key, now),
                    )
                if wait and parent_run_id and suspension_id:
                    resolution = json.dumps(
                        {"status": "cancelled", "result": cause},
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
                        (now, cause, resolution, parent_run_id, suspension_id),
                    )
                event_id = completion_id
                detail = cause
                terminal = 1
            elif child_session_id:
                await self._conn.execute(
                    "DELETE FROM execution_cancellation_scopes WHERE session_id = ?",
                    (child_session_id,),
                )

            seq_rows = await self._conn.execute_fetchall(
                "SELECT COALESCE(MAX(seq), 0) + 1 AS next_seq FROM background_agent_events WHERE session_id = ?",
                (session_id,),
            )
            await self._conn.execute(
                """
                INSERT OR IGNORE INTO background_agent_events (
                    session_id, seq, task_id, status, detail, terminal, created_at, event_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    int(seq_rows[0]["next_seq"]),
                    task_id,
                    disposition.value,
                    detail,
                    terminal,
                    now,
                    event_id,
                ),
            )
            await self._conn.commit()
        return disposition

