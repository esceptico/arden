import asyncio

import pytest

from arden.events.sse import (
    ApprovalNeededEvent,
    ConnectionNeededEvent,
    ReasoningMessageContentEvent,
    ReasoningMessageStartEvent,
)
from arden.server.bus import ReplaySubscription, StreamRecord
from arden.server.chat_replay import ChatReplayPlanner, ChatReplayUnavailableError
from arden.server.state import RunRegistry


class _IncompleteReplayStore:
    async def list_session_events(
        self,
        session_id: str,
        *,
        after_seq: int,
        limit: int,
    ) -> list[StreamRecord]:
        return []


class _EventReplayStore:
    def __init__(self, record: StreamRecord):
        self.record = record

    async def list_session_events(
        self,
        session_id: str,
        *,
        after_seq: int,
        limit: int,
    ) -> list[StreamRecord]:
        return [self.record]


def _subscription(record: StreamRecord) -> ReplaySubscription:
    return ReplaySubscription(snapshot=[record], queue=asyncio.Queue())


@pytest.mark.asyncio
async def test_replay_rejects_an_incomplete_durable_tail():
    planner = ChatReplayPlanner(
        session_id="session-1",
        run_registry=RunRegistry(),
        event_store=_IncompleteReplayStore(),
    )

    with pytest.raises(
        ChatReplayUnavailableError,
        match="chat replay for session 'session-1' is incomplete at sequence 2",
    ):
        await planner.build(
            ReplaySubscription(snapshot=[], queue=asyncio.Queue()),
            after_seq=0,
            replay_upper_seq=2,
            checkpoint_seq=0,
        )


@pytest.mark.asyncio
async def test_replay_accepts_ephemeral_buffered_tail_after_durable_event():
    durable = StreamRecord(
        seq=1,
        session_id="session-1",
        event=ReasoningMessageStartEvent(message_id="reasoning-1"),
    )
    ephemeral = StreamRecord(
        seq=2,
        session_id="session-1",
        event=ReasoningMessageContentEvent(message_id="reasoning-1", delta="working"),
    )
    planner = ChatReplayPlanner(
        session_id="session-1",
        run_registry=RunRegistry(),
        event_store=_EventReplayStore(durable),
    )

    plan = await planner.build(
        ReplaySubscription(snapshot=[durable, ephemeral], queue=asyncio.Queue()),
        after_seq=0,
        replay_upper_seq=2,
        checkpoint_seq=0,
    )

    assert plan.records == (durable, ephemeral)


@pytest.mark.asyncio
async def test_replay_at_captured_cursor_is_empty():
    planner = ChatReplayPlanner(
        session_id="session-1",
        run_registry=RunRegistry(),
        event_store=_IncompleteReplayStore(),
    )

    plan = await planner.build(
        ReplaySubscription(snapshot=[], queue=asyncio.Queue()),
        after_seq=2,
        replay_upper_seq=2,
        checkpoint_seq=0,
    )

    assert plan.records == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event, message",
    [
        (
            ApprovalNeededEvent(tool_id="tool-1", name="file_edit"),
            "cannot verify pending approvals",
        ),
        (
            ConnectionNeededEvent(
                tool_id="tool-1",
                integration_id="gmail",
                connection_id="google",
                label="Gmail",
                capability="Read email",
            ),
            "cannot verify pending connections",
        ),
    ],
)
async def test_replay_requires_durable_pending_state(event, message):
    record = StreamRecord(seq=1, session_id="session-1", event=event)
    planner = ChatReplayPlanner(
        session_id="session-1",
        run_registry=RunRegistry(),
        event_store=_EventReplayStore(record),
    )

    with pytest.raises(ChatReplayUnavailableError, match=message):
        await planner.build(
            _subscription(record),
            after_seq=0,
            replay_upper_seq=1,
            checkpoint_seq=0,
        )
