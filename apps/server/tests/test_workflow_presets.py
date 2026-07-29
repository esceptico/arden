"""Workflow presets — registry/service round-trip and the workflow tool's preset resolution."""

import ast
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


def make_ctx(registry: SkillRegistry, events: list) -> ToolContext:
    async def emit(event) -> None:
        events.append(event)

    async def spawn_fn(*args, **kwargs):  # pragma: no cover - presets under test never spawn
        raise AssertionError("spawn_fn should not be called")

    return ToolContext(
        session_state=SessionState(session_id="s1", started_at=datetime.now(UTC)),
        registry=ToolRegistry(),
        run=RunContext(run_id="r1"),
        io=IOBridge(emit=emit),
        services={"skill_registry": registry},
        spawn_fn=spawn_fn,
        background_tasks=BackgroundTaskRegistry(session_id="s1"),
    )


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
    ctx = make_ctx(registry, events)
    execution = ToolExecution(tool_id="t1", tool_name="workflow", ctx=ctx)

    result = await run_workflow(execution, WorkflowInput(name="echo", args={"x": "hi"}))

    assert result.is_error is None or result.is_error is False
    assert result.content == "hi"
    started = next(e for e in events if type(e).__name__ == "WorkflowStartedEvent")
    assert started.name == "echo"
    assert started.description == PRESET_DESCRIPTION
    finished = next(e for e in events if type(e).__name__ == "WorkflowFinishedEvent")
    assert finished.status == "completed"


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
