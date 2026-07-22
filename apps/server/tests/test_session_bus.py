import asyncio

import pytest

from arden.events.sse import (
    EventType,
    StreamResetEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ThinkingEvent,
    ToolCallArgsEvent,
)
from arden.server import sse_stream
from arden.server.bus import BusRegistry, SessionBus, SessionEventWriter, StreamRecord
from arden.server.sse_stream import live_records


def _seqs(records):
    return [record.seq for record in records]


def _events(records):
    return [record.event for record in records]


@pytest.mark.asyncio
async def test_ephemeral_delta_events_are_not_persisted_durably():
    persisted: list[EventType] = []

    async def collector(records):
        persisted.extend(record.event.type for record in records)

    bus = SessionBus(session_id="sess-1", record_events=collector)
    await bus.emit(TextMessageStartEvent(message_id="m1"))
    await bus.emit(TextMessageContentEvent(message_id="m1", delta="he"))
    await bus.emit(TextMessageContentEvent(message_id="m1", delta="llo"))
    await bus.emit(ToolCallArgsEvent(tool_call_id="t1", delta="{}"))
    await bus.emit(TextMessageEndEvent(message_id="m1", content="hello"))
    await bus.drain_record_tasks()

    # Token deltas are ephemeral — never written to the durable event log.
    assert EventType.TEXT_MESSAGE_CONTENT not in persisted
    assert EventType.TOOL_CALL_ARGS not in persisted
    # Structural + terminal events persist (terminal carries cumulative text).
    assert EventType.TEXT_MESSAGE_START in persisted
    assert EventType.TEXT_MESSAGE_END in persisted
    # ...but the in-memory buffer keeps everything for live / short-gap replay.
    buffered = [record.event.type for record in bus._recent]
    assert EventType.TEXT_MESSAGE_CONTENT in buffered
    assert EventType.TOOL_CALL_ARGS in buffered


@pytest.mark.asyncio
async def test_session_bus_closes_slow_subscriber_without_blocking_fast_one():
    bus = SessionBus(session_id="sess-1", subscriber_queue_size=2)
    slow = bus.subscribe()
    fast = bus.subscribe()

    first = ThinkingEvent(status="one")
    second = ThinkingEvent(status="two")
    third = ThinkingEvent(status="three")

    await bus.emit(first)
    assert fast.get_nowait().event == first

    await bus.emit(second)
    assert fast.get_nowait().event == second

    await bus.emit(third)

    assert slow not in bus._subscribers
    slow_reset = slow.get_nowait()
    assert slow_reset.event.reason == "slow_consumer"
    assert slow.get_nowait() is None
    assert fast.get_nowait().event == third


@pytest.mark.asyncio
async def test_slow_subscriber_reset_uses_checkpoint_cursor():
    bus = SessionBus(session_id="sess-1", subscriber_queue_size=2)
    slow = bus.subscribe()

    await bus.emit(ThinkingEvent(status="persisted"))
    bus.mark_checkpoint()
    await bus.emit(ThinkingEvent(status="tail-one"))
    await bus.emit(ThinkingEvent(status="tail-two"))

    assert slow not in bus._subscribers
    slow_reset = slow.get_nowait()
    assert slow_reset.event.reason == "slow_consumer"
    assert slow_reset.seq == bus.checkpoint_seq
    assert slow.get_nowait() is None


@pytest.mark.asyncio
async def test_full_subscriber_queue_compacts_adjacent_text_deltas_before_closing():
    bus = SessionBus(session_id="sess-1", subscriber_queue_size=3)
    slow = bus.subscribe()

    await bus.emit(TextMessageContentEvent(message_id="m1", delta="a"))
    await bus.emit(TextMessageContentEvent(message_id="m1", delta="b"))
    await bus.emit(TextMessageContentEvent(message_id="m1", delta="c"))
    await bus.emit(TextMessageContentEvent(message_id="m1", delta="d"))

    assert slow in bus._subscribers
    compacted = slow.get_nowait()
    assert compacted.seq == 4
    assert compacted.event.delta == "abcd"
    assert slow.empty()


