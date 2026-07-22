import asyncio
import json
import time
from collections.abc import AsyncGenerator, Callable, Iterable

from arden.events.sse import KeepaliveEvent, SSEEvent, StreamResetEvent
from arden.server.bus import SessionBus, StreamRecord, stream_record_to_sse_string

ShouldEmit = Callable[[SSEEvent], bool]
LIVE_RECORD_BATCH_MAX = 128
LIVE_RECORD_BATCH_MAX_BYTES = 64 * 1024


def keepalive_chunk(session_id: str, latest_seq: int) -> str:
    event = KeepaliveEvent(session_id=session_id, latest_seq=latest_seq)
    sse = event.to_sse()
    payload = {
        **json.loads(sse["data"]),
        "seq": latest_seq,
        "session_id": session_id,
    }
    return f"id: {latest_seq}\nevent: {sse['event']}\ndata: {json.dumps(payload)}\n\n"


def reset_chunk(session_id: str, reason: str, seq: int) -> str:
    reset_record = StreamRecord(
        seq=max(0, seq),
        session_id=session_id,
        event=StreamResetEvent(reason=reason),
    )
    return stream_record_to_sse_string(session_id, reset_record)


async def replay_records(
    session_id: str,
    records: Iterable[StreamRecord],
    *,
    should_emit: ShouldEmit,
) -> AsyncGenerator[str]:
    for record in records:
        if not should_emit(record.event):
            continue
        yield stream_record_to_sse_string(session_id, record, replay=True)
        await asyncio.sleep(0)


async def live_records(
    *,
    bus: SessionBus,
    queue: asyncio.Queue[StreamRecord | None],
    session_id: str,
    should_emit: ShouldEmit,
    keepalive_interval: float,
    replay_upper_seq: int | None = None,
) -> AsyncGenerator[str]:
    last_event_at = time.monotonic()
    pending: StreamRecord | None = None

    while True:
        if pending is None:
            try:
                record = await asyncio.wait_for(queue.get(), timeout=0.5)
            except TimeoutError:
                if time.monotonic() - last_event_at >= keepalive_interval:
                    last_event_at = time.monotonic()
                    yield keepalive_chunk(session_id, bus.next_seq - 1)
                continue
        else:
            record = pending
            pending = None

        if record is None:
            break

        last_event_at = time.monotonic()
        chunks: list[str] = []
        chunk_bytes = 0
        records_taken = 0
        terminal = False

        while record is not None:
            records_taken += 1
            if (
                replay_upper_seq is not None
                and record.seq <= replay_upper_seq
                and not isinstance(record.event, StreamResetEvent)
            ):
                pass
            else:
                event = record.event
                if should_emit(event):
                    chunk = stream_record_to_sse_string(session_id, record)
                    encoded_size = len(chunk.encode("utf-8"))
                    if chunks and chunk_bytes + encoded_size > LIVE_RECORD_BATCH_MAX_BYTES:
                        pending = record
                        break

                    chunks.append(chunk)
                    chunk_bytes += encoded_size
                    if isinstance(event, StreamResetEvent):
                        terminal = True
                        break

            if records_taken >= LIVE_RECORD_BATCH_MAX:
                break
            try:
                record = queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if record is None:
                terminal = True
                break

        if chunks:
            yield "".join(chunks)

        if terminal:
            break
        await asyncio.sleep(0)
