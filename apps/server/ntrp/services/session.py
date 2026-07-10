import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Literal

from ntrp.context.models import SessionData, SessionState
from ntrp.context.store import AREA_FILTER_UNSET, SessionStore
from ntrp.core.compactor import compact_messages, compactable_range
from ntrp.core.tool_result_files import purge_session_results
from ntrp.events.sse import SessionActivityEvent, SessionCreatedEvent, SSEEvent
from ntrp.logging import get_logger

_logger = get_logger(__name__)

EventSink = Callable[[SSEEvent], Awaitable[None]]


def session_row(state: SessionState, message_count: int) -> dict:
    """SessionListItem-shaped row the desktop sidebar renders directly —
    mirrors the fields GET /sessions returns, so the client can add or
    patch a row without a refetch."""
    return {
        "session_id": state.session_id,
        "started_at": state.started_at.isoformat(),
        "last_activity": state.last_activity.isoformat(),
        "name": state.name,
        "message_count": message_count,
        "session_type": state.session_type,
        "origin_automation_id": state.origin_automation_id,
        "parent_session_id": state.parent_session_id,
        "parent_tool_call_id": state.parent_tool_call_id,
        "agent_type": state.agent_type,
        "agent_status": state.agent_status,
        "area_id": state.area_id,
        "chat_model": state.chat_model,
    }


