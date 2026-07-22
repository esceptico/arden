import asyncio
import json
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import InitVar, dataclass, field, replace
from typing import Protocol

from arden.events.sse import (
    EPHEMERAL_EVENT_TYPES,
    ReasoningMessageContentEvent,
    SSEEvent,
    StreamResetEvent,
    TextMessageContentEvent,
    ToolCallArgsEvent,
)
from arden.logging import get_logger

SSE_QUEUE_MAXSIZE = 2048
# The persist queue holds STRUCTURAL events only (ephemeral deltas are filtered
# before submit), so it fills far slower than the raw event rate. Generous
# headroom + backpressure-on-overflow (see SessionEventWriter.submit) means a
# durable event is never dropped — only delayed if the disk writer falls behind.
SESSION_EVENT_RECORD_QUEUE_MAXSIZE = 50000
# In-memory replay buffer (ALL events incl. deltas) for short-gap reconnects.
# Sized for a single research turn that fans out to ~150 nested tool calls (each
# emits START + ARGS + END + RESULT = 4 events, plus text deltas). Below this, an
# overflowing run evicts early events and a mid-run reconnect falls into a
# replay_gap (full history reload). ~15k events × ~200B = ~3MB per active session.
RECENT_BUFFER_MAX = 15000
# Max durable events flushed in one transaction by the writer worker. Bounds the
# size of a single commit while letting a backlog drain in few commits instead of
# one-commit-per-event (which starves request-path writes on the shared conn).
RECORD_WRITE_BATCH_MAX = 256
# Hard ceiling on how long close() waits to flush pending durable events before
# forcing the worker down. A slow/stuck DB write must never wedge server
# shutdown (the recurring "can't stop the server"); on timeout the few unflushed
# events are abandoned (the process is exiting anyway).
CLOSE_DRAIN_TIMEOUT_SECONDS = 5.0
# Attempts to persist a batch before abandoning it. A transient DB error (e.g. a
# SQLITE_BUSY lock timeout) should be retried, not dropped — a dropped batch is
# durable structural-event loss that desyncs a long-gap reconnect.
RECORD_WRITE_RETRIES = 3
_logger = get_logger(__name__)


class SessionEventCursorStore(Protocol):
    async def get_latest_session_event_seq(self, session_id: str) -> int: ...

    async def get_latest_session_checkpoint_seq(self, session_id: str) -> int: ...


@dataclass(frozen=True, slots=True)
class StreamRecord:
    seq: int
    session_id: str
    event: SSEEvent


@dataclass(frozen=True, slots=True)
class ReplaySubscription:
    snapshot: list[StreamRecord]
    queue: asyncio.Queue[StreamRecord | None]
    replay_gap: bool = False

    def __iter__(self):
        yield self.snapshot
        yield self.queue


def stream_record_to_sse_string(session_id: str, record: StreamRecord, *, replay: bool = False) -> str:
    sse = record.event.to_sse()
    payload = json.loads(sse["data"])
    payload["seq"] = record.seq
    payload["session_id"] = record.session_id
    if replay:
        payload["replay"] = True
    return f"id: {record.seq}\nevent: {sse['event']}\ndata: {json.dumps(payload)}\n\n"


def _compact_adjacent_records(records: list[StreamRecord]) -> list[StreamRecord]:
    compacted: list[StreamRecord] = []
    for record in records:
        if compacted:
            merged = _merge_adjacent_record(compacted[-1], record)
            if merged is not None:
                compacted[-1] = merged
                continue
        compacted.append(record)
    return compacted


def _merge_adjacent_record(left: StreamRecord, right: StreamRecord) -> StreamRecord | None:
    if left.session_id != right.session_id:
        return None
    merged_event = _merge_adjacent_event(left.event, right.event)
    if merged_event is None:
        return None
    return StreamRecord(seq=right.seq, session_id=right.session_id, event=merged_event)


