import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from arden.core.public_refs import public_ref
from arden.server.state import RunRegistry, RunState, RunStatus
from arden.tools.core.context import ResourceObservation


def _agent_ref(task_id: str) -> str:
    return public_ref("agent", task_id, empty_slug="agent")


def test_run_registry_restores_explicit_run_id():
    registry = RunRegistry()

    run = registry.create_run("sess-x", run_id="original-run")

    assert run.run_id == "original-run"
    assert registry.get_run("original-run") is run


def test_run_registry_shares_resource_observations_across_session_runs():
    registry = RunRegistry()
    first = registry.create_run("sess-x")
    first.resource_observations["wiki:page"] = ResourceObservation("v1", "head-1", True)

    second = registry.create_run("sess-x")
    other_session = registry.create_run("sess-y")

    assert second.resource_observations["wiki:page"] == ResourceObservation("v1", "head-1", True)
    assert other_session.resource_observations == {}


@pytest.mark.asyncio
async def test_cancel_endpoint_resolves_active_run_by_session():
    from arden.server.routers.chat import cancel_run as cancel_run_endpoint
    from arden.server.schemas import CancelRequest

    registry = RunRegistry()
    run = registry.create_run("sess-x")
    run.status = RunStatus.RUNNING

    result = await cancel_run_endpoint(CancelRequest(session_id="sess-x"), run_registry=registry)

    assert result["run_id"] == run.run_id
    assert result["found"] is True
    assert run.cancelled is True


@pytest.mark.asyncio
async def test_cancel_endpoint_404_when_no_active_run_for_session():
    from fastapi import HTTPException

    from arden.server.routers.chat import cancel_run as cancel_run_endpoint
    from arden.server.schemas import CancelRequest

    registry = RunRegistry()
    with pytest.raises(HTTPException) as exc:
        await cancel_run_endpoint(CancelRequest(session_id="missing"), run_registry=registry)
    assert exc.value.status_code == 404


def test_run_state_owns_injection_queue_lifecycle():
    run = RunState(run_id="run-1", session_id="sess-1")

    run.queue_injection({"role": "user", "content": "first", "client_id": "cid-1"})
    run.queue_injection({"role": "user", "content": "second", "client_id": "cid-2"})

    assert run.pending_injection_count == 2
    assert run.cancel_injection("cid-1") is True
    assert run.cancel_injection("missing") is False

    assert run.drain_injections() == [{"role": "user", "content": "second", "client_id": "cid-2"}]
    assert run.pending_injection_count == 0
    assert run.injection_was_drained("cid-2") is True
    assert run.injection_was_drained("cid-1") is False
    assert run.drain_injections() == []


def test_run_state_queues_injection_batches():
    run = RunState(run_id="run-1", session_id="sess-1")

    run.queue_injections([])
    run.queue_injections(
        [
            {"role": "user", "content": "first"},
            {"role": "user", "content": "second"},
        ]
    )

    assert run.drain_injections() == [
        {"role": "user", "content": "first"},
        {"role": "user", "content": "second"},
    ]


def test_run_state_status_is_content_free():
    now = datetime(2026, 4, 28, 12, 0, tzinfo=UTC)
    run = RunState(
        run_id="run-1",
        session_id="sess-1",
        created_at=now - timedelta(seconds=30),
        updated_at=now - timedelta(seconds=5),
    )
    run.messages = [{"role": "user", "content": "do not leak this"}]
    run.queue_injection({"role": "user", "content": "queued secret", "client_id": "cid-1"})
    run.updated_at = now - timedelta(seconds=5)

    status = run.get_status(now)

    assert status["message_count"] == 1
    assert status["pending_injections"] == 1
    assert status["age_seconds"] == 30
    assert status["idle_seconds"] == 5
    assert "messages" not in status
    assert "content" not in status


def test_run_registry_status_reports_active_runs_only():
    now = datetime(2026, 4, 28, 12, 0, tzinfo=UTC)
    registry = RunRegistry()
    active = registry.create_run("sess-active")
    active.status = RunStatus.RUNNING
    active.queue_injection({"role": "user", "content": "queued"})

    stale = registry.create_run("sess-stale")
    stale.status = RunStatus.COMPLETED

    status = registry.get_status(now)

    assert status["observed_at"] == now.isoformat()
    assert status["total_retained"] == 2
    assert status["active_count"] == 1
    assert status["active_runs"][0]["run_id"] == active.run_id
    assert status["active_runs"][0]["pending_injections"] == 1
    assert status["background_task_sessions"] == []


def test_backgrounded_run_status_reports_backgrounded():
    now = datetime(2026, 4, 28, 12, 0, tzinfo=UTC)
    registry = RunRegistry()
    run = registry.create_run("sess-bg")
    run.status = RunStatus.RUNNING
    run.backgrounded = True

    status = registry.get_status(now)

    assert status["active_runs"][0]["status"] == "backgrounded"
    assert status["active_runs"][0]["backgrounded"] is True
    assert registry.get_active_run("sess-bg") is run
    assert registry.get_accepting_run("sess-bg") is None