class SessionService:
    def __init__(self, store: SessionStore, event_sink: EventSink | None = None):
        self.store = store
        self._locks: dict[str, asyncio.Lock] = {}
        # Optional publisher (wired by the runtime to the global automation
        # bus) so session lifecycle changes reach connected desktops live.
        self._event_sink = event_sink

    def set_event_sink(self, sink: EventSink) -> None:
        self._event_sink = sink

    async def _publish(self, event: SSEEvent) -> None:
        if self._event_sink is None:
            return
        try:
            await self._event_sink(event)
        except Exception:
            _logger.debug("Failed to publish session event", exc_info=True)

    def _lock_for(self, session_id: str) -> asyncio.Lock:
        lock = self._locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[session_id] = lock
        return lock

    def create(
        self,
        name: str | None = None,
        session_type: Literal["chat", "channel", "agent"] = "chat",
        origin_automation_id: str | None = None,
        session_id: str | None = None,
        parent_session_id: str | None = None,
        parent_tool_call_id: str | None = None,
        agent_type: str | None = None,
        agent_status: str | None = None,
        area_id: str | None = None,
        chat_model: str | None = None,
    ) -> SessionState:
        now = datetime.now(UTC)
        return SessionState(
            session_id=session_id or f"{now.strftime('%Y%m%d_%H%M%S')}_{now.microsecond // 1000:03d}",
            started_at=now,
            name=name,
            session_type=session_type,
            origin_automation_id=origin_automation_id,
            parent_session_id=parent_session_id,
            parent_tool_call_id=parent_tool_call_id,
            agent_type=agent_type,
            agent_status=agent_status,
            area_id=area_id,
            chat_model=chat_model,
        )

    async def provision(
        self,
        name: str | None = None,
        session_type: Literal["chat", "channel", "agent"] = "chat",
        origin_automation_id: str | None = None,
        session_id: str | None = None,
        parent_session_id: str | None = None,
        parent_tool_call_id: str | None = None,
        agent_type: str | None = None,
        agent_status: str | None = None,
        area_id: str | None = None,
        chat_model: str | None = None,
    ) -> SessionState:
        """Create + persist a session and announce it (SESSION_CREATED) so
        connected desktops add the sidebar row live. The single creation
        chokepoint for server-spawned sessions (automations, agent spawn
        tool) that the user didn't open themselves."""
        state = self.create(
            name=name,
            session_type=session_type,
            origin_automation_id=origin_automation_id,
            session_id=session_id,
            parent_session_id=parent_session_id,
            parent_tool_call_id=parent_tool_call_id,
            agent_type=agent_type,
            agent_status=agent_status,
            area_id=area_id,
            chat_model=chat_model,
        )
        await self.save(state, [])
        await self._publish(SessionCreatedEvent(session=session_row(state, 0)))
        return state

    async def provision_state(self, state: SessionState, messages: list[dict] | None = None) -> SessionState:
        rows = messages or []
        await self.save(state, rows)
        await self._publish(SessionCreatedEvent(session=session_row(state, len(rows))))
        return state

    async def load(self, session_id: str | None = None) -> SessionData | None:
        try:
            sid = session_id or await self.store.get_latest_id()
            if not sid:
                return None
            return await self.store.load_session(sid)
        except Exception as e:
            _logger.warning("Failed to load session %s: %s", session_id or "latest", e)
            return None

    async def save(
        self,
        session_state: SessionState,
        messages: list[dict],
        metadata: dict | None = None,
    ) -> None:
        try:
            session_state.last_activity = datetime.now(UTC)
            async with self._lock_for(session_state.session_id):
                await self.store.save_session(session_state, messages, metadata=metadata)
        except Exception as e:
            _logger.warning("Failed to save session: %s", e)
            raise
        await self._announce_activity(session_state, messages)

    async def save_progress(self, session_state: SessionState, messages: list[dict]) -> None:
        """Mid-run checkpoint — upserts messages, leaves metadata alone."""
        try:
            session_state.last_activity = datetime.now(UTC)
            async with self._lock_for(session_state.session_id):
                await self.store.update_progress(session_state, messages)
        except Exception as e:
            _logger.warning("Failed to save mid-run progress: %s", e)
            raise
        await self._announce_activity(session_state, messages)

    async def _announce_activity(self, session_state: SessionState, messages: list[dict]) -> None:
        """Push a row delta for passive sessions so the sidebar bumps/re-sorts
        rows the user might not be viewing. Ordinary chat streaming stays off
        this global bus because the user is already watching it over the
        per-session stream."""
        if session_state.session_type not in {"channel", "agent"} or not messages:
            return
        await self._publish(SessionActivityEvent(session=session_row(session_state, len(messages))))

    async def record_chat_run_started(self, run_id: str, session_id: str, metadata: dict | None = None) -> None:
        try:
            await self.store.record_chat_run_started(run_id, session_id, metadata=metadata)
        except Exception as e:
            _logger.warning("Failed to record chat run start: %s", e)
            raise

    async def claim_chat_idempotency_key(
        self,
        *,
        session_id: str,
        client_id: str,
        request_hash: str,
        status: str = "accepted",
    ) -> tuple[bool, dict]:
        return await self.store.claim_chat_idempotency_key(
            session_id=session_id,
            client_id=client_id,
            request_hash=request_hash,
            status=status,
        )

    async def update_chat_idempotency_key(
        self,
        *,
        session_id: str,
        client_id: str,
        status: str,
        run_id: str | None = None,
        message_id: str | None = None,
    ) -> dict | None:
        return await self.store.update_chat_idempotency_key(
            session_id=session_id,
            client_id=client_id,
            status=status,
            run_id=run_id,
            message_id=message_id,
        )

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
        try:
            await self.store.record_chat_run_status(
                run_id,
                status,
                stop_reason=stop_reason,
                last_seq=last_seq,
                error_code=error_code,
                error_message=error_message,
            )
        except Exception as e:
            _logger.warning("Failed to record chat run status: %s", e)
            raise

    async def record_chat_queued_message(
        self,
        *,
        client_id: str,
        session_id: str,
        run_id: str,
        message: dict,
        enqueued_seq: int | None = None,
    ) -> None:
        try:
            await self.store.record_chat_queued_message(
                client_id=client_id,
                session_id=session_id,
                run_id=run_id,
                message=message,
                enqueued_seq=enqueued_seq,
            )
        except Exception as e:
            _logger.warning("Failed to record queued chat message: %s", e)
            raise

    async def mark_chat_queued_message_ingested(self, client_id: str, ingested_seq: int | None = None) -> None:
        try:
            await self.store.mark_chat_queued_message_ingested(client_id, ingested_seq=ingested_seq)
        except Exception as e:
            _logger.warning("Failed to mark queued chat message ingested: %s", e)
            raise

    async def mark_chat_queued_message_cancelled(self, client_id: str) -> None:
        try:
            await self.store.mark_chat_queued_message_cancelled(client_id)
        except Exception as e:
            _logger.warning("Failed to mark queued chat message cancelled: %s", e)
            raise

    async def record_chat_compaction(
        self,
        *,
        compaction_id: str,
        session_id: str,
        boundary_seq: int,
        messages_before: int,
        messages_after: int,
        rehydration_state: dict | None = None,
    ) -> None:
        try:
            await self.store.record_chat_compaction(
                compaction_id=compaction_id,
                session_id=session_id,
                boundary_seq=boundary_seq,
                messages_before=messages_before,
                messages_after=messages_after,
                rehydration_state=rehydration_state,
            )
        except Exception as e:
            _logger.warning("Failed to record chat compaction: %s", e)

    async def set_goal(self, session_id: str, objective: str, token_budget: int | None = None) -> dict | None:
        try:
            return await self.store.set_goal(session_id, objective, token_budget=token_budget)
        except Exception as e:
            _logger.warning("Failed to set goal: %s", e)
            return None

    async def get_goal(self, session_id: str) -> dict | None:
        try:
            return await self.store.get_goal(session_id)
        except Exception as e:
            _logger.warning("Failed to load goal: %s", e)
            return None

    async def update_goal(self, session_id: str, **kwargs) -> dict | None:
        try:
            return await self.store.update_goal(session_id, **kwargs)
        except Exception as e:
            _logger.warning("Failed to update goal: %s", e)
            return None

    async def clear_goal(self, session_id: str) -> bool:
        try:
            return await self.store.clear_goal(session_id)
        except Exception as e:
            _logger.warning("Failed to clear goal: %s", e)
            return False

    async def set_todo_override(
        self, session_id: str, items: list[dict], explanation: str | None = None
    ) -> dict | None:
        try:
            return await self.store.set_todo_override(session_id, items, explanation)
        except Exception as e:
            _logger.warning("Failed to set todo override: %s", e)
            return None

    async def get_todo_override(self, session_id: str) -> dict | None:
        try:
            return await self.store.get_todo_override(session_id)
        except Exception as e:
            _logger.warning("Failed to load todo override: %s", e)
            return None

    async def clear_todo_override(self, session_id: str) -> bool:
        try:
            return await self.store.clear_todo_override(session_id)
        except Exception as e:
            _logger.warning("Failed to clear todo override: %s", e)
            return False

    async def list_sessions(
        self,
        limit: int = 20,
        area_id: str | None | object = AREA_FILTER_UNSET,
        include_agents: bool = True,
        offset: int = 0,
    ) -> list[dict]:
        return await self.store.list_sessions(
            limit=limit,
            area_id=area_id,
            include_agents=include_agents,
            offset=offset,
        )

    async def list_messages(
        self,
        session_id: str,
        limit: int = 100,
        before: str | None = None,
        after: str | None = None,
        around: str | None = None,
        around_seq: int | None = None,
        before_seq: int | None = None,
        after_seq: int | None = None,
        area_id: str | None | object = AREA_FILTER_UNSET,
    ) -> dict:
        return await self.store.list_session_messages(
            session_id,
            limit=limit,
            before=before,
            after=after,
            around=around,
            around_seq=around_seq,
            before_seq=before_seq,
            after_seq=after_seq,
            area_id=area_id,
        )

    async def search_messages(
        self,
        query: str,
        *,
        limit: int = 20,
        offset: int = 0,
        session_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
        area_id: str | None | object = AREA_FILTER_UNSET,
    ) -> dict:
        return await self.store.search_messages(
            query,
            limit=limit,
            offset=offset,
            session_id=session_id,
            since=since,
            until=until,
            area_id=area_id,
        )

    async def messages_since(self, session_id: str, seq: int) -> list[dict]:
        return await self.store.messages_since(session_id, seq)

    async def recent_session_scopes(self, limit: int) -> list[dict]:
        return await self.store.recent_session_scopes(limit)

    async def list_turns(self, session_id: str, limit: int = 100) -> list[dict]:
        return await self.store.list_session_turns(session_id, limit=limit)

    async def rename(self, session_id: str, name: str) -> bool:
        return await self.store.update_session_name(session_id, name)

    async def rename_if_empty(self, session_id: str, name: str) -> bool:
        return await self.store.update_session_name_if_empty(session_id, name)

    async def update_chat_model(self, session_id: str, chat_model: str | None) -> bool:
        if await self.store.load_session(session_id) is None:
            return False
        await self.store.update_session_chat_model(session_id, chat_model)
        return True

    async def create_area(
        self,
        *,
        name: str,
        default_cwd: str | None = None,
        instructions: str | None = None,
        knowledge_scope: str | None = None,
        page_path: str | None = None,
        autonomy: str | None = None,
    ) -> dict:
        return await self.store.create_area(
            name=name,
            default_cwd=default_cwd,
            instructions=instructions,
            knowledge_scope=knowledge_scope,
            page_path=page_path,
            autonomy=autonomy,
        )

    async def get_area(self, area_id: str | None) -> dict | None:
        return await self.store.get_area(area_id)

    async def find_area_by_name(self, name: str) -> dict | None:
        # Case-insensitive: "MATS" must resolve to an existing "mats" area
        # instead of minting a duplicate (suggestion promote, triage create).
        target = name.strip().casefold()
        for area in await self.store.list_areas():
            if area["name"].strip().casefold() == target:
                return area
        return None

    async def list_areas(self) -> list[dict]:
        return await self.store.list_areas()

    async def update_area(self, area_id: str, **kwargs) -> dict | None:
        return await self.store.update_area(area_id, **kwargs)

    async def archive_area(self, area_id: str) -> bool:
        return await self.store.archive_area(area_id)

    async def restore_area(self, area_id: str) -> dict | None:
        return await self.store.restore_area(area_id)

    async def move_session_to_area(self, session_id: str, area_id: str | None) -> bool:
        return await self.store.update_session_area(session_id, area_id)

    async def archive(self, session_id: str) -> bool:
        return await self.store.archive_session(session_id)

    async def restore(self, session_id: str) -> bool:
        return await self.store.restore_session(session_id)

    async def list_archived(self, limit: int = 20) -> list[dict]:
        return await self.store.list_archived_sessions(limit=limit)

    async def revert(
        self,
        session_id: str | None = None,
        turns: int = 1,
        message_id: str | None = None,
    ) -> dict | None:
        if not (data := await self.load(session_id)) or not data.messages:
            return None

        target_idx: int | None = None
        if message_id is not None:
            for i, msg in enumerate(data.messages):
                if msg.get("client_id") == message_id or msg.get("message_id") == message_id:
                    target_idx = i
                    break
        else:
            seen = 0
            for i in range(len(data.messages) - 1, -1, -1):
                if data.messages[i].get("role") == "user":
                    seen += 1
                    if seen >= turns:
                        target_idx = i
                        break

        if target_idx is None:
            return None

        target_message = data.messages[target_idx]
        target_ref = target_message.get("message_id") or target_message.get("client_id")
        raw = target_message["content"]
        user_message = (
            raw
            if isinstance(raw, str)
            else "\n\n".join(
                b["text"] for b in raw if isinstance(b, dict) and b.get("type") == "text" and b.get("text")
            )
            if isinstance(raw, list)
            else ""
        )
        reverted_count = len(data.messages) - target_idx
        data.messages = data.messages[:target_idx]
        metadata = {"last_input_tokens": data.last_input_tokens} if data.last_input_tokens else None
        await self.save(data.state, data.messages, metadata=metadata)
        await self.store.delete_session_messages_from(
            data.state.session_id,
            message_id=target_ref if isinstance(target_ref, str) else None,
            seq=target_idx if not isinstance(target_ref, str) else None,
        )
        return {"user_message": user_message, "reverted_count": reverted_count}

    async def permanently_delete(self, session_id: str) -> bool:
        deleted = await self.store.permanently_delete_session(session_id)
        if deleted:
            # Drop the session's offloaded tool-result files too, so the store
            # doesn't keep dead sessions' data around.
            purge_session_results(session_id)
        return deleted

    async def branch(
        self,
        session_id: str,
        name: str | None = None,
        up_to_message_id: str | None = None,
        from_end_index: int | None = None,
    ) -> SessionState | None:
        """Clone a session's messages into a new session, preserving context.

        Either pin-point arg picks the truncation point (and a trailing
        tool-message run is kept so the cloned context is valid):

        - `up_to_message_id`: the saved `client_id` of the assistant message
          to include. Preferred — works without positional math.
        - `from_end_index`: legacy positional fallback for sessions whose
          messages were persisted before stable ids were introduced. 0 = the
          last message, 1 = the second-to-last, …
        """
        data = await self.load(session_id)
        if not data:
            return None

        messages = list(data.messages)
        target_idx: int | None = None

        if up_to_message_id is not None:
            for i, msg in enumerate(messages):
                if msg.get("client_id") == up_to_message_id or msg.get("message_id") == up_to_message_id:
                    target_idx = i
                    break
            if target_idx is None:
                return None
        elif from_end_index is not None:
            if from_end_index < 0 or from_end_index >= len(messages):
                return None
            target_idx = len(messages) - 1 - from_end_index

        if target_idx is not None:
            end = target_idx + 1
            while end < len(messages) and messages[end].get("role") == "tool":
                end += 1
            messages = messages[:end]

        new_state = self.create(
            name=name or (f"{data.state.name} (branch)" if data.state.name else None),
            area_id=data.state.area_id,
        )
        new_state.auto_approve = set(data.state.auto_approve)
        metadata = {"last_input_tokens": data.last_input_tokens} if data.last_input_tokens else None
        await self.save(new_state, messages, metadata=metadata)
        return new_state


