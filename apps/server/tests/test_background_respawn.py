"""Restart recovery for orphaned detached background agents."""

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

import arden.database as database
from arden.context.store import SessionStore
from arden.core.isolation import IsolationLevel
from arden.core.spawn_spec import ParentRef, SpawnSpec
from arden.outbox import OUTBOX_AGENT_RUN_REQUESTED, OutboxStore
from arden.server.app import _enqueue_background_respawns
from arden.server.bus import BusRegistry
from arden.server.runtime.outbox import RuntimeOutbox
from arden.server.state import RunRegistry
from arden.services.chat import respawn_background_agent


@pytest_asyncio.fixture
async def store(tmp_path: Path):
    conn = await database.connect(tmp_path / "sessions.db")
    completion_conn = await database.connect(tmp_path / "sessions.db")
    read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
    s = SessionStore(conn, read_conn, completion_conn)
    await s.init_schema()
    yield s
    await read_conn.close()
    await completion_conn.close()
    await conn.close()


@pytest_asyncio.fixture
async def outbox_store(tmp_path: Path):
    conn = await database.connect(tmp_path / "outbox.db")
    s = OutboxStore(conn)
    await s.init_schema()
    yield s
    await conn.close()


def _spec(session_id: str = "sess-1") -> str:
    return SpawnSpec(
        context=(
            {"role": "system", "content": "You are a background researcher."},
            {"role": "user", "content": "Summarize the repo."},
        ),
        agent_type="background_research",
        tools=("bash",),
        model="model-x",
        reasoning_effort=None,
        timeout_seconds=1800,
        isolation=IsolationLevel.FULL,
        parent=ParentRef(session_id=session_id, run_id="run-1", tool_call_id="call-1"),
        workflow=None,
    ).to_json()


async def _start_detached(store: SessionStore, task_id: str, *, wait: bool = False, spawn_spec: str | None = None):
    await store.record_background_agent_started(
        task_id=task_id,
        session_id="sess-1",
        parent_run_id="run-1",
        command="research",
        wait=wait,
        spawn_spec=spawn_spec,
    )


class _Scheduler:
    async def handle_run_completed(self, event, *, preserve_next_run=False):
        pass

    async def handle_run_failed(self, event):
        pass


class _FakeSessionService:
    def __init__(self, store: SessionStore, session=None):
        self.store = store
        self._session = session

    async def load(self, session_id=None):
        return self._session


@pytest.mark.asyncio
async def test_startup_pass_enqueues_only_qualifying_interrupted_runs(store: SessionStore, outbox_store: OutboxStore):
    await _start_detached(store, "bg-detached", spawn_spec=_spec())
    await _start_detached(store, "bg-awaited", wait=True, spawn_spec=_spec())
    await _start_detached(store, "bg-nospec")
    await _start_detached(store, "bg-done", spawn_spec=_spec())
    await store.record_background_agent_finished(
        task_id="bg-done",
        session_id="sess-1",
        status="completed",
        result_text="done",
    )
    await store.mark_interrupted_background_agent_runs()

    await _enqueue_background_respawns(store, outbox_store)

    events = await outbox_store.claim_batch(worker_id="test-worker", limit=10)
    assert [event.event_type for event in events] == [OUTBOX_AGENT_RUN_REQUESTED]
    assert events[0].idempotency_key == "agent.run.requested:bg-detached:1"
    assert events[0].payload == {"session_id": "sess-1", "task_id": "bg-detached"}
    await outbox_store.mark_completed(events[0].id)

    row = await store.get_background_agent_run("sess-1", "bg-detached")
    assert row["spawn_attempts"] == 1

    # Second boot with the row still interrupted: attempt 2 under a fresh key.
    await _enqueue_background_respawns(store, outbox_store)
    events = await outbox_store.claim_batch(worker_id="test-worker", limit=10)
    assert [event.idempotency_key for event in events] == ["agent.run.requested:bg-detached:2"]
    assert (await store.get_background_agent_run("sess-1", "bg-detached"))["spawn_attempts"] == 2


@pytest.mark.asyncio
async def test_startup_pass_skips_rows_at_the_attempts_cap(store: SessionStore, outbox_store: OutboxStore):
    await _start_detached(store, "bg-capped", spawn_spec=_spec())
    await store.mark_interrupted_background_agent_runs()
    assert await store.increment_background_agent_spawn_attempts("sess-1", "bg-capped") == 1
    assert await store.increment_background_agent_spawn_attempts("sess-1", "bg-capped") == 2

    await _enqueue_background_respawns(store, outbox_store)

    assert await outbox_store.claim_batch(worker_id="test-worker", limit=10) == []


