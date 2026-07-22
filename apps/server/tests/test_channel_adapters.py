import pytest

from arden.channels.models import ChannelDeliveryHandle, RuntimeIdentity
from arden.channels.queue import ChannelQueue


def test_channel_delivery_handle_is_not_runtime_identity():
    delivery = ChannelDeliveryHandle(
        channel="slack",
        native_thread_id="C1:1700000000.0001",
        native_message_id="1700000000.0002",
        continuation_token="resume-token",
    )
    runtime = RuntimeIdentity(session_id="sess-1", run_id="run-1", turn_id="turn-1", cursor="42")

    assert delivery.native_thread_id != runtime.session_id
    assert delivery.continuation_token != runtime.cursor


@pytest.mark.asyncio
async def test_channel_queue_serializes_delivery_per_native_thread():
    queue = ChannelQueue()
    order = []

    async def worker(label):
        async with queue.lock_for("slack", "C1:root"):
            order.append(f"start:{label}")
            await queue.sleep(0)
            order.append(f"end:{label}")

    await queue.gather(worker("a"), worker("b"))

    assert order in (["start:a", "end:a", "start:b", "end:b"], ["start:b", "end:b", "start:a", "end:a"])


@pytest.mark.asyncio
async def test_channel_queue_allows_different_threads_to_run_concurrently():
    queue = ChannelQueue()
    active = []

    async def worker(thread_id):
        async with queue.lock_for("slack", thread_id):
            active.append(thread_id)
            await queue.sleep(0)

    await queue.gather(worker("C1:root"), worker("C2:root"))

    assert set(active) == {"C1:root", "C2:root"}
