"""Tests for durable awaiting: an awaited spawn writes a subagent_result
run-suspension row, resolves it when the child settles, and a restarted
parent replaying the spawning tool call picks the recorded outcome up (or
re-spawns a child that died with the server, bounded)."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from arden.agent import Choice, CompletionResponse, Message, Usage
from arden.context.models import SessionState
from arden.core import spawner as spawner_module
from arden.core.spawner import create_spawn_fn
from arden.server.state import RunRegistry
from arden.tools.core.context import BackgroundTaskRegistry, IOBridge, RunContext, ToolContext
from tests.helpers import make_executor


def _suspension_io(existing: dict | None = None) -> tuple[IOBridge, dict[str, list[dict]]]:
    calls: dict[str, list[dict]] = {"record": [], "resolve": [], "consume": []}

    async def get_suspension(**kwargs):
        return existing

    async def record_suspension(**kwargs):
        calls["record"].append(kwargs)

    async def resolve_suspension(**kwargs):
        calls["resolve"].append(kwargs)

    async def consume_suspension(**kwargs):
        calls["consume"].append(kwargs)

    io = IOBridge(
        get_suspension=get_suspension,
        consume_suspension=consume_suspension,
        record_suspension=record_suspension,
        resolve_suspension=resolve_suspension,
    )
    return io, calls


def _make_ctx(
    io: IOBridge,
    *,
    bg_registry: BackgroundTaskRegistry | None = None,
    run_registry: RunRegistry | None = None,
    run_id: str = "run-1",
    services: dict | None = None,
) -> ToolContext:
    executor = make_executor()
    return ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=executor.registry,
        run=RunContext(run_id=run_id, current_depth=0, max_depth=3),
        io=io,
        services=services or {},
        background_tasks=bg_registry or BackgroundTaskRegistry(session_id="test"),
        run_registry=run_registry,
    )


def _text_llm(text: str = "done"):
    class FakeLLM:
        async def stream(self, messages, model, tools, tool_choice=None, reasoning_effort=None, prompt_cache_key=None):
            yield CompletionResponse(
                choices=[
                    Choice(
                        message=Message(role="assistant", content=text, tool_calls=None, reasoning_content=None),
                        finish_reason="stop",
                    )
                ],
                usage=Usage(),
                model=model,
            )

    return FakeLLM()


def _spawn_fn():
    return create_spawn_fn(executor=make_executor(), model="test-model", max_depth=3, current_depth=0)


@pytest.mark.asyncio
async def test_awaited_spawn_records_and_resolves_suspension_on_completion(monkeypatch):
    monkeypatch.setattr(spawner_module, "llm_client", _text_llm("done"))
    io, calls = _suspension_io()
    ctx = _make_ctx(io)

    result = await _spawn_fn()(
        ctx,
        "research task",
        system_prompt="sys",
        tools=[],
        parent_id="call-research",
        agent_type="research",
        timeout=1,
    )

    assert result.status == "completed"
    assert len(calls["record"]) == 1
    record = calls["record"][0]
    assert record["run_id"] == "run-1"
    assert record["session_id"] == "test"
    assert record["suspension_id"] == "call-research"
    assert record["kind"] == "subagent_result"
    assert record["payload"]["child_run_id"] == result.child_run_id
    assert record["payload"]["agent_type"] == "research"
    assert record["payload"]["respawns"] == 0
    assert calls["resolve"] == [
        {
            "run_id": "run-1",
            "suspension_id": "call-research",
            "status": "completed",
            "resolution": {"status": "completed", "result": "done"},
        }
    ]


@pytest.mark.asyncio
async def test_awaited_spawn_resolves_suspension_as_failed_on_salvage(monkeypatch):
    class FailingLLM:
        async def stream(self, messages, model, tools, tool_choice=None, reasoning_effort=None, prompt_cache_key=None):
            raise RuntimeError("oops")
            yield  # pragma: no cover

        async def complete(self, model, messages, **kwargs):
            return CompletionResponse(
                choices=[
                    Choice(
                        message=Message(role="assistant", content="partial", tool_calls=None, reasoning_content=None),
                        finish_reason="stop",
                    )
                ],
                usage=Usage(),
                model=model,
            )

    monkeypatch.setattr(spawner_module, "llm_client", FailingLLM())
    io, calls = _suspension_io()
    ctx = _make_ctx(io)

    result = await _spawn_fn()(
        ctx,
        "research task",
        system_prompt="sys",
        tools=[],
        parent_id="call-research",
        timeout=1,
    )

    assert result.status == "failed"
    assert [c["status"] for c in calls["resolve"]] == ["failed"]
    assert calls["resolve"][0]["resolution"]["status"] == "failed"
    assert calls["resolve"][0]["resolution"]["result"] == result.text


@pytest.mark.asyncio
async def test_awaited_spawn_resolves_suspension_as_cancelled(monkeypatch):
    class SlowAgent:
        hooks = SimpleNamespace(on_response=None)

        async def stream(self, messages):
            messages.append({"role": "assistant", "content": "Found useful evidence."})
            await asyncio.sleep(60)

    monkeypatch.setattr(spawner_module, "Agent", lambda **kwargs: SlowAgent())
    monkeypatch.setattr(spawner_module, "_salvage_summary", AsyncMock(return_value="Partial summary."))

    registry = RunRegistry()
    parent_run = registry.create_run("test")
    io, calls = _suspension_io()
    ctx = _make_ctx(io, run_registry=registry, run_id=parent_run.run_id)

    task = asyncio.create_task(
        _spawn_fn()(ctx, "research trace replay", system_prompt="sys", tools=[], parent_id="call-research")
    )
    await asyncio.sleep(0)
    assert registry.cancel_subagent(parent_run.run_id, "call-research") == {"found": True, "cancel_requested": True}

    result = await task
    assert result.status == "cancelled"
    assert [c["status"] for c in calls["resolve"]] == ["cancelled"]
    assert calls["resolve"][0]["resolution"]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_replay_with_resolved_suspension_returns_recorded_result_without_spawning(monkeypatch):
    class BoomLLM:
        async def stream(self, *args, **kwargs):
            raise AssertionError("must not spawn on a resolved suspension")
            yield  # pragma: no cover

    monkeypatch.setattr(spawner_module, "llm_client", BoomLLM())

    events: list[dict] = []

    async def record_event(**event):
        events.append(event)

    io, calls = _suspension_io(
        existing={
            "kind": "subagent_result",
            "status": "completed",
            "resolution": {"status": "completed", "result": "recorded answer"},
            "payload": {"child_run_id": "agent-old", "child_session_id": "test::abcd1234"},
        }
    )
    session_service = SimpleNamespace(provision_state=AsyncMock(), save=AsyncMock())
    bg_registry = BackgroundTaskRegistry(session_id="test", record_event=record_event)
    ctx = _make_ctx(io, bg_registry=bg_registry, services={"session": session_service})

    result = await _spawn_fn()(
        ctx,
        "research task",
        system_prompt="sys",
        tools=[],
        parent_id="call-research",
        agent_type="research",
        timeout=1,
    )

    assert result.text == "recorded answer"
    assert result.status == "completed"
    assert result.wait is True
    assert result.child_run_id == "agent-old"
    assert result.child_session_id == "test::abcd1234"
    assert calls["consume"] == [{"run_id": "run-1", "suspension_id": "call-research"}]
    # Nothing was spawned: no child session, no roster row, no new suspension.
    session_service.provision_state.assert_not_awaited()
    assert events == []
    assert bg_registry.list_pending() == []
    assert calls["record"] == []
    assert calls["resolve"] == []


@pytest.mark.asyncio
async def test_replay_with_pending_suspension_respawns_and_increments_counter(monkeypatch):
    monkeypatch.setattr(spawner_module, "llm_client", _text_llm("fresh answer"))
    io, calls = _suspension_io(
        existing={
            "kind": "subagent_result",
            "status": "pending",
            "resolution": None,
            "payload": {"child_run_id": "agent-dead", "respawns": 0},
        }
    )
    ctx = _make_ctx(io)

    result = await _spawn_fn()(
        ctx,
        "research task",
        system_prompt="sys",
        tools=[],
        parent_id="call-research",
        timeout=1,
    )

    assert result.status == "completed"
    assert result.text == "fresh answer"
    # A fresh child ran under a NEW id; the (upserted) row carries it plus the
    # bumped respawn counter.
    assert result.child_run_id != "agent-dead"
    assert len(calls["record"]) == 1
    assert calls["record"][0]["payload"]["respawns"] == 1
    assert calls["record"][0]["payload"]["child_run_id"] == result.child_run_id
    assert [c["status"] for c in calls["resolve"]] == ["completed"]


@pytest.mark.asyncio
async def test_replay_with_pending_suspension_caps_respawns(monkeypatch):
    class BoomLLM:
        async def stream(self, *args, **kwargs):
            raise AssertionError("must not respawn past the cap")
            yield  # pragma: no cover

    monkeypatch.setattr(spawner_module, "llm_client", BoomLLM())
    io, calls = _suspension_io(
        existing={
            "kind": "subagent_result",
            "status": "pending",
            "resolution": None,
            "payload": {"child_run_id": "agent-dead", "child_session_id": "test::dead", "respawns": 2},
        }
    )
    bg_registry = BackgroundTaskRegistry(session_id="test")
    ctx = _make_ctx(io, bg_registry=bg_registry)

    result = await _spawn_fn()(
        ctx,
        "research task",
        system_prompt="sys",
        tools=[],
        parent_id="call-research",
        timeout=1,
    )

    assert result.status == "failed"
    assert "restarts" in result.text
    assert calls["record"] == []
    assert [c["status"] for c in calls["resolve"]] == ["failed"]
    assert calls["resolve"][0]["resolution"]["status"] == "failed"
    assert calls["consume"] == [{"run_id": "run-1", "suspension_id": "call-research"}]
    assert bg_registry.list_pending() == []


@pytest.mark.asyncio
async def test_workflow_parallel_spawns_get_distinct_suspension_ids(monkeypatch):
    monkeypatch.setattr(spawner_module, "llm_client", _text_llm())
    io, calls = _suspension_io()
    ctx = _make_ctx(io)
    spawn = _spawn_fn()

    # Workflow fan-out: MANY awaited children share the workflow's one
    # tool_call_id — the per-spawn lifecycle_id must key the rows instead.
    for lifecycle_id in ("wf-1:analyst:a1b2", "wf-1:analyst:c3d4"):
        await spawn(
            ctx,
            "worker task",
            system_prompt="sys",
            tools=[],
            parent_id="call-workflow",
            lifecycle_id=lifecycle_id,
            workflow_id="wf-1",
            phase="analyze",
            timeout=1,
        )
    # A research spawn has no lifecycle_id: its stable tool_call_id is the key.
    await spawn(ctx, "research task", system_prompt="sys", tools=[], parent_id="call-research", timeout=1)

    assert [c["suspension_id"] for c in calls["record"]] == [
        "wf-1:analyst:a1b2",
        "wf-1:analyst:c3d4",
        "call-research",
    ]
    assert len({c["suspension_id"] for c in calls["record"]}) == 3
    assert [c["suspension_id"] for c in calls["resolve"]] == [
        "wf-1:analyst:a1b2",
        "wf-1:analyst:c3d4",
        "call-research",
    ]