@pytest.mark.asyncio
async def test_stream_reset_preempts_full_subscriber_queue():
    bus = SessionBus(session_id="sess-1", subscriber_queue_size=2)
    queue = bus.subscribe()

    await bus.emit(ThinkingEvent(status="stale-one"))
    await bus.emit(ThinkingEvent(status="stale-two"))
    await bus.emit(StreamResetEvent(reason="manual_reset"))

    assert queue not in bus._subscribers
    reset = queue.get_nowait()
    assert reset.event.reason == "manual_reset"
    assert reset.seq == 3
    assert queue.get_nowait() is None


@pytest.mark.asyncio
async def test_live_records_batches_backlogged_events_into_one_chunk():
    bus = SessionBus(session_id="sess-1")
    queue = bus.subscribe()
    await bus.emit(ThinkingEvent(status="one"))
    await bus.emit(ThinkingEvent(status="two"))
    await bus.emit(ThinkingEvent(status="three"))

    stream = live_records(
        bus=bus,
        queue=queue,
        session_id="sess-1",
        should_emit=lambda _event: True,
        keepalive_interval=60,
    )
    chunk = await anext(stream)

    assert chunk.count("\n\n") == 3
    assert "one" in chunk
    assert "two" in chunk
    assert "three" in chunk
    await stream.aclose()


@pytest.mark.asyncio
async def test_live_records_respects_batch_byte_limit(monkeypatch):
    monkeypatch.setattr(sse_stream, "LIVE_RECORD_BATCH_MAX_BYTES", 1)
    bus = SessionBus(session_id="sess-1")
    queue = bus.subscribe()
    await bus.emit(ThinkingEvent(status="one"))
    await bus.emit(ThinkingEvent(status="two"))

    stream = sse_stream.live_records(
        bus=bus,
        queue=queue,
        session_id="sess-1",
        should_emit=lambda _event: True,
        keepalive_interval=60,
    )

    assert (await anext(stream)).count("\n\n") == 1
    assert (await anext(stream)).count("\n\n") == 1
    await stream.aclose()


def test_bus_registry_close_all_handles_full_subscriber_queues():
    registry = BusRegistry()
    bus = registry.get_or_create("sess-1")
    queue = bus.subscribe()

    for i in range(bus.subscriber_queue_size):
        queue.put_nowait(ThinkingEvent(status=str(i)))

    registry.close_all_sync()

    assert queue.get_nowait() is None
    assert bus._subscribers == []


@pytest.mark.asyncio
async def test_subscribe_with_replay_returns_buffered_records_to_new_subscribers():
    bus = SessionBus(session_id="sess-1")
    a, b, c = ThinkingEvent(status="a"), ThinkingEvent(status="b"), ThinkingEvent(status="c")
    await bus.emit(a)
    await bus.emit(b)
    await bus.emit(c)

    snapshot, queue = bus.subscribe_with_replay()
    assert _seqs(snapshot) == [1, 2, 3]
    assert _events(snapshot) == [a, b, c]
    # The queue is for events emitted AFTER subscribe, not the buffer.
    assert queue.empty()

    # Live event after subscribe lands on the queue, not the snapshot.
    d = ThinkingEvent(status="d")
    await bus.emit(d)
    record = queue.get_nowait()
    assert record.seq == 4
    assert record.event == d


@pytest.mark.asyncio
async def test_bus_registry_records_emitted_events():
    recorded = []

    async def record_events(records):
        recorded.extend(records)

    registry = BusRegistry(record_events=record_events)
    bus = registry.get_or_create("sess-1")
    await bus.emit(ThinkingEvent(status="saved"))
    await bus.drain_record_tasks()

    assert len(recorded) == 1
    assert recorded[0].seq == 1
    assert recorded[0].session_id == "sess-1"
    assert recorded[0].event.status == "saved"


@pytest.mark.asyncio
async def test_event_persistence_uses_one_bounded_writer_queue():
    release = asyncio.Event()
    recorded = []

    async def record_events(records):
        recorded.extend(records)
        await release.wait()

    bus = SessionBus(session_id="sess-1", record_events=record_events, record_queue_size=2)

    await bus.emit(ThinkingEvent(status="one"))
    await asyncio.sleep(0)
    writer = bus._record_writer
    assert writer is not None
    first_worker = writer._worker

    await bus.emit(ThinkingEvent(status="two"))
    await bus.emit(ThinkingEvent(status="three"))

    assert writer._worker is first_worker
    assert writer.queue_depth == 2

    release.set()
    await bus.drain_record_tasks()

    assert [record.event.status for record in recorded] == ["one", "two", "three"]