def _merge_adjacent_event(left: SSEEvent, right: SSEEvent) -> SSEEvent | None:
    if isinstance(left, TextMessageContentEvent) and isinstance(right, TextMessageContentEvent):
        if (
            left.message_id,
            left.depth,
            left.parent_id,
        ) == (
            right.message_id,
            right.depth,
            right.parent_id,
        ):
            return replace(right, delta=left.delta + right.delta)
        return None

    if isinstance(left, ReasoningMessageContentEvent) and isinstance(right, ReasoningMessageContentEvent):
        if (
            left.message_id,
            left.depth,
            left.parent_id,
        ) == (
            right.message_id,
            right.depth,
            right.parent_id,
        ):
            return replace(right, delta=left.delta + right.delta)
        return None

    if isinstance(left, ToolCallArgsEvent) and isinstance(right, ToolCallArgsEvent):
        if (
            left.tool_call_id,
            left.depth,
            left.parent_id,
        ) == (
            right.tool_call_id,
            right.depth,
            right.parent_id,
        ):
            return replace(right, delta=left.delta + right.delta)
    return None


class SessionEventWriter:
    def __init__(
        self,
        record_events: Callable[[list[StreamRecord]], Awaitable[None]],
        *,
        maxsize: int = SESSION_EVENT_RECORD_QUEUE_MAXSIZE,
        close_timeout: float = CLOSE_DRAIN_TIMEOUT_SECONDS,
    ):
        self._record_events = record_events
        self._close_timeout = close_timeout
        self._queue: asyncio.Queue[StreamRecord | None] = asyncio.Queue(maxsize=maxsize)
        self._worker: asyncio.Task[None] | None = None
        # Overflow holding area, drained into the queue in strict FIFO by a
        # single pump task. See submit() for why order matters.
        self._backlog: deque[StreamRecord] = deque()
        self._pump: asyncio.Task[None] | None = None

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize() + len(self._backlog)

    def submit(self, record: StreamRecord) -> None:
        self._ensure_worker()
        # Only STRUCTURAL (durable) events reach here — ephemeral deltas are
        # filtered upstream. Two invariants must hold:
        #   1. No drops. Dropping one desyncs the client transcript on reconnect
        #      (a tool stuck "running", a missing RUN_FINISHED).
        #   2. Strict FIFO persistence. The resume path (durable_replay_records)
        #      treats a HOLE in the durable ledger as an omitted ephemeral delta
        #      by design. If a later seq persisted ahead of an earlier structural
        #      one, that earlier seq would look like a hole and be silently
        #      skipped on replay. So once anything is backlogged, everything
        #      queues behind it — the writer never reorders structural events.
        if self._backlog:
            self._backlog.append(record)
            return
        try:
            self._queue.put_nowait(record)
        except asyncio.QueueFull:
            _logger.error(
                "session_event_persist_queue_full; applying backpressure (not dropping)",
                session_id=record.session_id,
                seq=record.seq,
                queue_size=self._queue.maxsize,
            )
            self._backlog.append(record)
            self._ensure_pump()

    def _ensure_pump(self) -> None:
        if self._pump is None or self._pump.done():
            self._pump = asyncio.create_task(self._drain_backlog())

    async def _drain_backlog(self) -> None:
        # Single owner of the backlog→queue handoff, so puts stay in seq order.
        # peek-then-pop (not pop-then-put) keeps the record visible to drain()
        # until it has actually landed in the queue.
        while self._backlog:
            await self._queue.put(self._backlog[0])
            self._backlog.popleft()

    async def drain(self) -> None:
        if self._worker is None:
            return
        # Flush the FIFO backlog into the queue first (a new submit during drain
        # may spawn a fresh pump), then wait for the worker to persist everything.
        while self._pump is not None and not self._pump.done():
            await self._pump
        await self._queue.join()

    async def _drain_and_stop(self) -> None:
        await self.drain()
        await self._queue.put(None)
        await self._worker

    async def close(self) -> None:
        if self._worker is None:
            return
        try:
            await asyncio.wait_for(self._drain_and_stop(), timeout=self._close_timeout)
        except TimeoutError:
            # A stuck/slow durable write must never wedge shutdown. Abandon the
            # unflushed events (only in this dying process's buffer) and force
            # the worker + pump down so close() always returns.
            _logger.warning(
                "session_event_writer_close_timeout; abandoning unflushed events to unblock shutdown",
                unflushed=self.queue_depth,
            )
            for task in (self._pump, self._worker):
                if task is not None and not task.done():
                    task.cancel()
                    # Await so the task's finally (task_done) actually runs before
                    # we return; bounded so a wedged cancel can't re-hang close().
                    try:
                        await asyncio.wait_for(task, timeout=1.0)
                    except (asyncio.CancelledError, Exception):
                        pass
        self._worker = None

    def _ensure_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run())

    async def _run(self) -> None:
        # Persist in BATCHES: drain everything already queued and write it in one
        # transaction (one commit) instead of one commit per event. Under a
        # high-volume run (research fan-out, child_io subagent streams), per-event
        # commits monopolize the single shared write connection and starve
        # request-path writes (POST /chat/message), which is what made chat
        # requests time out until a reload. Batching cuts commits by 1-2 orders
        # of magnitude. FIFO is preserved (queue is drained in order).
        while True:
            first = await self._queue.get()
            if first is None:
                self._queue.task_done()
                return
            batch = [first]
            saw_sentinel = False
            while len(batch) < RECORD_WRITE_BATCH_MAX:
                try:
                    nxt = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if nxt is None:
                    saw_sentinel = True
                    break
                batch.append(nxt)
            try:
                await self._write_batch(batch)
            finally:
                for _ in batch:
                    self._queue.task_done()
                if saw_sentinel:
                    self._queue.task_done()
            if saw_sentinel:
                return

    async def _write_batch(self, batch: list[StreamRecord]) -> None:
        """Persist a batch, retrying transient failures with backoff before
        giving up. Never raises — a permanently-failing batch is logged and
        abandoned (the in-memory replay buffer still covers a short-gap
        reconnect); the worker must keep draining either way."""
        delay = 0.1
        for attempt in range(1, RECORD_WRITE_RETRIES + 1):
            try:
                await self._record_events(batch)
                return
            except Exception as exc:
                if attempt >= RECORD_WRITE_RETRIES:
                    _logger.warning(
                        "session_event_persist_failed_after_retries",
                        session_id=batch[0].session_id,
                        seqs=f"{batch[0].seq}..{batch[-1].seq}",
                        count=len(batch),
                        attempts=attempt,
                        error=str(exc),
                    )
                    return
                await asyncio.sleep(delay)
                delay = min(delay * 4, 2.0)


