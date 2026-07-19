"""Workflow presets — registry/service round-trip and the workflow tool's preset resolution."""

import ast
import textwrap
from datetime import UTC, datetime
from pathlib import Path

import pytest

import ntrp.skills.service as skill_service_module
from ntrp.context.models import SessionState
from ntrp.skills.registry import SkillRegistry
from ntrp.skills.service import BUILTIN_SKILLS_DIR, SkillService
from ntrp.tools.core.context import (
    BackgroundTaskRegistry,
    IOBridge,
    RunContext,
    ToolContext,
    ToolExecution,
)
from ntrp.tools.core.registry import ToolRegistry
from ntrp.tools.workflow import SaveWorkflowInput, WorkflowInput, run_save_workflow, run_workflow

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


@pytest.mark.asyncio
async def test_run_workflow_rejects_script_and_name_together(registry: SkillRegistry):
    ctx = make_ctx(registry, [])
    execution = ToolExecution(tool_id="t1", tool_name="workflow", ctx=ctx)

    result = await run_workflow(execution, WorkflowInput(name="echo", script="return 1"))

    assert result.is_error is True


@pytest.mark.asyncio
async def test_run_workflow_rejects_inline_python_without_executing_it(registry: SkillRegistry):
    called = False

    def mutate_process_state() -> None:
        nonlocal called
        called = True

    ctx = make_ctx(registry, [])
    execution = ToolExecution(tool_id="t1", tool_name="workflow", ctx=ctx)

    result = await run_workflow(
        execution,
        WorkflowInput(script="args['mutate']()\nreturn 'ran'", args={"mutate": mutate_process_state}),
    )

    assert result.is_error is True
    assert result.preview == "Inline scripts disabled"
    assert called is False


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
async def test_save_workflow_tool_rejects_python_without_persisting(registry: SkillRegistry):
    class UnexpectedService:
        def save_workflow(self, *args, **kwargs):
            raise AssertionError("untrusted workflow must not be persisted")

    ctx = make_ctx(registry, [])
    ctx.services["skill_service"] = UnexpectedService()
    execution = ToolExecution(tool_id="t1", tool_name="save_workflow", ctx=ctx)

    result = await run_save_workflow(
        execution,
        SaveWorkflowInput(name="unsafe", description="unsafe", script="return 1"),
    )

    assert result.is_error is True
    assert result.preview == "Python workflows disabled"


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


def test_save_workflow_round_trips_yaml_hostile_description(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(skill_service_module, "NTRP_DIR", tmp_path)
    registry = SkillRegistry()
    service = SkillService(registry)
    description = "Audit a target: find, verify — args: target."

    meta = service.save_workflow("rt-preset", description, "  \n" + PRESET_SCRIPT + "\n  ")

    assert meta.kind == "workflow"
    assert meta.description == description
    assert registry.load_workflow_script("rt-preset") == PRESET_SCRIPT + "\n"


def test_save_workflow_rejects_blank_script_and_duplicates(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(skill_service_module, "NTRP_DIR", tmp_path)
    registry = SkillRegistry()
    service = SkillService(registry)

    with pytest.raises(ValueError, match="non-empty script"):
        service.save_workflow("blank", "desc", "   \n")

    service.save_workflow("dup", "desc", PRESET_SCRIPT)
    with pytest.raises(ValueError, match="already exists"):
        service.save_workflow("dup", "desc", PRESET_SCRIPT)