@pytest.mark.asyncio
async def test_writer_close_times_out_instead_of_hanging_on_stuck_write():
    # A slow/stuck durable write must never wedge close() — that is what hangs
    # server shutdown ("can't stop the server"). close() abandons the unflushed
    # event and forces the worker down within close_timeout.
    started = asyncio.Event()

    async def stuck_record(records):
        started.set()
        await asyncio.Event().wait()  # never returns

    writer = SessionEventWriter(stuck_record, close_timeout=0.2)
    writer.submit(StreamRecord(seq=1, session_id="s", event=ThinkingEvent(status="x")))
    await asyncio.wait_for(started.wait(), timeout=1)  # worker is now stuck mid-write

    # Must return promptly instead of blocking on the stuck write.
    await asyncio.wait_for(writer.close(), timeout=2)
    assert writer._worker is None


@pytest.mark.asyncio
async def test_overflowing_writer_queue_persists_all_structural_events_in_order():
    # Structural (durable) events must NOT be dropped when the persist queue is
    # full — losing one (e.g. RUN_FINISHED / TOOL_CALL_RESULT) desyncs the client.
    # AND they must persist in strict seq order: the resume path treats a hole in
    # the durable ledger as an omitted ephemeral delta, so a structural event that
    # jumped ahead of an earlier one would make that earlier seq look like a hole
    # and be silently skipped on replay. The writer applies FIFO backpressure.
    release = asyncio.Event()
    recorded = []

    async def record_events(records):
        await release.wait()
        recorded.extend(records)

    bus = SessionBus(session_id="sess-1", record_events=record_events, record_queue_size=2)

    # Emit far more than the queue holds while the writer is blocked, forcing overflow.
    for i in range(8):
        await bus.emit(ThinkingEvent(status=f"e{i}"))

    release.set()
    await bus.drain_record_tasks()

    # All 8 persisted (none dropped), in the exact seq order they were emitted.
    assert [r.event.status for r in recorded] == [f"e{i}" for i in range(8)]
    assert [r.seq for r in recorded] == sorted(r.seq for r in recorded)


@pytest.mark.asyncio
async def test_emit_does_not_wait_for_persistence_before_live_delivery():
    release = asyncio.Event()
    recorded = []

    async def record_events(records):
        await release.wait()
        recorded.extend(records)

    bus = SessionBus(session_id="sess-1", record_events=record_events)
    queue = bus.subscribe()

    emit_task = asyncio.create_task(bus.emit(ThinkingEvent(status="live")))
    try:
        record = await asyncio.wait_for(queue.get(), timeout=0.05)
        assert record.event.status == "live"
        assert recorded == []
        await asyncio.wait_for(emit_task, timeout=1)
    finally:
        release.set()
        await asyncio.wait_for(bus.drain_record_tasks(), timeout=1)

    assert recorded[0].event.status == "live"


@pytest.mark.asyncio
async def test_registry_idle_remove_waits_for_event_persistence():
    release = asyncio.Event()
    recorded = []

    async def record_events(records):
        await release.wait()
        recorded.extend(records)

    registry = BusRegistry(record_events=record_events)
    bus = registry.get_or_create("sess-1")
    emit_task = asyncio.create_task(bus.emit(ThinkingEvent(status="persist-me")))
    await asyncio.sleep(0)

    remove_task = asyncio.create_task(registry.remove_if_idle("sess-1", is_active=lambda: False))
    await asyncio.sleep(0)

    assert registry.get("sess-1") is bus
    assert emit_task.done()
    assert not remove_task.done()

    release.set()
    await asyncio.wait_for(emit_task, timeout=1)
    await asyncio.wait_for(remove_task, timeout=1)

    assert recorded[0].event.status == "persist-me"
    assert registry.get("sess-1") is None