@dataclass
class SessionBus:
    # Live subscriber fanout is intentionally process-local. The supported
    # server mode is a single uvicorn worker; cross-process correctness comes
    # from durable replay after reconnect, not shared live pub/sub.
    session_id: str
    subscriber_queue_size: int = SSE_QUEUE_MAXSIZE
    initial_next_seq: InitVar[int] = 1
    initial_checkpoint_seq: InitVar[int] = 0
    record_queue_size: InitVar[int] = SESSION_EVENT_RECORD_QUEUE_MAXSIZE
    record_events: Callable[[list[StreamRecord]], Awaitable[None]] | None = None
    _subscribers: list[asyncio.Queue[StreamRecord | None]] = field(default_factory=list)
    _next_seq: int = field(default=1, init=False)
    # Highest seq that has been persisted to disk via session_service.save().
    # The buffer keeps every event regardless; this watermark just tells
    # reconnecting clients which side of the disk/buffer boundary their
    # cursor falls on. A cursor < _checkpoint_seq means the client missed
    # state that's now only on disk → stream_reset → history reload. A
    # cursor >= _checkpoint_seq means a clean buffered replay is enough.
    _checkpoint_seq: int = field(default=0, init=False)
    # Live replay buffer. Bounded by RECENT_BUFFER_MAX; oldest evicts on
    # overflow. Unlike the prior design, we don't clear at checkpoint —
    # so a fast reconnect after a step boundary stays in the buffered-
    # replay path instead of triggering a stream_reset + history reload.
    _recent: deque[StreamRecord] = field(default_factory=lambda: deque(maxlen=RECENT_BUFFER_MAX))
    _record_writer: SessionEventWriter | None = field(default=None, init=False)
    _emit_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    def __post_init__(self, initial_next_seq: int, initial_checkpoint_seq: int, record_queue_size: int) -> None:
        self._next_seq = initial_next_seq
        self._checkpoint_seq = initial_checkpoint_seq
        if self.record_events:
            self._record_writer = SessionEventWriter(self.record_events, maxsize=record_queue_size)

    @property
    def next_seq(self) -> int:
        return self._next_seq

    @property
    def checkpoint_seq(self) -> int:
        return self._checkpoint_seq

    async def emit(self, event: SSEEvent) -> None:
        async with self._emit_lock:
            record = StreamRecord(seq=self._next_seq, session_id=self.session_id, event=event)
            self._next_seq += 1
            self._recent.append(record)
            self._schedule_record_event(record)
            for queue in tuple(self._subscribers):
                try:
                    queue.put_nowait(record)
                except asyncio.QueueFull:
                    if isinstance(record.event, StreamResetEvent):
                        self._close_reset_subscriber(queue, record)
                    elif self._compact_and_enqueue(queue, record):
                        continue
                    else:
                        self._close_slow_subscriber(queue)

    def _schedule_record_event(self, record: StreamRecord) -> None:
        # Token-level deltas live only in the in-memory buffer (appended in
        # emit() above) for live streaming + short-gap reconnect; they are not
        # persisted durably. Skipping them here keeps the durable event log
        # bounded and avoids serializing/writing ~89% of events for no
        # reachable replay benefit.
        if record.event.type in EPHEMERAL_EVENT_TYPES:
            return
        if self._record_writer:
            self._record_writer.submit(record)

    async def drain_record_tasks(self) -> None:
        if self._record_writer:
            await self._record_writer.drain()

    async def close_record_writer(self) -> None:
        if self._record_writer:
            await self._record_writer.close()

    def mark_checkpoint(self) -> None:
        """Record that every event up to the latest emitted seq is now
        persisted to disk by the chat service. Reconnecting clients with
        a cursor below this point will be served a stream_reset so they
        rehydrate from history instead of the buffer (which may have
        evicted those events, and even if not, replaying them would
        double-apply over the freshly loaded history)."""
        self._checkpoint_seq = self._next_seq - 1

    def subscribe_with_replay(self, after_seq: int | None = None) -> ReplaySubscription:
        """Atomically snapshot the replay buffer AND register a live queue.
        emit() never awaits, so no event can interleave between snapshot and
        queue registration.

        Replay snapshot only includes events with `seq > _checkpoint_seq`
        — events at or below the checkpoint are on disk and the client is
        expected to fetch them via the history endpoint, not replay."""
        live_floor = max(after_seq if after_seq is not None else 0, self._checkpoint_seq)
        snapshot = [record for record in self._recent if record.seq > live_floor]
        replay_gap = self._has_replay_gap(after_seq) if after_seq is not None else False
        queue: asyncio.Queue[StreamRecord | None] = asyncio.Queue(maxsize=self.subscriber_queue_size)
        self._subscribers.append(queue)
        return ReplaySubscription(snapshot=snapshot, queue=queue, replay_gap=replay_gap)

    def _has_replay_gap(self, after_seq: int) -> bool:
        newest_seq = self._next_seq - 1
        if after_seq > newest_seq:
            return True
        if after_seq < self._checkpoint_seq:
            return True
        if not self._recent and after_seq < newest_seq:
            return True
        # Buffer eviction can in theory leave a gap above the checkpoint
        # if a single step emits more events than RECENT_BUFFER_MAX. In
        # practice steps are far smaller than that, but we guard anyway.
        if self._recent and after_seq < self._recent[0].seq - 1:
            return True
        return False

    def subscribe(self) -> asyncio.Queue[StreamRecord | None]:
        """Live-only subscription — for feeds that don't replay (e.g. the
        global automation stream)."""
        queue: asyncio.Queue[StreamRecord | None] = asyncio.Queue(maxsize=self.subscriber_queue_size)
        self._subscribers.append(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[StreamRecord | None]) -> None:
        try:
            self._subscribers.remove(queue)
        except ValueError:
            pass

    def close_all_sync(self) -> None:
        for queue in tuple(self._subscribers):
            self.unsubscribe(queue)
            self._close_queue(queue)

    def _close_slow_subscriber(self, queue: asyncio.Queue[StreamRecord | None]) -> None:
        self.unsubscribe(queue)
        _logger.warning(
            "sse_slow_subscriber_closed",
            session_id=self.session_id,
            latest_seq=self._next_seq - 1,
            checkpoint_seq=self._checkpoint_seq,
        )
        reset_record = StreamRecord(
            seq=max(0, self._checkpoint_seq),
            session_id=self.session_id,
            event=StreamResetEvent(reason="slow_consumer"),
        )
        self._close_queue(queue, terminal=reset_record)

    @staticmethod
    def _compact_and_enqueue(
        queue: asyncio.Queue[StreamRecord | None],
        incoming: StreamRecord,
    ) -> bool:
        records: list[StreamRecord] = []
        while True:
            try:
                record = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if record is None:
                return False
            records.append(record)

        compacted = _compact_adjacent_records([*records, incoming])
        if len(compacted) > queue.maxsize:
            return False

        for record in compacted:
            queue.put_nowait(record)
        return True

    def _close_reset_subscriber(
        self,
        queue: asyncio.Queue[StreamRecord | None],
        reset_record: StreamRecord,
    ) -> None:
        self.unsubscribe(queue)
        _logger.warning(
            "sse_reset_subscriber_preempted",
            session_id=self.session_id,
            reset_seq=reset_record.seq,
            reason=getattr(reset_record.event, "reason", ""),
        )
        self._close_queue(queue, terminal=reset_record)

    @staticmethod
    def _close_queue(
        queue: asyncio.Queue[StreamRecord | None],
        *,
        terminal: StreamRecord | None = None,
    ) -> None:
        while True:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        if terminal is not None:
            queue.put_nowait(terminal)
        if not queue.full():
            queue.put_nowait(None)


