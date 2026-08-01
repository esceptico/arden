import pytest
import pytest_asyncio

import arden.database as database
from arden.constants import OFFLOAD_THRESHOLD
from arden.execution import InvocationConflictError, InvocationStatus, InvocationStore


@pytest_asyncio.fixture
async def store(tmp_path):
    conn = await database.connect(tmp_path / "sessions.db")
    store = InvocationStore(conn)
    await store.init_schema()
    yield store
    await conn.close()


async def _create(store: InvocationStore, invocation_id: str = "inv-1"):
    return await store.create(
        invocation_id=invocation_id,
        run_id="run-1",
        session_id="sess-1",
        tool_call_id="call-1",
        tool_name="read_file",
        placement="client",
        arguments_json='{"path": "/tmp/x"}',
    )


@pytest.mark.asyncio
async def test_create_is_idempotent(store):
    first = await _create(store)
    assert first.status == InvocationStatus.REQUESTED

    duplicate = await _create(store)
    assert duplicate == first


@pytest.mark.asyncio
async def test_lifecycle_requested_running_succeeded(store):
    await _create(store)

    running = await store.mark_running("inv-1")
    assert running.status == InvocationStatus.RUNNING
    assert running.attempts == 1
    assert running.started_at is not None

    done = await store.complete("inv-1", status=InvocationStatus.SUCCEEDED, result_payload='{"content": "ok"}')
    assert done.status == InvocationStatus.SUCCEEDED
    assert done.result_payload == '{"content": "ok"}'
    assert done.completed_at is not None


@pytest.mark.asyncio
async def test_duplicate_mark_running_does_not_restart(store):
    await _create(store)
    await store.mark_running("inv-1")

    again = await store.mark_running("inv-1")
    assert again.status == InvocationStatus.RUNNING
    assert again.attempts == 1


@pytest.mark.asyncio
async def test_duplicate_identical_terminal_is_accepted(store):
    await _create(store)
    await store.mark_running("inv-1")
    await store.complete("inv-1", status=InvocationStatus.SUCCEEDED, result_payload='{"content": "ok"}')

    repeat = await store.complete("inv-1", status=InvocationStatus.SUCCEEDED, result_payload='{"content": "ok"}')
    assert repeat.status == InvocationStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_conflicting_terminal_is_rejected(store):
    await _create(store)
    await store.mark_running("inv-1")
    await store.complete("inv-1", status=InvocationStatus.SUCCEEDED, result_payload='{"content": "ok"}')

    with pytest.raises(InvocationConflictError):
        await store.complete("inv-1", status=InvocationStatus.FAILED, result_payload='{"content": "boom"}')

    with pytest.raises(InvocationConflictError):
        await store.complete("inv-1", status=InvocationStatus.SUCCEEDED, result_payload='{"content": "different"}')


@pytest.mark.asyncio
async def test_mark_running_after_terminal_conflicts(store):
    await _create(store)
    await store.complete("inv-1", status=InvocationStatus.CANCELLED, result_payload='{"content": "cancelled"}')

    with pytest.raises(InvocationConflictError):
        await store.mark_running("inv-1")


@pytest.mark.asyncio
async def test_cancel_request_does_not_change_status(store):
    await _create(store)
    await store.mark_running("inv-1")

    record = await store.request_cancel("inv-1")
    assert record.cancel_requested is True
    assert record.status == InvocationStatus.RUNNING

    done = await store.complete("inv-1", status=InvocationStatus.UNCERTAIN, result_payload='{"content": "?"}')
    assert done.status == InvocationStatus.UNCERTAIN


@pytest.mark.asyncio
async def test_oversized_result_spills_to_blob(store):
    await _create(store)
    await store.mark_running("inv-1")

    payload = "x" * (OFFLOAD_THRESHOLD + 1)
    record = await store.complete("inv-1", status=InvocationStatus.SUCCEEDED, result_payload=payload)
    assert record.result_payload is None
    assert record.result_blob_ref is not None
    assert record.result_sha256 is not None


@pytest.mark.asyncio
async def test_open_invocations_filters_by_placement(store):
    await _create(store, "inv-1")
    await store.create(
        invocation_id="inv-2",
        run_id="run-1",
        session_id="sess-1",
        tool_call_id="call-2",
        tool_name="remember",
        placement="server",
        arguments_json="{}",
    )
    await store.complete("inv-2", status=InvocationStatus.SUCCEEDED, result_payload='{"content": "ok"}')

    open_client = await store.open_invocations(placement="client")
    assert [r.invocation_id for r in open_client] == ["inv-1"]
    assert await store.open_invocations(placement="server") == []


@pytest.mark.asyncio
async def test_non_terminal_complete_rejected(store):
    await _create(store)
    with pytest.raises(ValueError):
        await store.complete("inv-1", status=InvocationStatus.RUNNING, result_payload="{}")