@pytest.mark.asyncio
async def test_bus_registry_preserves_sequence_when_session_bus_is_recreated():
    registry = BusRegistry()
    first_bus = registry.get_or_create("sess-1")
    await first_bus.emit(ThinkingEvent(status="first"))
    assert first_bus._recent[-1].seq == 1

    registry.remove("sess-1")

    second_bus = registry.get_or_create("sess-1")
    queue = second_bus.subscribe()
    second = ThinkingEvent(status="second")
    await second_bus.emit(second)

    record = queue.get_nowait()
    assert record.seq == 2
    assert record.event == second


@pytest.mark.asyncio
async def test_subscribe_with_replay_after_seq_returns_only_later_records():
    bus = SessionBus(session_id="sess-1")
    await bus.emit(ThinkingEvent(status="a"))
    await bus.emit(ThinkingEvent(status="b"))
    await bus.emit(ThinkingEvent(status="c"))

    snapshot, queue = bus.subscribe_with_replay(after_seq=1)

    assert _seqs(snapshot) == [2, 3]
    assert [record.event.status for record in snapshot] == ["b", "c"]
    assert queue.empty()


@pytest.mark.asyncio
async def test_subscribe_with_replay_reports_gap_for_cursor_below_checkpoint():
    bus = SessionBus(session_id="sess-1")
    await bus.emit(ThinkingEvent(status="a"))
    await bus.emit(ThinkingEvent(status="b"))
    bus.mark_checkpoint()
    await bus.emit(ThinkingEvent(status="c"))

    subscription = bus.subscribe_with_replay(after_seq=1)
    snapshot, queue = subscription

    assert subscription.replay_gap is True
    # Only post-checkpoint events are replayed; client must reload history.
    assert _seqs(snapshot) == [3]
    assert queue.empty()


@pytest.mark.asyncio
async def test_subscribe_with_replay_reports_gap_for_future_cursor():
    bus = SessionBus(session_id="sess-1")
    await bus.emit(ThinkingEvent(status="a"))

    subscription = bus.subscribe_with_replay(after_seq=44)
    snapshot, queue = subscription

    assert subscription.replay_gap is True
    assert snapshot == []
    assert queue.empty()


def test_recreated_empty_bus_reports_gap_above_checkpoint():
    bus = SessionBus(session_id="sess-1", initial_next_seq=8, initial_checkpoint_seq=4)

    subscription = bus.subscribe_with_replay(after_seq=4)

    assert subscription.replay_gap is True
    assert subscription.snapshot == []


@pytest.mark.asyncio
async def test_subscribe_with_replay_reports_no_gap_for_satisfiable_cursor():
    bus = SessionBus(session_id="sess-1")
    await bus.emit(ThinkingEvent(status="a"))
    await bus.emit(ThinkingEvent(status="b"))
    await bus.emit(ThinkingEvent(status="c"))

    subscription = bus.subscribe_with_replay(after_seq=1)
    snapshot, queue = subscription

    assert subscription.replay_gap is False
    assert _seqs(snapshot) == [2, 3]
    assert queue.empty()


@pytest.mark.asyncio
async def test_subscribe_with_replay_reports_no_gap_without_cursor():
    bus = SessionBus(session_id="sess-1")
    await bus.emit(ThinkingEvent(status="a"))
    bus.mark_checkpoint()
    await bus.emit(ThinkingEvent(status="b"))

    subscription = bus.subscribe_with_replay()
    snapshot, queue = subscription

    assert subscription.replay_gap is False
    # Without cursor, only post-checkpoint events are replayed (the live
    # tail). Pre-checkpoint events are on disk and the client is expected
    # to reach those via the history endpoint.
    assert _seqs(snapshot) == [2]
    assert queue.empty()


@pytest.mark.asyncio
async def test_subscribe_with_replay_no_gap_for_cursor_at_or_above_checkpoint():
    """The fast-path tab-switch case: client comes back with a cursor
    that's >= the latest checkpoint. We just ship the buffered tail and
    don't force a history reload, even though a checkpoint fired while
    the client was away."""
    bus = SessionBus(session_id="sess-1")
    await bus.emit(ThinkingEvent(status="a"))
    await bus.emit(ThinkingEvent(status="b"))
    bus.mark_checkpoint()  # checkpoint at seq=2
    await bus.emit(ThinkingEvent(status="c"))
    await bus.emit(ThinkingEvent(status="d"))

    # Cursor at the checkpoint: smooth replay, no gap.
    subscription = bus.subscribe_with_replay(after_seq=2)
    assert subscription.replay_gap is False
    assert _seqs(subscription.snapshot) == [3, 4]

    # Cursor above the checkpoint: same.
    subscription = bus.subscribe_with_replay(after_seq=3)
    assert subscription.replay_gap is False
    assert _seqs(subscription.snapshot) == [4]


