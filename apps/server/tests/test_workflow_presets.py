"""Workflow presets — registry/service round-trip and the workflow tool's preset resolution."""

import ast
import asyncio
import textwrap
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from arden.context.models import SessionState
from arden.orchestra.dynamic import run_script
from arden.skills.registry import SkillRegistry
from arden.skills.service import BUILTIN_SKILLS_DIR
from arden.tools.core.context import (
    BackgroundTaskRegistry,
    IOBridge,
    RunContext,
    ToolContext,
    ToolExecution,
)
from arden.tools.core.registry import ToolRegistry
from arden.tools.workflow import WorkflowInput, run_workflow

PRESET_DESCRIPTION = "Echo preset returning args x."
PRESET_SCRIPT = 'return args.get("x", "ok")'


def write_preset(base: Path, name: str = "echo", kind: str = "workflow") -> None:
    skill_dir = base / name
    skill_dir.mkdir(parents=True)
    kind_line = f"kind: {kind}\n" if kind != "skill" else ""
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {PRESET_DESCRIPTION}\n{kind_line}---\n\n# {name}\n"
    )
    if kind == "workflow":
        (skill_dir / "workflow.py").write_text(PRESET_SCRIPT + "\n")


@pytest.fixture
def registry(tmp_path: Path) -> SkillRegistry:
    write_preset(tmp_path)
    write_preset(tmp_path, name="plain-skill", kind="skill")
    reg = SkillRegistry()
    reg.load([(tmp_path, "builtin")])
    return reg


def make_ctx(registry: SkillRegistry, events: list, delivered: list | None = None) -> ToolContext:
    async def emit(event) -> None:
        events.append(event)

    async def spawn_fn(*args, **kwargs):  # pragma: no cover - presets under test never spawn
        raise AssertionError("spawn_fn should not be called")

    background_tasks = BackgroundTaskRegistry(session_id="s1")
    if delivered is not None:

        async def on_result(messages: list[dict]) -> None:
            delivered.extend(messages)

        background_tasks.on_result = on_result
    return ToolContext(
        session_state=SessionState(session_id="s1", started_at=datetime.now(UTC)),
        registry=ToolRegistry(),
        run=RunContext(run_id="r1"),
        io=IOBridge(emit=emit),
        services={"skill_registry": registry},
        spawn_fn=spawn_fn,
        background_tasks=background_tasks,
    )


async def await_workflow(ctx: ToolContext, result) -> None:
    """Block on the detached workflow's registry task (tests only)."""
    task = ctx.background_tasks.task(result.data["workflow_id"])
    assert task is not None
    await task


def test_builtin_workflow_presets_compile():
    """Every builtin preset script must parse under the same wrapping run_script
    uses — a syntax error here would only surface on the user's first run."""
    scripts = sorted(BUILTIN_SKILLS_DIR.glob("*/workflow.py"))
    assert {s.parent.name for s in scripts} >= {"audit", "investigate", "panel", "implement"}
    for script in scripts:
        source = f"async def __workflow__():\n{textwrap.indent(script.read_text().strip(), '    ')}\n"
        ast.parse(source, str(script), "exec")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "args, expected_message",
    [
        ({}, "panel requires a non-empty args['question']."),
        ({"question": "Choose a cache", "n": 0}, "panel args['n'] must be an integer from 1 to 5."),
        ({"question": "Choose a cache", "n": True}, "panel args['n'] must be an integer from 1 to 5."),
        ({"question": "Choose a cache", "n": 2.9}, "panel args['n'] must be an integer from 1 to 5."),
        ({"question": "Choose a cache", "n": "many"}, "panel args['n'] must be an integer from 1 to 5."),
    ],
)
async def test_panel_preset_rejects_invalid_inputs_before_spawning(args, expected_message):
    script = (BUILTIN_SKILLS_DIR / "panel" / "workflow.py").read_text()
    orchestra = SimpleNamespace(
        agent=None,
        parallel=None,
        pipeline=None,
        phase=None,
        log=None,
        budget_view=None,
    )

    with pytest.raises(ValueError) as error:
        await run_script(orchestra, script, args)
    assert str(error.value) == expected_message


def test_load_workflow_script_only_for_workflow_kind(registry: SkillRegistry):
    assert registry.load_workflow_script("echo") == PRESET_SCRIPT + "\n"
    assert registry.load_workflow_script("plain-skill") is None
    assert registry.load_workflow_script("missing") is None


@pytest.mark.asyncio
async def test_run_workflow_unknown_preset_lists_available(registry: SkillRegistry):
    ctx = make_ctx(registry, [])
    execution = ToolExecution(tool_id="t1", tool_name="workflow", ctx=ctx)

    result = await run_workflow(execution, WorkflowInput(name="nope"))

    assert result.is_error is True
    assert "echo" in result.content
    assert "plain-skill" not in result.content