def test_backgrounded_run_remains_listed_after_new_foreground_run():
    registry = RunRegistry()
    backgrounded = registry.create_run("sess-1")
    backgrounded.status = RunStatus.RUNNING
    backgrounded.backgrounded = True

    foreground = registry.create_run("sess-1")
    foreground.status = RunStatus.RUNNING

    assert registry.get_active_run("sess-1") is foreground
    assert registry.get_accepting_run("sess-1") is foreground
    assert {run.run_id for run in registry.list_active_runs()} == {
        backgrounded.run_id,
        foreground.run_id,
    }

    registry.complete_run(backgrounded.run_id)

    assert registry.get_active_run("sess-1") is foreground
    assert [run.run_id for run in registry.list_active_runs()] == [foreground.run_id]


def test_cancel_run_keeps_run_active_until_terminal_cancel():
    registry = RunRegistry()
    run = registry.create_run("sess-1")
    run.status = RunStatus.RUNNING

    result = registry.cancel_run(run.run_id)

    assert result["found"] is True
    assert run.cancelled is True
    assert run.status == RunStatus.RUNNING
    assert registry.get_active_run("sess-1") is run

    registry.finish_cancelled(run.run_id)

    assert run.status == RunStatus.CANCELLED
    assert registry.get_active_run("sess-1") is None


def test_stale_complete_and_error_do_not_clear_newer_active_run():
    registry = RunRegistry()
    old = registry.create_run("sess-1")
    old.status = RunStatus.RUNNING
    newer = registry.create_run("sess-1")
    newer.status = RunStatus.RUNNING

    registry.complete_run(old.run_id)

    assert registry.get_active_run("sess-1") is newer

    older_error = registry.create_run("sess-2")
    older_error.status = RunStatus.RUNNING
    newer_after_error = registry.create_run("sess-2")
    newer_after_error.status = RunStatus.RUNNING

    registry.error_run(older_error.run_id)

    assert registry.get_active_run("sess-2") is newer_after_error


@pytest.mark.asyncio
async def test_stale_cancel_does_not_cancel_newer_background_tasks():
    registry = RunRegistry()
    old = registry.create_run("sess-1")
    old.status = RunStatus.RUNNING
    newer = registry.create_run("sess-1")
    newer.status = RunStatus.RUNNING
    bg_registry = registry.get_background_registry("sess-1")
    started = asyncio.Event()
    release = asyncio.Event()

    async def background_work():
        started.set()
        await release.wait()

    task = asyncio.create_task(background_work())
    await asyncio.wait_for(started.wait(), timeout=1)
    bg_registry.register("task-1", task, "research")

    try:
        result = registry.cancel_run(old.run_id)

        assert result["found"] is True
        assert result["cancel_requested"] is False
        assert not task.cancelled()
        assert not task.done()
        assert bg_registry.pending_count == 1
        assert registry.get_active_run("sess-1") is newer
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_cancel_all_cancels_background_registry_tasks():
    registry = RunRegistry()
    bg_registry = registry.get_background_registry("sess-1")
    started = asyncio.Event()
    release = asyncio.Event()

    async def background_work():
        started.set()
        await release.wait()

    task = asyncio.create_task(background_work())
    await asyncio.wait_for(started.wait(), timeout=1)
    bg_registry.register("task-1", task, "research")

    cancelled = await registry.cancel_all(timeout=0.1)

    assert cancelled == 1
    await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()


@pytest.mark.asyncio
async def test_cancel_all_cancels_pending_run_task():
    registry = RunRegistry()
    run = registry.create_run("sess-1")
    started = asyncio.Event()
    release = asyncio.Event()

    async def pending_work():
        started.set()
        await release.wait()

    task = asyncio.create_task(pending_work())
    await asyncio.wait_for(started.wait(), timeout=1)
    run.task = task

    cancelled = await registry.cancel_all(timeout=0.1)

    assert cancelled == 1
    await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()


@pytest.mark.asyncio
async def test_cancel_all_marks_backgrounded_drain_cancelled():
    registry = RunRegistry()
    run = registry.create_run("sess-1")
    run.status = RunStatus.RUNNING
    run.backgrounded = True
    started = asyncio.Event()
    release = asyncio.Event()

    async def drain_work():
        started.set()
        await release.wait()

    task = asyncio.create_task(drain_work())
    await asyncio.wait_for(started.wait(), timeout=1)
    run.drain_task = task

    cancelled = await registry.cancel_all(timeout=0.1)

    assert cancelled == 1
    assert run.cancelled is True
    await asyncio.gather(task, return_exceptions=True)
    assert task.cancelled()


def test_lookup_otid_returns_none_for_unknown():
    registry = RunRegistry()
    assert registry.lookup_otid("sess-1", "cid-missing") is None
    assert registry.lookup_otid("sess-1", "") is None


def test_lookup_otid_returns_run_when_registered():
    registry = RunRegistry()
    run = registry.create_run("sess-1")
    registry.register_otid("sess-1", "cid-1", run.run_id)
    assert registry.lookup_otid("sess-1", "cid-1") is run