async def compact_session(
    svc: SessionService,
    model: str,
    session_id: str | None = None,
    keep_ratio: float = 0.2,
    summary_max_tokens: int = 1500,
) -> dict:
    if not (data := await svc.load(session_id)):
        return {"status": "no_session", "message": "No active session to compact"}

    session_state = data.state
    messages = data.messages
    before_count = len(messages)
    before_tokens = data.last_input_tokens

    r = compactable_range(messages, keep_ratio=keep_ratio)
    if r is None:
        return {
            "status": "nothing_to_compact",
            "message": f"Nothing to compact ({before_count} messages)",
            "message_count": before_count,
        }
    msg_count = r[1] - r[0]

    new_messages = await compact_messages(
        messages,
        model,
        keep_ratio=keep_ratio,
        summary_max_tokens=summary_max_tokens,
    )

    if new_messages is not None:
        await svc.save(
            session_state,
            new_messages,
            metadata={"last_input_tokens": None},
        )
        return {
            "status": "compacted",
            "message": f"Compacted {before_count} → {len(new_messages)} messages ({msg_count} summarized)",
            "before_tokens": before_tokens,
            "before_messages": before_count,
            "after_messages": len(new_messages),
            "messages_compressed": msg_count,
        }

    return {
        "status": "already_optimal",
        "message": f"Context already optimal ({before_count} messages)",
        "message_count": before_count,
    }