class BusRegistry:
    def __init__(
        self,
        *,
        record_events: Callable[[list[StreamRecord]], Awaitable[None]] | None = None,
    ):
        self._buses: dict[str, SessionBus] = {}
        self._next_seq_by_session: dict[str, int] = {}
        self._checkpoint_seq_by_session: dict[str, int] = {}
        self._record_events = record_events

    def get_or_create(self, session_id: str) -> SessionBus:
        if session_id not in self._buses:
            self._buses[session_id] = SessionBus(
                session_id=session_id,
                initial_next_seq=self._next_seq_by_session.get(session_id, 1),
                initial_checkpoint_seq=self._checkpoint_seq_by_session.get(session_id, 0),
                record_events=self._record_events,
            )
        return self._buses[session_id]

    def remember_session_cursor(
        self,
        session_id: str,
        *,
        next_seq: int,
        checkpoint_seq: int | None = None,
    ) -> None:
        if session_id in self._buses:
            return
        self._next_seq_by_session[session_id] = max(next_seq, self._next_seq_by_session.get(session_id, 1))
        if checkpoint_seq is not None:
            self._checkpoint_seq_by_session[session_id] = max(
                checkpoint_seq,
                self._checkpoint_seq_by_session.get(session_id, 0),
            )

    def get(self, session_id: str) -> SessionBus | None:
        return self._buses.get(session_id)

    def remove(self, session_id: str) -> None:
        bus = self._buses.pop(session_id, None)
        if bus:
            self._next_seq_by_session[session_id] = bus.next_seq
            self._checkpoint_seq_by_session[session_id] = bus.checkpoint_seq

    async def remove_if_idle(self, session_id: str, *, is_active: Callable[[], bool]) -> None:
        bus = self._buses.get(session_id)
        if bus is None:
            return
        await bus.drain_record_tasks()
        if bus._emit_lock.locked():
            async with bus._emit_lock:
                pass
        if self._buses.get(session_id) is not bus:
            return
        if bus._subscribers or is_active():
            return
        await bus.close_record_writer()
        self.remove(session_id)

    def close_all_sync(self) -> None:
        for bus in self._buses.values():
            self._next_seq_by_session[bus.session_id] = bus.next_seq
            self._checkpoint_seq_by_session[bus.session_id] = bus.checkpoint_seq
            bus.close_all_sync()

    async def close_all(self) -> None:
        self.close_all_sync()
        await asyncio.gather(
            *(bus.close_record_writer() for bus in self._buses.values()),
            return_exceptions=True,
        )
        self._buses.clear()


async def prime_bus_cursor_from_store(
    bus_registry: BusRegistry,
    session_id: str,
    event_store: SessionEventCursorStore | None,
) -> None:
    if bus_registry.get(session_id) is not None:
        return
    if event_store is None:
        return
    latest_seq = await event_store.get_latest_session_event_seq(session_id)
    if bus_registry.get(session_id) is not None:
        return
    if not latest_seq:
        return
    checkpoint_seq = await event_store.get_latest_session_checkpoint_seq(session_id)
    bus_registry.remember_session_cursor(
        session_id,
        next_seq=int(latest_seq) + 1,
        checkpoint_seq=int(checkpoint_seq),
    )