def test_workflow_schema_has_no_executable_script_field():
    assert "script" not in WorkflowInput.model_fields


@pytest.mark.asyncio
async def test_run_workflow_rejects_user_saved_python_preset(tmp_path: Path):
    write_preset(tmp_path)
    registry = SkillRegistry()
    registry.load([(tmp_path, "global")])
    ctx = make_ctx(registry, [])
    execution = ToolExecution(tool_id="t1", tool_name="workflow", ctx=ctx)

    result = await run_workflow(execution, WorkflowInput(name="echo"))

    assert result.is_error is True
    assert result.preview == "Untrusted workflow"


@pytest.mark.asyncio
async def test_run_workflow_preset_resolves_and_carries_description(registry: SkillRegistry):
    events: list = []
    delivered: list = []
    ctx = make_ctx(registry, events, delivered)
    execution = ToolExecution(tool_id="t1", tool_name="workflow", ctx=ctx)

    result = await run_workflow(execution, WorkflowInput(name="echo", args={"x": "hi"}))

    # Detached: the tool returns a receipt immediately …
    assert result.is_error is None or result.is_error is False
    assert "delivered to this conversation automatically" in result.content
    assert result.data["workflow_id"].startswith("wf-")
    started = next(e for e in events if type(e).__name__ == "WorkflowStartedEvent")
    assert started.name == "echo"
    assert started.description == PRESET_DESCRIPTION

    # … and the orchestra result arrives via the standard hidden completion.
    await await_workflow(ctx, result)
    finished = next(e for e in events if type(e).__name__ == "WorkflowFinishedEvent")
    assert finished.status == "completed"
    assert len(delivered) == 1
    assert 'status="completed"' in delivered[0]["content"]
    assert "<result>\nhi\n</result>" in delivered[0]["content"]


@pytest.mark.asyncio
async def test_run_workflow_started_event_carries_declared_phases(registry: SkillRegistry):
    events: list = []
    ctx = make_ctx(registry, events)
    execution = ToolExecution(tool_id="t1", tool_name="workflow", ctx=ctx)

    result = await run_workflow(
        execution,
        WorkflowInput(name="echo", title="planned", phases=["find", "verify"]),
    )

    assert result.is_error is None or result.is_error is False
    started = next(e for e in events if type(e).__name__ == "WorkflowStartedEvent")
    assert started.phases == ["find", "verify"]
    await await_workflow(ctx, result)


@pytest.mark.asyncio
async def test_run_workflow_cancel_kills_the_orchestra(registry: SkillRegistry, tmp_path: Path):
    # A preset that blocks forever: cancelling the registry task must cancel the
    # in-flight orchestra run and deliver a cancelled completion.
    slow_dir = tmp_path / "slow-preset"
    write_preset(slow_dir, name="slow")
    (slow_dir / "slow" / "workflow.py").write_text("import asyncio\nawait asyncio.sleep(60)\nreturn 'never'\n")
    slow_registry = SkillRegistry()
    slow_registry.load([(slow_dir, "builtin")])

    events: list = []
    delivered: list = []
    ctx = make_ctx(slow_registry, events, delivered)
    execution = ToolExecution(tool_id="t1", tool_name="workflow", ctx=ctx)

    result = await run_workflow(execution, WorkflowInput(name="slow"))
    workflow_id = result.data["workflow_id"]
    task = ctx.background_tasks.task(workflow_id)
    assert task is not None

    await asyncio.sleep(0.01)  # let the orchestra reach its in-flight await
    assert ctx.background_tasks.cancel(workflow_id) is not None
    with pytest.raises(asyncio.CancelledError):
        await task

    finished = next(e for e in events if type(e).__name__ == "WorkflowFinishedEvent")
    assert finished.status == "cancelled"
    assert len(delivered) == 1
    assert 'status="cancelled"' in delivered[0]["content"]


@pytest.mark.asyncio
async def test_run_workflow_failure_delivers_failed_completion(registry: SkillRegistry, tmp_path: Path):
    broken_dir = tmp_path / "broken-preset"
    write_preset(broken_dir, name="broken")
    (broken_dir / "broken" / "workflow.py").write_text("raise RuntimeError('boom')\n")
    broken_registry = SkillRegistry()
    broken_registry.load([(broken_dir, "builtin")])

    events: list = []
    delivered: list = []
    ctx = make_ctx(broken_registry, events, delivered)
    execution = ToolExecution(tool_id="t1", tool_name="workflow", ctx=ctx)

    result = await run_workflow(execution, WorkflowInput(name="broken"))
    await await_workflow(ctx, result)

    finished = next(e for e in events if type(e).__name__ == "WorkflowFinishedEvent")
    assert finished.status == "failed"
    assert len(delivered) == 1
    assert 'status="failed"' in delivered[0]["content"]
    assert "failed during execution" in delivered[0]["content"]
