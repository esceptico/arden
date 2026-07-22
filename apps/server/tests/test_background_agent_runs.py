import asyncio
from pathlib import Path

import pytest

from arden.tools.core.context import BackgroundTaskRegistry


@pytest.mark.asyncio
async def test_background_registry_records_started_activity_and_completed():
    calls = []

    async def record(**kwargs):
        calls.append(kwargs)

    results = {}

    async def read_result(task_id):
        return results.get(task_id)

    registry = BackgroundTaskRegistry(session_id="sess-1", record_event=record, read_result=read_result)
    task = asyncio.create_task(asyncio.sleep(0))
    await registry.record_started(
        task_id="bg-1",
        command="research",
        parent_run_id="run-1",
        parent_tool_call_id="call-background",
        agent_type="background_research",
        wait=False,
    )
    registry.register("bg-1", task, command="research")
    await registry.record_activity("bg-1", "read files")
    await registry.deliver_result(
        task_id="bg-1",
        result="done",
        label="research",
        status="completed",
        emit=None,
    )

    assert [c["status"] for c in calls] == ["started", "activity", "completed"]
    assert calls[0]["session_id"] == "sess-1"
    assert calls[0]["parent_run_id"] == "run-1"
    assert calls[0]["parent_tool_call_id"] == "call-background"
    assert calls[0]["agent_type"] == "background_research"
    assert calls[0]["wait"] is False
    assert calls[-1]["terminal"] is True
    assert calls[-1]["result_text"] == "done"

    results["bg-1"] = "done"
    assert await registry.read_background_result("bg-1") == "done"


@pytest.mark.asyncio
async def test_background_registry_injects_hidden_meta_completion_with_result():
    injected = []

    async def on_result(messages):
        injected.extend(messages)

    registry = BackgroundTaskRegistry(session_id="sess-1", on_result=on_result)

    await registry.deliver_result(
        task_id="bg-1",
        result="email summary",
        label="fetch email",
        status="completed",
        emit=None,
    )

    assert injected == [
        {
            "role": "user",
            "content": (
                '<background_agent_result task_id="bg-1" status="completed">\n'
                "This is a hidden completion event. The user cannot see this message.\n"
                "Write a visible assistant response now. Summarize the result directly for the user.\n"
                "If the result contains sources, IDs, links, or evidence, include the relevant ones inline.\n"
                "Do not say the sources/result are above, hidden, attached, in a file, or in the bg result.\n\n"
                "<result>\nemail summary\n</result>\n"
                "</background_agent_result>"
            ),
            "is_meta": True,
            "client_id": "bg:bg-1:completed",
        }
    ]


@pytest.mark.asyncio
async def test_background_registry_delivers_claimed_completion_once():
    emitted = []
    injected = []
    marked = []
    delivered = False

    async def claim_completion(**kwargs):
        return {
            **kwargs,
            "claimed": not delivered,
            "delivered": delivered,
            "completion_id": "bg:bg-1:completed",
        }

    async def mark_delivered(**kwargs):
        nonlocal delivered
        delivered = True
        marked.append(kwargs)

    async def emit(event):
        emitted.append(event)

    async def on_result(messages):
        injected.extend(messages)

    registry = BackgroundTaskRegistry(
        session_id="sess-1",
        on_result=on_result,
        claim_completion=claim_completion,
        mark_completion_delivered=mark_delivered,
    )

    await registry.deliver_result("bg-1", "done", "research", "completed", emit)
    await registry.deliver_result("bg-1", "duplicate", "research", "failed", emit)

    assert len(emitted) == 1
    assert emitted[0].event_id == "bg:bg-1:completed"
    assert len(injected) == 1
    assert marked == [{"session_id": "sess-1", "task_id": "bg-1", "completion_id": "bg:bg-1:completed"}]


@pytest.mark.asyncio
async def test_background_registry_retries_delivery_mark_without_reinjecting():
    emitted = []
    injected = []
    mark_attempts = 0

    async def claim_completion(**kwargs):
        return {**kwargs, "claimed": mark_attempts == 0, "delivered": False}

    async def mark_delivered(**kwargs):
        nonlocal mark_attempts
        mark_attempts += 1
        if mark_attempts == 1:
            raise RuntimeError("crash after delivery")

    registry = BackgroundTaskRegistry(
        session_id="sess-1",
        on_result=lambda messages: _extend(injected, messages),
        claim_completion=claim_completion,
        mark_completion_delivered=mark_delivered,
    )

    with pytest.raises(RuntimeError, match="crash after delivery"):
        await registry.deliver_result("bg-1", "done", "research", "completed", _append(emitted))
    await registry.deliver_result("bg-1", "done", "research", "completed", _append(emitted))

    assert len(emitted) == 1
    assert len(injected) == 1
    assert mark_attempts == 2


def _append(items):
    async def append(item):
        items.append(item)

    return append


async def _extend(items, values):
    items.extend(values)


@pytest.mark.asyncio
async def test_background_result_fallback_rejects_path_traversal(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("arden.tools.core.context.RESULT_BASE", tmp_path)
    result_dir = tmp_path / "sess-1" / "bg_results"
    result_dir.mkdir(parents=True)
    (tmp_path / "secret.txt").write_text("other session secret")
    registry = BackgroundTaskRegistry(session_id="sess-1")

    result = await registry.read_background_result("../../secret")

    assert result is None


@pytest.mark.asyncio
async def test_durable_background_reader_is_authoritative(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("arden.tools.core.context.RESULT_BASE", tmp_path)

    async def missing_owned_result(_task_id: str) -> None:
        return None

    registry = BackgroundTaskRegistry(session_id="sess-1", read_result=missing_owned_result)
    registry._write_result_file("not-owned", "legacy secret")

    assert await registry.read_background_result("not-owned") is None
