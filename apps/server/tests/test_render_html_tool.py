"""render_html tool — display/input modes, the pending_inputs blocking
mechanism, skip-approvals isolation, and the /tools/result input branch."""

import asyncio
import json
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ntrp.context.models import SessionState
from ntrp.events.sse import InputNeededEvent
from ntrp.server.routers.chat import router as chat_router
from ntrp.server.runtime import get_runtime
from ntrp.server.state import RunRegistry
from ntrp.tools.core.context import (
    BackgroundTaskRegistry,
    IOBridge,
    RunContext,
    ToolContext,
    ToolExecution,
)
from ntrp.tools.core.registry import ToolRegistry
from ntrp.tools.render_html import render_html_tool


def _make_execution(io: IOBridge, tool_id: str = "toolu_01abc") -> ToolExecution:
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=ToolRegistry(),
        run=RunContext(run_id="run-1"),
        io=io,
        background_tasks=BackgroundTaskRegistry(session_id="test"),
    )
    return ToolExecution(tool_id=tool_id, tool_name="render_html", ctx=ctx)


def test_tool_kind_is_html_widget():
    assert render_html_tool.kind == "html_widget"
    assert render_html_tool.policy.offload is False


@pytest.mark.asyncio
async def test_display_mode_returns_immediately():
    execution = _make_execution(IOBridge())

    result = await render_html_tool.execute(execution, html="<div>x</div>", title="T", mode="display")

    assert result.content == "Rendered HTML T."
    assert result.preview == "T"
    assert result.data == {"html": "<div>x</div>", "title": "T", "mode": "display"}
    assert result.is_error is False


@pytest.mark.asyncio
async def test_input_mode_emits_event_blocks_and_returns_envelope_verbatim():
    emitted = []
    pending: dict[str, asyncio.Future] = {}

    async def emit(event):
        emitted.append(event)

    execution = _make_execution(IOBridge(emit=emit, pending_inputs=pending))

    task = asyncio.create_task(
        render_html_tool.execute(execution, html="<form></form>", title="Pick a time slot", mode="input")
    )
    for _ in range(20):
        if "toolu_01abc" in pending and emitted:
            break
        await asyncio.sleep(0)

    assert "toolu_01abc" in pending
    assert len(emitted) == 1
    event = emitted[0]
    assert isinstance(event, InputNeededEvent)
    assert event.tool_id == "toolu_01abc"
    assert event.name == "render_html"
    assert event.title == "Pick a time slot"
    assert event.html == "<form></form>"

    envelope = '{"action": "accept", "values": {"a": 1}}'
    pending["toolu_01abc"].set_result(
        {"type": "tool_response", "tool_id": "toolu_01abc", "result": envelope, "approved": True}
    )
    result = await task

    assert result.content == envelope
    assert result.preview == "Pick a time slot"
    assert result.data == {"html": "<form></form>", "title": "Pick a time slot", "mode": "input"}
    assert result.is_error is False
    assert pending == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["decline", "cancel"])
async def test_input_mode_decline_and_cancel_pass_through_verbatim(action: str):
    pending: dict[str, asyncio.Future] = {}

    async def emit(event):
        pass

    execution = _make_execution(IOBridge(emit=emit, pending_inputs=pending))
    task = asyncio.create_task(render_html_tool.execute(execution, html="<form></form>", title="T", mode="input"))
    for _ in range(20):
        if "toolu_01abc" in pending:
            break
        await asyncio.sleep(0)

    envelope = json.dumps({"action": action, "values": {}})
    pending["toolu_01abc"].set_result(
        {"type": "tool_response", "tool_id": "toolu_01abc", "result": envelope, "approved": True}
    )
    result = await task

    assert result.content == envelope
    assert result.is_error is False


@pytest.mark.asyncio
async def test_input_mode_timeout_resolves_to_cancel_envelope():
    async def emit(event):
        pass

    execution = _make_execution(IOBridge(emit=emit, pending_inputs={}, approval_timeout_seconds=0.001))

    result = await render_html_tool.execute(execution, html="<form></form>", title="T", mode="input")

    assert result.content == json.dumps({"action": "cancel", "values": {}})
    assert result.is_error is False


@pytest.mark.asyncio
async def test_input_mode_fails_fast_without_interactive_client():
    async def emit(event):
        pass

    for io in (IOBridge(), IOBridge(emit=emit)):
        execution = _make_execution(io)
        result = await render_html_tool.execute(execution, html="<form></form>", title="T", mode="input")
        assert result.is_error is True
        assert "No interactive client connected" in result.content


@pytest.mark.asyncio
async def test_set_skip_approvals_does_not_resolve_pending_inputs():
    registry = RunRegistry()
    run = registry.create_run("sess-1")
    future = asyncio.get_running_loop().create_future()
    run.pending_inputs["toolu_01abc"] = future

    resolved = run.set_skip_approvals(True)

    assert resolved == 0
    assert not future.done()


@pytest.fixture
def router_client():
    registry = RunRegistry()
    run = registry.create_run("sess-1")
    loop = asyncio.new_event_loop()
    future = loop.create_future()
    run.pending_inputs["toolu_01abc"] = future

    class _Runtime:
        run_registry = registry

    test_app = FastAPI()
    test_app.include_router(chat_router)
    test_app.dependency_overrides[get_runtime] = lambda: _Runtime()
    with TestClient(test_app) as client:
        yield client, run, future
    loop.close()


def test_tools_result_resolves_pending_input(router_client):
    client, run, future = router_client
    envelope = '{"action":"accept","values":{"rating":4}}'
    body = {"run_id": run.run_id, "tool_id": "toolu_01abc", "result": envelope, "approved": True}

    response = client.post("/tools/result", json=body)

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert future.result() == {
        "type": "tool_response",
        "tool_id": "toolu_01abc",
        "result": envelope,
        "approved": True,
    }

    second = client.post("/tools/result", json=body)
    assert second.status_code == 409

    unknown = client.post(
        "/tools/result",
        json={"run_id": run.run_id, "tool_id": "toolu_nope", "result": "", "approved": True},
    )
    assert unknown.status_code == 404
