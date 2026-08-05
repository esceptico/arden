from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

from arden.events.sse import (
    EPHEMERAL_EVENT_TYPES,
    ApprovalNeededEvent,
    ConnectionNeededEvent,
    InputNeededEvent,
    SSEEvent,
)
from arden.server.bus import ReplaySubscription, StreamRecord
from arden.server.state import RunRegistry


class ChatReplayStore(Protocol):
    async def list_session_events(
        self,
        session_id: str,
        *,
        after_seq: int,
        limit: int,
    ) -> list[StreamRecord]: ...


@runtime_checkable
class PendingApprovalReplayStore(Protocol):
    async def list_pending_tool_approvals(self, session_id: str) -> list[Mapping[str, object]]: ...


@runtime_checkable
class PendingConnectionReplayStore(Protocol):
    async def list_pending_integration_connections(self, session_id: str) -> list[Mapping[str, object]]: ...


def is_replayable_chat_event(event: SSEEvent) -> bool:
    return event.type not in EPHEMERAL_EVENT_TYPES


@dataclass(frozen=True, slots=True)
class ChatReplayReset:
    reason: Literal["future_cursor", "replay_gap"]
    seq: int


@dataclass(frozen=True, slots=True)
class ChatReplayPlan:
    records: tuple[StreamRecord, ...]
    reset: ChatReplayReset | None = None


@dataclass(slots=True)
class ChatReplayPlanner:
    session_id: str
    run_registry: RunRegistry
    event_store: ChatReplayStore | None = None
    _durable_pending_approval_ids: set[str] | None = field(default=None, init=False)
    _durable_pending_connection_ids: set[str] | None = field(default=None, init=False)

    async def build(
        self,
        subscription: ReplaySubscription,
        *,
        after_seq: int | None,
        replay_upper_seq: int,
        checkpoint_seq: int,
    ) -> ChatReplayPlan:
        if after_seq is not None and after_seq > replay_upper_seq:
            return ChatReplayPlan(
                records=(),
                reset=ChatReplayReset(reason="future_cursor", seq=replay_upper_seq),
            )

        if after_seq is not None and after_seq < checkpoint_seq:
            return ChatReplayPlan(
                records=await self._filter_records(subscription.snapshot),
                reset=ChatReplayReset(reason="replay_gap", seq=checkpoint_seq),
            )

        durable_records = await self._durable_records(
            after_seq=after_seq,
            replay_upper_seq=replay_upper_seq,
        )
        records = subscription.snapshot if durable_records is None else durable_records
        reset = None
        if durable_records is None and subscription.replay_gap and after_seq is not None:
            reset = ChatReplayReset(
                reason="replay_gap",
                seq=min(max(after_seq + 1, checkpoint_seq), replay_upper_seq),
            )
        return ChatReplayPlan(
            records=await self._filter_records(records),
            reset=reset,
        )

    async def _durable_records(
        self,
        *,
        after_seq: int | None,
        replay_upper_seq: int,
    ) -> list[StreamRecord] | None:
        if self.event_store is None or after_seq is None:
            return None
        if after_seq >= replay_upper_seq:
            return []

        records = await self.event_store.list_session_events(
            self.session_id,
            after_seq=after_seq,
            limit=10000,
        )
        bounded = [record for record in records if record.seq <= replay_upper_seq]
        # session_events is intentionally sparse because ephemeral deltas are
        # not persisted. Only the durable tail reaching the captured live
        # boundary distinguishes a complete replay from a writer still draining.
        if not bounded or bounded[-1].seq != replay_upper_seq:
            return None
        return bounded

    async def _filter_records(self, records: list[StreamRecord]) -> tuple[StreamRecord, ...]:
        filtered: list[StreamRecord] = []
        for record in records:
            event = record.event
            if isinstance(event, ApprovalNeededEvent) and not await self._is_pending_approval(event.tool_id):
                continue
            if isinstance(event, InputNeededEvent) and not self._is_pending_input(event.tool_id):
                continue
            if isinstance(event, ConnectionNeededEvent) and not await self._is_pending_connection(event.tool_id):
                continue
            filtered.append(record)
        return tuple(filtered)

    async def _is_pending_approval(self, tool_id: str) -> bool:
        active_run = self.run_registry.get_active_run(self.session_id)
        future = active_run.pending_approvals.get(tool_id) if active_run else None
        if future is not None:
            # The live Future wins over a briefly stale durable pending row.
            return not future.done()

        if self._durable_pending_approval_ids is None:
            rows = (
                await self.event_store.list_pending_tool_approvals(self.session_id)
                if isinstance(self.event_store, PendingApprovalReplayStore)
                else []
            )
            self._durable_pending_approval_ids = self._pending_ids(rows)
        return tool_id in self._durable_pending_approval_ids

    async def _is_pending_connection(self, tool_id: str) -> bool:
        active_run = self.run_registry.get_active_run(self.session_id)
        future = active_run.pending_connections.get(tool_id) if active_run else None
        if future is not None:
            return not future.done()

        if self._durable_pending_connection_ids is None:
            rows = (
                await self.event_store.list_pending_integration_connections(self.session_id)
                if isinstance(self.event_store, PendingConnectionReplayStore)
                else []
            )
            self._durable_pending_connection_ids = self._pending_ids(rows)
        return tool_id in self._durable_pending_connection_ids

    @staticmethod
    def _pending_ids(rows: list[Mapping[str, object]]) -> set[str]:
        return {tool_id for row in rows if isinstance(tool_id := row.get("tool_call_id"), str)}

    def _is_pending_input(self, tool_id: str) -> bool:
        active_run = self.run_registry.get_active_run(self.session_id)
        future = active_run.pending_inputs.get(tool_id) if active_run else None
        return future is not None and not future.done()