@pytest.mark.asyncio
async def test_mark_checkpoint_keeps_events_in_buffer():
    """Unlike the prior clear_buffer behavior, mark_checkpoint leaves the
    buffer intact. This lets clients with cursors right at the checkpoint
    see the pre-checkpoint events in their queue (they won't, because the
    snapshot filters by max(after_seq, checkpoint_seq), but emit() to
    existing subscribers is unaffected and the deque retains everything
    until it overflows)."""
    bus = SessionBus(session_id="sess-1")
    await bus.emit(ThinkingEvent(status="a"))
    await bus.emit(ThinkingEvent(status="b"))
    bus.mark_checkpoint()
    await bus.emit(ThinkingEvent(status="c"))

    assert _seqs(list(bus._recent)) == [1, 2, 3]
    assert bus.checkpoint_seq == 2


@pytest.mark.asyncio
async def test_existing_subscribers_keep_receiving_after_checkpoint():
    """mark_checkpoint only affects future subscribers' replay snapshot
    boundary; live subscribers' queues are not touched."""
    bus = SessionBus(session_id="sess-1")
    queue = bus.subscribe()
    await bus.emit(ThinkingEvent(status="one"))
    bus.mark_checkpoint()
    await bus.emit(ThinkingEvent(status="two"))

    assert queue.get_nowait().event.status == "one"
    assert queue.get_nowait().event.status == "two"


@pytest.mark.asyncio
async def test_emit_checkpoint_subscribe_invariant():
    """A new subscriber attached after a checkpoint only sees post-
    checkpoint events in its replay snapshot. Pre-checkpoint events are
    in the buffer but filtered out — the client is expected to fetch
    them via history."""
    bus = SessionBus(session_id="sess-1")

    await bus.emit(ThinkingEvent(status="step1-a"))
    await bus.emit(ThinkingEvent(status="step1-b"))
    bus.mark_checkpoint()
    e3 = ThinkingEvent(status="step2-a")
    await bus.emit(e3)

    snapshot, _q = bus.subscribe_with_replay()
    assert _seqs(snapshot) == [3]
    assert _events(snapshot) == [e3]


@pytest.mark.asyncio
async def test_bus_registry_preserves_checkpoint_seq_across_recreation():
    registry = BusRegistry()
    bus = registry.get_or_create("sess-1")
    await bus.emit(ThinkingEvent(status="a"))
    await bus.emit(ThinkingEvent(status="b"))
    bus.mark_checkpoint()
    assert bus.checkpoint_seq == 2

    registry.remove("sess-1")
    recreated = registry.get_or_create("sess-1")
    assert recreated.checkpoint_seq == 2
    # next_seq is also preserved.
    assert recreated.next_seq == 3


@pytest.mark.asyncio
async def test_buffer_overflow_drops_oldest_silently():
    """The deque's maxlen is sized to cover a single research turn's worth
    of nested events. If a run somehow blows past that, the oldest entries
    are dropped silently — replay sees only the tail. Documented behavior;
    the bump from 2k to 10k makes this rare in practice but the failure
    mode (orphaned tool-call events on the client) is on us if it hits."""
    from arden.server.bus import RECENT_BUFFER_MAX

    bus = SessionBus(session_id="sess-1")
    overflow = RECENT_BUFFER_MAX + 50
    for i in range(overflow):
        await bus.emit(ThinkingEvent(status=f"e{i}"))

    snapshot, _q = bus.subscribe_with_replay()
    assert len(snapshot) == RECENT_BUFFER_MAX
    # Oldest events are gone; tail is preserved.
    assert snapshot[0].event.status == f"e{overflow - RECENT_BUFFER_MAX}"
    assert snapshot[-1].event.status == f"e{overflow - 1}"