def test_lookup_otid_expires_after_ttl():
    from arden.server.state import OTID_DEDUP_TTL

    registry = RunRegistry()
    run = registry.create_run("sess-1")
    registry.register_otid("sess-1", "cid-1", run.run_id)
    expired = datetime.now(UTC) - OTID_DEDUP_TTL - timedelta(seconds=1)
    registry._otid_runs[("sess-1", "cid-1")] = (run.run_id, expired)

    assert registry.lookup_otid("sess-1", "cid-1") is None
    assert ("sess-1", "cid-1") not in registry._otid_runs


def test_register_otid_ignores_empty_client_id():
    registry = RunRegistry()
    registry.register_otid("sess-1", "", "run-A")
    assert registry._otid_runs == {}


def test_otid_scoped_per_session():
    registry = RunRegistry()
    registry.create_run("sess-1")
    registry.create_run("sess-2")
    registry.register_otid("sess-1", "cid-1", "run-A")
    assert registry.lookup_otid("sess-2", "cid-1") is None


def _live_run_with_state(registry: RunRegistry, session_id: str, **state_over) -> RunState:
    from arden.context.models import SessionState

    run = registry.create_run(session_id)
    run.status = RunStatus.RUNNING
    run.session_state = SessionState(session_id=session_id, started_at=datetime.now(UTC), **state_over)
    return run


def test_sync_session_chat_model_reaches_a_live_runs_state():
    # save_session rewrites the whole sessions row from the run's in-memory
    # state at run end — a PUT /sessions/{id}/model landing mid-run must
    # reach that state or the finished run reverts the pin.
    registry = RunRegistry()
    run = _live_run_with_state(registry, "sess-x", chat_model="gpt-5.2")

    registry.sync_session_chat_model("sess-x", "claude-fable-5")

    assert run.session_state.chat_model == "claude-fable-5"


def test_sync_session_chat_model_leaves_settled_runs_alone():
    registry = RunRegistry()
    run = _live_run_with_state(registry, "sess-x", chat_model="gpt-5.2")
    run.status = RunStatus.COMPLETED

    registry.sync_session_chat_model("sess-x", "claude-fable-5")

    assert run.session_state.chat_model == "gpt-5.2"


def test_sync_session_chat_model_without_a_run_is_a_no_op():
    RunRegistry().sync_session_chat_model("nobody-home", "claude-fable-5")


@pytest.mark.asyncio
async def test_cancel_run_cascades_into_child_sessions():
    registry = RunRegistry()
    run = registry.create_run("sess-1")
    run.status = RunStatus.RUNNING
    bg = registry.get_background_registry("sess-1")
    child_task = asyncio.create_task(asyncio.sleep(3600))
    bg.reserve(
        "agent-A",
        command="a",
        limit=16,
        agent_ref=_agent_ref("agent-A"),
        child_session_id="sess-1::a",
        parent_run_id=run.run_id,
    )
    bg.register("agent-A", child_task, command="a")
    grand = registry.get_background_registry("sess-1::a")
    grand_task = asyncio.create_task(asyncio.sleep(3600))
    grand.reserve(
        "agent-B",
        command="b",
        limit=16,
        agent_ref=_agent_ref("agent-B"),
        child_session_id="sess-1::a::b",
        parent_run_id="child-run",
    )
    grand.register("agent-B", grand_task, command="b")

    try:
        result = registry.cancel_run(run.run_id)

        assert result["cancel_requested"] is True
        assert ("sess-1", "agent-A") in result["cancelled_children"]
        assert ("sess-1::a", "agent-B") in result["cancelled_children"]
        with pytest.raises(asyncio.CancelledError):
            await child_task
        with pytest.raises(asyncio.CancelledError):
            await grand_task
    finally:
        child_task.cancel()
        grand_task.cancel()
        await asyncio.gather(child_task, grand_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_stale_cancel_stops_only_its_own_agents():
    registry = RunRegistry()
    old = registry.create_run("sess-1")
    old.status = RunStatus.RUNNING
    newer = registry.create_run("sess-1")
    newer.status = RunStatus.RUNNING
    bg = registry.get_background_registry("sess-1")
    old_task = asyncio.create_task(asyncio.sleep(3600))
    bg.reserve("agent-old", command="a", limit=16, agent_ref=_agent_ref("agent-old"), parent_run_id=old.run_id)
    bg.register("agent-old", old_task, command="a")
    new_task = asyncio.create_task(asyncio.sleep(3600))
    bg.reserve("agent-new", command="b", limit=16, agent_ref=_agent_ref("agent-new"), parent_run_id=newer.run_id)
    bg.register("agent-new", new_task, command="b")

    try:
        result = registry.cancel_run(old.run_id)

        assert result["cancel_requested"] is True
        assert result["cancelled_children"] == [("sess-1", "agent-old")]
        with pytest.raises(asyncio.CancelledError):
            await old_task
        assert not new_task.done()
        assert registry.get_active_run("sess-1") is newer
    finally:
        old_task.cancel()
        new_task.cancel()
        await asyncio.gather(old_task, new_task, return_exceptions=True)