@pytest.mark.asyncio
async def test_respawn_noops_before_building_the_host(store: SessionStore):
    run_registry = RunRegistry()
    buses = BusRegistry()

    def build_deps():
        raise AssertionError("build_deps must not be called for a no-op respawn")

    session_service = _FakeSessionService(store)

    # Unknown row.
    assert (
        await respawn_background_agent(
            run_registry,
            build_deps,
            buses,
            session_id="sess-1",
            task_id="missing",
            session_service=session_service,
        )
        is False
    )

    # Row exists but is not interrupted anymore.
    await _start_detached(store, "bg-running", spawn_spec=_spec())
    assert (
        await respawn_background_agent(
            run_registry,
            build_deps,
            buses,
            session_id="sess-1",
            task_id="bg-running",
            session_service=session_service,
        )
        is False
    )

    # Interrupted but the registry already tracks a live task for the id.
    await _start_detached(store, "bg-live", spawn_spec=_spec())
    await store.mark_interrupted_background_agent_runs()
    live = asyncio.create_task(asyncio.sleep(10))
    run_registry.get_background_registry("sess-1").register("bg-live", live, command="research")
    try:
        assert (
            await respawn_background_agent(
                run_registry,
                build_deps,
                buses,
                session_id="sess-1",
                task_id="bg-live",
                session_service=session_service,
            )
            is False
        )
    finally:
        live.cancel()

    # Interrupted with a spec, but the parent session no longer loads.
    await _start_detached(store, "bg-orphan", spawn_spec=_spec())
    await store.mark_interrupted_background_agent_runs()
    assert (
        await respawn_background_agent(
            run_registry,
            build_deps,
            buses,
            session_id="sess-1",
            task_id="bg-orphan",
            session_service=session_service,
        )
        is False
    )


@pytest.mark.asyncio
async def test_agent_worker_completes_the_event_on_dispatch(outbox_store: OutboxStore):
    dispatched: list[tuple[str, str]] = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def handler(session_id: str, task_id: str) -> None:
        dispatched.append((session_id, task_id))
        started.set()
        await release.wait()

    runtime_outbox = RuntimeOutbox(outbox_store=outbox_store, scheduler=_Scheduler())
    runtime_outbox.set_agent_run_handler(handler)
    await outbox_store.enqueue_agent_run_requested(session_id="sess-1", task_id="bg-1", attempt=1)

    # The main worker must never claim agent events.
    assert await runtime_outbox.worker.process_once() is False

    assert await runtime_outbox.agent_worker.process_once() is True
    await asyncio.wait_for(started.wait(), timeout=1)
    assert dispatched == [("sess-1", "bg-1")]

    # Completed on dispatch — nothing left to claim while the respawn still runs.
    assert await outbox_store.claim_batch(worker_id="test-worker", limit=10) == []
    status = await outbox_store.get_status()
    assert status["by_status"]["completed"] == 1

    release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_respawn_host_failure_settles_row_and_notifies(store: SessionStore):
    """The E2E-caught silent-loss bug: if the host blows up after the no-op
    guards, the row must settle failed and the session must be told — never an
    interrupted row the roster believes is alive."""
    from datetime import UTC, datetime
    from types import SimpleNamespace

    from arden.context.models import SessionState

    run_registry = RunRegistry()
    buses = BusRegistry()
    await _start_detached(store, "bg-crash", spawn_spec=_spec())
    await store.mark_interrupted_background_agent_runs()

    dispatched: list[tuple] = []

    async def dispatch(session_id, content, client_id, is_meta, extra):
        dispatched.append((session_id, content, client_id, is_meta))
        return {"run_id": "hidden-run"}

    class _ExplodingExecutor:
        def get_tools(self, **kwargs):
            raise RuntimeError("boom: executor misconfigured")

    session = SimpleNamespace(state=SessionState(session_id="sess-1", started_at=datetime.now(UTC)))
    session_service = _FakeSessionService(store, session=session)
    deps = SimpleNamespace(
        executor=_ExplodingExecutor(),
        agent_config=SimpleNamespace(approval_timeout_seconds=300),
        dispatch_session_message=dispatch,
    )

    result = await respawn_background_agent(
        run_registry,
        lambda: deps,
        buses,
        session_id="sess-1",
        task_id="bg-crash",
        session_service=session_service,
    )

    assert result is False
    row = await store.get_background_agent_run("sess-1", "bg-crash")
    assert row["status"] == "failed"
    assert "[respawn failed]" in (row["result_text"] or "")
    assert dispatched, "the session must be told the agent is gone"
    _sid, content, client_id, is_meta = dispatched[0]
    assert 'status="failed"' in content
    assert "app_followup_task" in content
    assert client_id == "bg:bg-crash:respawn-failed"
    assert is_meta is True
