import asyncio
from datetime import UTC, datetime

import aiosqlite

from arden.automation import store_queries as queries
from arden.automation import store_records as records
from arden.automation.models import Automation, IdempotencyClaim
from arden.automation.store_schema import initialize as initialize_schema
from arden.core.public_refs import is_public_ref
from arden.outbox.store import OutboxStore


class AutomationStore:
    def __init__(
        self,
        conn: aiosqlite.Connection,
        outbox: OutboxStore | None = None,
        *,
        settlement_conn: aiosqlite.Connection | None = None,
    ):
        self._settlement_conn = conn if settlement_conn is None else settlement_conn
        if outbox is not None and outbox.conn is not self._settlement_conn:
            raise ValueError("automation settlement outbox must use the settlement database connection")
        self.conn = conn
        self.outbox = outbox
        self._settlement_lock = asyncio.Lock()
        self._idempotency_lock = asyncio.Lock()

    async def init_schema(self) -> None:
        await initialize_schema(self.conn)

    async def save(self, automation: Automation) -> None:
        try:
            cursor = await self.conn.execute(queries.SAVE, records.automation_values(automation))
            if cursor.rowcount == 0:
                raise ValueError("automation_ref is immutable")
            await self.conn.commit()
        except BaseException:
            await self.conn.rollback()
            raise

    async def seed_predefined(self, seed: str, automation: Automation) -> bool:
        """Insert one user-owned default once; deletion remains deletion."""

        marker = f"predefined_seed:{seed}"
        async with self._idempotency_lock:
            await self.conn.execute("BEGIN")
            try:
                claimed = await self.conn.execute(
                    "INSERT OR IGNORE INTO automation_meta (key, value) VALUES (?, '1')",
                    (marker,),
                )
                if claimed.rowcount == 0:
                    await self.conn.rollback()
                    return False
                existing = await self.conn.execute_fetchall(queries.GET_BY_ID, (automation.task_id,))
                if not existing:
                    await self.conn.execute(queries.INSERT, records.automation_values(automation))
                await self.conn.commit()
                return True
            except BaseException:
                await self.conn.rollback()
                raise

    async def get(self, task_id: str) -> Automation | None:
        rows = await self.conn.execute_fetchall(queries.GET_BY_ID, (task_id,))
        if not rows:
            return None
        return records.automation(rows[0])

    async def get_by_ref(self, automation_ref: str) -> Automation | None:
        if not is_public_ref(automation_ref):
            return None
        rows = await self.conn.execute_fetchall(queries.GET_BY_REF, (automation_ref,))
        return records.automation(rows[0]) if rows else None

    async def list_all(self) -> list[Automation]:
        rows = await self.conn.execute_fetchall(queries.LIST_ALL)
        return [records.automation(row) for row in rows]

    async def list_due(self, now: datetime) -> list[Automation]:
        rows = await self.conn.execute_fetchall(queries.LIST_DUE, (now.isoformat(),))
        return [records.automation(row) for row in rows]

    async def list_running(self) -> list[Automation]:
        rows = await self.conn.execute_fetchall(queries.LIST_RUNNING)
        return [records.automation(row) for row in rows]

    async def list_event_triggered(self, event_type: str) -> list[Automation]:
        rows = await self.conn.execute_fetchall(queries.LIST_EVENT_TRIGGERED, (event_type,))
        return [records.automation(row) for row in rows]

    async def list_by_trigger_type(self, trigger_type: str) -> list[Automation]:
        rows = await self.conn.execute_fetchall(queries.LIST_BY_TRIGGER_TYPE, (trigger_type,))
        return [records.automation(row) for row in rows]

    async def list_message_triggered(self, source: str, channel_id: str) -> list[Automation]:
        rows = await self.conn.execute_fetchall(queries.LIST_MESSAGE_TRIGGERED, (source, channel_id))
        return [records.automation(row) for row in rows]

    async def list_watched_slack_channels(self) -> list[str]:
        rows = await self.conn.execute_fetchall(queries.LIST_WATCHED_SLACK_CHANNELS)
        return [row["channel_id"] for row in rows if row["channel_id"] is not None]

    async def try_mark_running(self, task_id: str, now: datetime) -> bool:
        cursor = await self.conn.execute(queries.TRY_MARK_RUNNING, (now.isoformat(), task_id))
        await self.conn.commit()
        return cursor.rowcount > 0

    async def clear_running(self, task_id: str) -> None:
        await self.conn.execute(queries.CLEAR_RUNNING, (task_id,))
        await self.conn.commit()

    async def update_last_run(
        self,
        task_id: str,
        last_run: datetime,
        next_run: datetime | None,
        result: str | None = None,
        *,
        preserve_result: bool = False,
    ) -> None:
        """`preserve_result` advances the clock without touching last_result —
        detached (fire-and-accept) runs have no result yet at this point."""
        if preserve_result:
            await self.conn.execute(
                queries.UPDATE_LAST_RUN_KEEP_RESULT,
                (last_run.isoformat(), next_run.isoformat() if next_run else None, task_id),
            )
        else:
            await self.conn.execute(
                queries.UPDATE_LAST_RUN,
                (last_run.isoformat(), next_run.isoformat() if next_run else None, result, task_id),
            )
        await self.conn.commit()

    async def update_last_run_if_next_run(
        self,
        task_id: str,
        last_run: datetime,
        next_run: datetime | None,
        expected_next_run: datetime | None,
        result: str | None = None,
        *,
        preserve_result: bool = False,
    ) -> None:
        """Finish a run without replacing a later external reschedule.

        The scheduler writes ``expected_next_run`` before dispatch.  If a
        debounce changes it while the handler is running, this one statement
        still records the result but leaves the newer due time intact.
        """
        params = (
            last_run.isoformat(),
            expected_next_run.isoformat() if expected_next_run else None,
            next_run.isoformat() if next_run else None,
        )
        if preserve_result:
            await self.conn.execute(
                queries.UPDATE_LAST_RUN_KEEP_RESULT_IF_NEXT_RUN,
                (*params, task_id),
            )
        else:
            await self.conn.execute(
                queries.UPDATE_LAST_RUN_IF_NEXT_RUN,
                (*params, result, task_id),
            )
        await self.conn.commit()

    async def record_run_start(self, task_id: str, started_at: datetime) -> int:
        try:
            cursor = await self.conn.execute(queries.INSERT_RUN, (started_at.isoformat(), task_id))
            if cursor.rowcount == 0:
                raise KeyError(f"Automation {task_id} not found")
            if cursor.lastrowid is None:
                raise RuntimeError("automation run insert returned no row id")
            await self.conn.commit()
            return int(cursor.lastrowid)
        except BaseException:
            await self.conn.rollback()
            raise

    async def defer_run(
        self,
        *,
        task_id: str,
        run_id: int,
        retry_at: datetime | None,
        expected_next_run: datetime | None,
        event_queue_id: int | None,
    ) -> bool:
        """Discard an attempt that never entered its target chat."""

        conn = self._settlement_conn
        async with self._settlement_lock:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                cursor = await conn.execute(queries.DELETE_UNSTARTED_RUN, (run_id, task_id))
                if cursor.rowcount == 0:
                    await conn.rollback()
                    return False
                await conn.execute(
                    queries.DEFER_TASK,
                    (
                        expected_next_run.isoformat() if expected_next_run else None,
                        retry_at.isoformat() if retry_at else None,
                        task_id,
                    ),
                )
                if event_queue_id is not None:
                    await conn.execute(
                        "UPDATE automation_event_queue SET claimed_at = NULL WHERE id = ?",
                        (event_queue_id,),
                    )
                await conn.commit()
                return True
            except BaseException:
                await conn.rollback()
                raise

    async def bind_detached_chat_run(
        self,
        automation_run_id: int,
        task_id: str,
        chat_run_id: str,
        chat_session_id: str,
    ) -> None:
        async with self._settlement_lock:
            cursor = await self._settlement_conn.execute(
                queries.BIND_DETACHED_CHAT_RUN,
                (chat_run_id, chat_session_id, automation_run_id, task_id),
            )
            if cursor.rowcount == 0:
                await self._settlement_conn.rollback()
                raise RuntimeError(f"automation run {automation_run_id} was not running")
            await self._settlement_conn.commit()

    async def bind_interrupted_chat_run(
        self,
        task_id: str,
        chat_run_id: str,
        chat_session_id: str,
    ) -> bool:
        async with self._settlement_lock:
            cursor = await self._settlement_conn.execute(
                queries.BIND_INTERRUPTED_CHAT_RUN,
                (
                    chat_run_id,
                    chat_session_id,
                    task_id,
                    task_id,
                ),
            )
            await self._settlement_conn.commit()
            return cursor.rowcount == 1

    async def refresh_detached_chat_run(
        self,
        task_id: str,
        chat_run_id: str,
        chat_session_id: str,
        started_at: datetime,
    ) -> bool:
        async with self._settlement_lock:
            cursor = await self._settlement_conn.execute(
                queries.REFRESH_DETACHED_CHAT_RUN,
                (
                    started_at.isoformat(),
                    task_id,
                    chat_run_id,
                    chat_session_id,
                ),
            )
            await self._settlement_conn.commit()
            return cursor.rowcount == 1

    async def complete_event_and_bind_detached_chat_run(
        self,
        queue_id: int,
        automation_run_id: int,
        task_id: str,
        chat_run_id: str,
        chat_session_id: str,
    ) -> None:
        conn = self._settlement_conn
        async with self._settlement_lock:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                await conn.execute(queries.COMPLETE_EVENT, (queue_id,))
                cursor = await conn.execute(
                    queries.BIND_DETACHED_CHAT_RUN,
                    (chat_run_id, chat_session_id, automation_run_id, task_id),
                )
                if cursor.rowcount == 0:
                    raise RuntimeError(f"automation run {automation_run_id} was not running")
                await conn.commit()
            except BaseException:
                await conn.rollback()
                raise

    async def get_running_detached_chat_run(self, task_id: str) -> tuple[str, str, datetime] | None:
        rows = await self.conn.execute_fetchall(queries.RUNNING_DETACHED_CHAT_RUN, (task_id,))
        if not rows:
            return None
        row = rows[0]
        return row["chat_run_id"], row["chat_session_id"], datetime.fromisoformat(row["started_at"])

    async def has_unbound_running_run(self, task_id: str) -> bool:
        rows = await self.conn.execute_fetchall(queries.HAS_UNBOUND_RUNNING_RUN, (task_id,))
        return bool(rows)

    async def settle_run(
        self,
        *,
        task_id: str,
        run_id: int | None,
        status: str,
        result: str | None,
        error: str | None,
        ended_at: datetime,
        next_run: datetime | None,
        expected_next_run: datetime | None,
        preserve_external_reschedule: bool,
        preserve_result: bool,
        disable_one_shot: bool,
        event_queue_id: int | None = None,
        event_retry_at: datetime | None = None,
    ) -> bool:
        """Persist one terminal run and all scheduler state as one transaction.

        ``event_retry_at`` applies only to failed queued events. ``None`` then
        means the event is terminal and must be dead-lettered.
        """

        if status not in {"completed", "failed"}:
            raise ValueError(f"unsupported automation run status: {status}")
        if status == "failed" and not error:
            raise ValueError("failed automation settlement requires an error")
        if status == "failed" and next_run is None and event_queue_id is None:
            raise ValueError("failed automation settlement requires a retry time")
        if status == "completed" and event_retry_at is not None:
            raise ValueError("completed queued events cannot have a retry time")
        if event_queue_id is None and event_retry_at is not None:
            raise ValueError("event retry time requires a queued event")

        clipped_result = result if result is None or len(result) <= 4000 else result[:4000] + "…"
        clipped_error = error if error is None or len(error) <= 4000 else error[:4000] + "…"
        conn = self._settlement_conn
        async with self._settlement_lock:
            await conn.execute("BEGIN IMMEDIATE")
            try:
                settled_run_id = run_id
                if settled_run_id is None:
                    rows = await conn.execute_fetchall(queries.LATEST_RUNNING_RUN_ID, (task_id,))
                    if not rows:
                        await conn.rollback()
                        return False
                    settled_run_id = int(rows[0]["id"])
                cursor = await conn.execute(
                    queries.SETTLE_RUN,
                    (
                        ended_at.isoformat(),
                        status,
                        clipped_result,
                        clipped_error,
                        settled_run_id,
                        task_id,
                    ),
                )
                if cursor.rowcount == 0:
                    await conn.rollback()
                    return False
                if status == "completed":
                    await self._record_successful_cadence(
                        conn,
                        task_id,
                        ended_at,
                        next_run,
                        expected_next_run,
                        preserve_external_reschedule,
                        clipped_result,
                        preserve_result,
                    )
                    if disable_one_shot:
                        await conn.execute(queries.SET_ENABLED, (False, task_id))
                elif next_run is not None:
                    await conn.execute(
                        queries.RECORD_RUN_FAILURE,
                        (next_run.isoformat(), clipped_error, task_id),
                    )

                await conn.execute(queries.CLEAR_RUNNING, (task_id,))

                if event_queue_id is not None:
                    if status == "completed":
                        await conn.execute(queries.COMPLETE_EVENT, (event_queue_id,))
                    elif event_retry_at is None:
                        await conn.execute(
                            queries.DEAD_LETTER_EVENT,
                            (ended_at.isoformat(), clipped_error, event_queue_id),
                        )
                        await conn.execute(queries.COMPLETE_EVENT, (event_queue_id,))
                    else:
                        await conn.execute(
                            queries.FAIL_EVENT,
                            (clipped_error, event_retry_at.isoformat(), event_queue_id),
                        )
                if self.outbox is not None:
                    await self.outbox.enqueue_automation_settled_in_transaction(
                        automation_run_id=settled_run_id,
                        task_id=task_id,
                        success=status == "completed",
                    )
                await conn.commit()
                return True
            except BaseException:
                await conn.rollback()
                raise

    async def _record_successful_cadence(
        self,
        conn: aiosqlite.Connection,
        task_id: str,
        ended_at: datetime,
        next_run: datetime | None,
        expected_next_run: datetime | None,
        preserve_external_reschedule: bool,
        result: str | None,
        preserve_result: bool,
    ) -> None:
        if preserve_external_reschedule:
            params = (
                ended_at.isoformat(),
                expected_next_run.isoformat() if expected_next_run else None,
                next_run.isoformat() if next_run else None,
            )
            if preserve_result:
                await conn.execute(queries.UPDATE_LAST_RUN_KEEP_RESULT_IF_NEXT_RUN, (*params, task_id))
            else:
                await conn.execute(queries.UPDATE_LAST_RUN_IF_NEXT_RUN, (*params, result, task_id))
            return
        if preserve_result:
            await conn.execute(
                queries.UPDATE_LAST_RUN_KEEP_RESULT,
                (ended_at.isoformat(), next_run.isoformat() if next_run else None, task_id),
            )
            return
        await conn.execute(
            queries.UPDATE_LAST_RUN,
            (ended_at.isoformat(), next_run.isoformat() if next_run else None, result, task_id),
        )

    async def fail_orphaned_runs(self, ended_at: datetime, error: str) -> int:
        """Fail unbound runs left by the previous process.

        Detached chat runs are completed by their durable run-completed
        outbox event, so they must remain running until that event settles.
        """
        cursor = await self.conn.execute(queries.FAIL_ORPHANED_RUNS, (error, ended_at.isoformat()))
        await self.conn.commit()
        return cursor.rowcount

    async def recent_run_statuses(self, task_ids: list[str], per_task: int = 4) -> dict[str, list[str]]:
        """Newest-first run statuses per task (for the card's sparkline/pip).
        One small indexed query per task — bounded by the automation count."""
        out: dict[str, list[str]] = {}
        for task_id in task_ids:
            cursor = await self.conn.execute(queries.RECENT_STATUSES, (task_id, per_task))
            rows = await cursor.fetchall()
            out[task_id] = [row["status"] for row in rows]
        return out

    async def latest_runs(self) -> dict[str, dict]:
        """Newest run per task in one query — the overview hydration path."""
        cursor = await self.conn.execute(queries.LATEST_RUNS)
        rows = await cursor.fetchall()
        return {row["task_id"]: records.automation_run(row) for row in rows}

    async def latest_completed_runs(self, task_ids: tuple[str, ...]) -> dict[str, datetime]:
        """Latest successfully completed run per requested task."""

        if not task_ids:
            return {}
        placeholders = ", ".join("?" for _ in task_ids)
        rows = await self.conn.execute_fetchall(
            f"""
            SELECT task_id, ended_at
            FROM automation_runs
            WHERE status = 'completed'
              AND ended_at IS NOT NULL
              AND task_id IN ({placeholders})
              AND id IN (
                  SELECT max(id) FROM automation_runs
                  WHERE status = 'completed' AND task_id IN ({placeholders})
                  GROUP BY task_id
              )
            """,
            (*task_ids, *task_ids),
        )
        return {row["task_id"]: records.required_datetime(row["ended_at"]) for row in rows}

    async def list_runs(self, task_id: str, limit: int = 20) -> list[dict]:
        cursor = await self.conn.execute(queries.LIST_RUNS, (task_id, limit))
        rows = await cursor.fetchall()
        return [records.automation_run(row) for row in rows]

    async def list_runs_since(
        self,
        since: datetime,
        *,
        automation_ref: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        if since.tzinfo is None or since.utcoffset() is None:
            raise ValueError("since must be timezone-aware")
        if limit < 1 or offset < 0:
            raise ValueError("limit must be positive and offset must be non-negative")
        where = "started_at >= ?"
        params: list[object] = [since.astimezone(UTC).isoformat()]
        if automation_ref is not None:
            if not is_public_ref(automation_ref):
                raise ValueError("automation_ref must be an exact public reference")
            where += " AND automation_ref = ?"
            params.append(automation_ref)
        params.extend((limit, offset))
        rows = await self.conn.execute_fetchall(
            f"""
            SELECT automation_ref, chat_session_id, started_at, ended_at, status, result, error
            FROM automation_runs
            WHERE {where}
            ORDER BY started_at DESC, id DESC
            LIMIT ? OFFSET ?
            """,
            tuple(params),
        )
        return [records.model_automation_run(row) for row in rows]

    async def delete(self, task_id: str) -> bool:
        async with self._idempotency_lock:
            await self.conn.execute("BEGIN")
            try:
                cursor = await self.conn.execute(queries.DELETE, (task_id,))
                if cursor.rowcount == 0:
                    await self.conn.rollback()
                    return False
                await self.conn.execute(queries.DELETE_GLOBAL_IDEMPOTENCY_CLAIMS_BY_TASK, (task_id,))
                await self.conn.execute(queries.DELETE_DEDUPE_BY_TASK, (task_id,))
                await self.conn.execute(queries.DELETE_QUEUE_BY_TASK, (task_id,))
                await self.conn.execute(queries.DELETE_COUNTS_BY_TASK, (task_id,))
                await self.conn.commit()
                return True
            except BaseException:
                await self.conn.rollback()
                raise

    async def set_enabled(self, task_id: str, enabled: bool) -> None:
        await self.conn.execute(queries.SET_ENABLED, (int(enabled), task_id))
        await self.conn.commit()

    async def disable_and_clear_next_run(self, task_id: str) -> None:
        await self.conn.execute(queries.DISABLE_AND_CLEAR_NEXT_RUN, (task_id,))
        await self.conn.commit()

    async def disable_by_parent(self, parent_id: str) -> int:
        cursor = await self.conn.execute(queries.DISABLE_BY_PARENT, (parent_id,))
        await self.conn.commit()
        return cursor.rowcount

    async def set_auto_approve(self, task_id: str, auto_approve: bool) -> None:
        await self.conn.execute(queries.SET_AUTO_APPROVE, (int(auto_approve), task_id))
        await self.conn.commit()

    async def update_metadata(self, automation: Automation) -> None:
        await self.conn.execute(
            queries.UPDATE_METADATA,
            (
                automation.name,
                automation.description,
                automation.description_source,
                automation.prompt,
                automation.model,
                records.serialize_triggers(automation.triggers),
                int(automation.enabled),
                automation.next_run_at.isoformat() if automation.next_run_at else None,
                int(automation.auto_approve),
                automation.handler,
                automation.cooldown_minutes,
                automation.max_iterations,
                automation.stop_when,
                automation.max_age_days,
                automation.thread_id,
                int(automation.read_history),
                automation.parent_automation_id,
                automation.idempotency_key,
                automation.idempotency_scope,
                automation.tool_scope,
                automation.triggers_source,
                automation.task_id,
            ),
        )
        await self.conn.commit()

    async def list_loops_by_session(self, session_id: str) -> list[Automation]:
        rows = await self.conn.execute_fetchall(queries.LIST_LOOPS_BY_SESSION, (session_id,))
        return [records.automation(row) for row in rows]

    async def list_session_bound_by_session(
        self, session_id: str, *, include_disabled: bool = False
    ) -> list[Automation]:
        """Kind-agnostic: any automation that targets `session_id` via
        thread_id. Used by the scheduler's run-completed fast path to fire
        deferred session-bound work the moment the session goes idle."""
        query = queries.LIST_ALL_SESSION_BOUND_BY_SESSION if include_disabled else queries.LIST_SESSION_BOUND_BY_SESSION
        rows = await self.conn.execute_fetchall(query, (session_id,))
        return [records.automation(row) for row in rows]

    async def list_by_parent(self, parent_automation_id: str) -> list[Automation]:
        rows = await self.conn.execute_fetchall(queries.LIST_BY_PARENT, (parent_automation_id,))
        return [records.automation(row) for row in rows]

    async def increment_iteration(self, task_id: str) -> None:
        await self.conn.execute(queries.INCREMENT_ITERATION, (task_id,))
        await self.conn.commit()

    async def clear_orphaned_running(self) -> int:
        cursor = await self.conn.execute(queries.CLEAR_ORPHANED_RUNNING)
        await self.conn.commit()
        return cursor.rowcount

    async def set_next_run(self, task_id: str, next_run: datetime) -> None:
        await self.conn.execute(queries.SET_NEXT_RUN, (next_run.isoformat(), task_id))
        await self.conn.commit()

    async def set_next_run_if_enabled(self, task_id: str, next_run: datetime) -> bool:
        cursor = await self.conn.execute(queries.SET_NEXT_RUN_IF_ENABLED, (next_run.isoformat(), task_id))
        await self.conn.commit()
        return cursor.rowcount > 0

    async def set_earlier_next_run_if_enabled(self, task_id: str, next_run: datetime) -> bool:
        serialized = next_run.isoformat()
        cursor = await self.conn.execute(
            queries.SET_EARLIER_NEXT_RUN_IF_ENABLED,
            (serialized, task_id, serialized),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def compare_and_set_next_run(
        self,
        task_id: str,
        expected_next_run: datetime | None,
        next_run: datetime | None,
    ) -> bool:
        """Set the due time only when its persisted value is still expected."""
        cursor = await self.conn.execute(
            queries.COMPARE_AND_SET_NEXT_RUN,
            (
                next_run.isoformat() if next_run else None,
                task_id,
                expected_next_run.isoformat() if expected_next_run else None,
            ),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def claim_and_enqueue_event(
        self,
        task_id: str,
        event_key: str,
        context: str,
        created_at: datetime,
    ) -> bool:
        await self.conn.execute("BEGIN")
        try:
            cursor = await self.conn.execute(queries.CLAIM_EVENT, (task_id, event_key, created_at.isoformat()))
            if cursor.rowcount == 0:
                await self.conn.rollback()
                return False
            await self.conn.execute(
                queries.ENQUEUE_EVENT,
                (task_id, event_key, context, created_at.isoformat()),
            )
            await self.conn.commit()
            return True
        except BaseException:
            await self.conn.rollback()
            raise

    async def try_claim_idempotency(
        self,
        *,
        scope: str,
        key: str,
        automation_task_id: str | None = None,
        parent_automation_id: str | None = None,
        parent_fire_at: str | None = None,
        attempt_n: int | None = None,
        claimed_at: datetime | None = None,
    ) -> bool:
        records.validate_idempotency_claim(
            scope=scope,
            key=key,
            parent_automation_id=parent_automation_id,
            parent_fire_at=parent_fire_at,
            attempt_n=attempt_n,
            automation_task_id=automation_task_id,
        )
        claim_id = records.claim_id(
            scope=scope,
            key=key,
            parent_automation_id=parent_automation_id,
            parent_fire_at=parent_fire_at,
            attempt_n=attempt_n,
        )
        cursor = await self.conn.execute(
            queries.TRY_CLAIM_IDEMPOTENCY,
            (
                claim_id,
                scope,
                key,
                parent_automation_id,
                parent_fire_at,
                attempt_n,
                (datetime.now(UTC) if claimed_at is None else claimed_at).isoformat(),
                automation_task_id,
            ),
        )
        await self.conn.commit()
        return cursor.rowcount > 0

    async def save_with_claim(
        self,
        automation: Automation,
        *,
        scope: str,
        key: str,
        parent_automation_id: str | None = None,
        parent_fire_at: str | None = None,
        attempt_n: int | None = None,
        claimed_at: datetime | None = None,
    ) -> bool:
        """Atomically claim idempotency and save the automation row.

        Returns True iff both rows were written. Returns False if the claim
        was already taken (no automation row inserted). Any exception during
        save rolls back the claim so a retry under the same key can succeed.
        """
        records.validate_idempotency_claim(
            scope=scope,
            key=key,
            parent_automation_id=parent_automation_id,
            parent_fire_at=parent_fire_at,
            attempt_n=attempt_n,
            automation_task_id=automation.task_id,
        )
        claim_id = records.claim_id(
            scope=scope,
            key=key,
            parent_automation_id=parent_automation_id,
            parent_fire_at=parent_fire_at,
            attempt_n=attempt_n,
        )
        # aiosqlite serializes statements, not transactions. Keep this paired
        # claim/insert transaction intact when concurrent tool calls share the
        # same store connection.
        async with self._idempotency_lock:
            await self.conn.execute("BEGIN")
            try:
                cursor = await self.conn.execute(
                    queries.TRY_CLAIM_IDEMPOTENCY,
                    (
                        claim_id,
                        scope,
                        key,
                        parent_automation_id,
                        parent_fire_at,
                        attempt_n,
                        (datetime.now(UTC) if claimed_at is None else claimed_at).isoformat(),
                        automation.task_id,
                    ),
                )
                if cursor.rowcount == 0:
                    if scope != "global":
                        await self.conn.rollback()
                        return False
                    reclaimed = await self.conn.execute(
                        queries.RECLAIM_ORPHAN_GLOBAL_IDEMPOTENCY_CLAIM,
                        (claim_id, automation.task_id, automation.task_id),
                    )
                    if reclaimed.rowcount == 0:
                        await self.conn.rollback()
                        return False
                    cursor = await self.conn.execute(
                        queries.TRY_CLAIM_IDEMPOTENCY,
                        (
                            claim_id,
                            scope,
                            key,
                            parent_automation_id,
                            parent_fire_at,
                            attempt_n,
                            (datetime.now(UTC) if claimed_at is None else claimed_at).isoformat(),
                            automation.task_id,
                        ),
                    )
                    if cursor.rowcount == 0:
                        await self.conn.rollback()
                        return False
                await self.conn.execute(queries.INSERT, records.automation_values(automation))
                await self.conn.commit()
                return True
            except BaseException:
                await self.conn.rollback()
                raise

    async def list_claims_for_parent(self, parent_automation_id: str) -> list[IdempotencyClaim]:
        rows = await self.conn.execute_fetchall(queries.LIST_CLAIMS_FOR_PARENT, (parent_automation_id,))
        return [records.idempotency_claim(row) for row in rows]

    async def evict_event_claims_older_than(self, cutoff: datetime) -> None:
        await self.conn.execute(queries.EVICT_EVENT_CLAIMS, (cutoff.isoformat(),))
        await self.conn.commit()

    async def enqueue_event(self, task_id: str, event_key: str, context: str, created_at: datetime) -> None:
        await self.conn.execute(
            queries.ENQUEUE_EVENT,
            (task_id, event_key, context, created_at.isoformat()),
        )
        await self.conn.commit()

    async def list_tasks_with_pending_events(self) -> list[str]:
        rows = await self.conn.execute_fetchall(queries.LIST_TASKS_WITH_PENDING_EVENTS)
        return [row["task_id"] for row in rows]

    async def claim_next_event(self, task_id: str, claimed_at: datetime) -> tuple[int, str, int] | None:
        while True:
            rows = await self.conn.execute_fetchall(
                queries.CLAIM_NEXT_EVENT_CANDIDATE,
                (task_id, claimed_at.isoformat()),
            )
            if not rows:
                return None

            queue_id = int(rows[0]["id"])
            context = rows[0]["context"]
            attempt_count = int(rows[0]["attempt_count"])
            cursor = await self.conn.execute(
                queries.CLAIM_EVENT_QUEUE_ROW,
                (claimed_at.isoformat(), queue_id),
            )
            await self.conn.commit()
            if cursor.rowcount > 0:
                return queue_id, context, attempt_count

    async def get_claimed_event(self, task_id: str) -> tuple[int, int] | None:
        rows = await self.conn.execute_fetchall(queries.GET_CLAIMED_EVENT, (task_id,))
        if not rows:
            return None
        return int(rows[0]["id"]), int(rows[0]["attempt_count"])

    async def fail_event(self, queue_id: int, error: str, next_attempt_at: datetime) -> None:
        await self.conn.execute(
            queries.FAIL_EVENT,
            (error, next_attempt_at.isoformat(), queue_id),
        )
        await self.conn.commit()

    async def release_event_claim(self, queue_id: int) -> None:
        await self.conn.execute(
            "UPDATE automation_event_queue SET claimed_at = NULL WHERE id = ?",
            (queue_id,),
        )
        await self.conn.commit()

    async def dead_letter_event(self, queue_id: int, error: str, failed_at: datetime) -> None:
        await self.conn.execute("BEGIN")
        try:
            await self.conn.execute(
                queries.DEAD_LETTER_EVENT,
                (failed_at.isoformat(), error, queue_id),
            )
            await self.conn.execute(queries.COMPLETE_EVENT, (queue_id,))
            await self.conn.commit()
        except BaseException:
            await self.conn.rollback()
            raise

    async def release_all_claimed_events(self) -> int:
        cursor = await self.conn.execute(queries.RELEASE_ALL_CLAIMED_EVENTS)
        await self.conn.commit()
        return cursor.rowcount

    async def increment_count(self, task_id: str, session_id: str, updated_at: datetime) -> int:
        await self.conn.execute(queries.INCREMENT_COUNT, (task_id, session_id, updated_at.isoformat()))
        rows = await self.conn.execute_fetchall(queries.GET_COUNT, (task_id, session_id))
        await self.conn.commit()
        return int(rows[0]["count"])

    async def clear_count(self, task_id: str, session_id: str) -> None:
        await self.conn.execute(queries.CLEAR_COUNT, (task_id, session_id))
        await self.conn.commit()

    async def get_status(self, now: datetime) -> dict:
        now_iso = now.isoformat()
        task_row = (await self.conn.execute_fetchall(queries.STATUS_TASKS, (now_iso,)))[0]
        queue_row = (await self.conn.execute_fetchall(queries.STATUS_EVENT_QUEUE, (now_iso, now_iso, now_iso)))[0]
        count_row = (await self.conn.execute_fetchall(queries.STATUS_COUNT_STATE))[0]
        dead_letter_row = (await self.conn.execute_fetchall(queries.STATUS_DEAD_LETTER))[0]

        return {
            "observed_at": now_iso,
            "tasks": {
                "total": int(task_row["total"]),
                "enabled": int(task_row["enabled"]),
                "disabled": int(task_row["disabled"]),
                "running": int(task_row["running"]),
                "due": int(task_row["due"]),
                "next_run_at": task_row["next_run_at"],
                "oldest_running_since": task_row["oldest_running_since"],
            },
            "event_queue": {
                "total": int(queue_row["total"]),
                "ready": int(queue_row["ready"]),
                "scheduled": int(queue_row["scheduled"]),
                "claimed": int(queue_row["claimed"]),
                "oldest_pending_created_at": queue_row["oldest_pending_created_at"],
                "next_attempt_at": queue_row["next_attempt_at"],
                "oldest_claimed_at": queue_row["oldest_claimed_at"],
            },
            "count_state": {
                "total": int(count_row["total"]),
                "oldest_updated_at": count_row["oldest_updated_at"],
            },
            "dead_letters": {
                "total": int(dead_letter_row["total"]),
                "oldest_failed_at": dead_letter_row["oldest_failed_at"],
                "newest_failed_at": dead_letter_row["newest_failed_at"],
            },
        }
