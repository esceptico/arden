import asyncio
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from arden.core.agent_types import SPAWN_SURFACE_GUIDANCE
from arden.events.sse import WorkflowFinishedEvent, WorkflowStartedEvent
from arden.orchestra.dynamic import run_script
from arden.orchestra.engine import Orchestra
from arden.tools.core import ToolResult, tool
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import ToolAction, ToolPolicy, ToolScope


class WorkflowInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(
        default=None,
        description="Name of a curated built-in workflow preset to run (e.g. 'audit').",
    )
    title: str | None = Field(
        default=None,
        description="Short label for this run, shown in the UI. Defaults to the preset name when running by `name`.",
    )
    phases: list[str] = Field(
        default_factory=list,
        max_length=50,
        description="Optional phase labels rendered before the preset starts.",
    )
    args: dict = Field(default_factory=dict, max_length=100, description="Parameters passed to the curated preset.")


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _render_lines(value: Any, indent: int = 0) -> list[str]:
    value = _jsonable(value)
    prefix = "  " * indent
    if isinstance(value, dict):
        if not value:
            return [f"{prefix}(empty object)"]
        lines: list[str] = []
        for key in sorted(value, key=str):
            child = value[key]
            if isinstance(child, (dict, list, tuple)):
                lines.append(f"{prefix}{key}:")
                lines.extend(_render_lines(child, indent + 1))
            else:
                lines.append(f"{prefix}{key}: {child}")
        return lines
    if isinstance(value, (list, tuple)):
        if not value:
            return [f"{prefix}(empty list)"]
        lines = []
        for child in value:
            if isinstance(child, (dict, list, tuple)):
                lines.append(f"{prefix}-")
                lines.extend(_render_lines(child, indent + 1))
            else:
                lines.append(f"{prefix}- {child}")
        return lines
    return [f"{prefix}{value}"]


def _render(result: Any) -> str:
    return "\n".join(_render_lines(result))


async def run_workflow(execution: ToolExecution, args: WorkflowInput) -> ToolResult:
    ctx = execution.ctx
    if ctx.spawn_fn is None:
        return ToolResult.failure(
            code="not_configured",
            message="Workflow spawn capability is unavailable.",
            preview="Workflow unavailable",
            recovery_action="Run the workflow from a session with agent spawning enabled.",
        )

    # Only named, curated presets may reach the trusted in-process runner.
    script = None
    description = None
    if args.name:
        registry = ctx.services.get("skill_registry")
        if registry is None:
            return ToolResult.failure(
                code="not_configured",
                message="Workflow registry is unavailable.",
                preview="Unavailable",
                recovery_action="Enable the skill registry before running a named workflow.",
            )
        meta = registry.get(args.name)
        if meta is not None and meta.kind == "workflow" and meta.location != "builtin":
            return ToolResult.failure(
                code="permission_denied",
                message="User-authored Python workflow presets are disabled.",
                preview="Untrusted workflow",
                recovery_action="Run a curated built-in workflow preset.",
            )
        script = registry.load_workflow_script(args.name)
        if script is not None:
            description = meta.description
        else:
            presets = ", ".join(m.name for m in registry.list_all() if m.kind == "workflow" and m.location == "builtin")
            return ToolResult.failure(
                code="not_found",
                message=f"No workflow preset named '{args.name}'. Built-in presets: {presets or '(none)'}.",
                preview="Unknown preset",
                recovery_action="Retry with an exact listed built-in preset name.",
            )
    if not script:
        return ToolResult.failure(
            code="invalid_arguments",
            message="Pass the name of a curated built-in workflow preset.",
            preview="No preset",
            recovery_action="Choose a built-in preset name from the workflow registry.",
        )

    workflow_id = f"wf-{uuid4().hex[:10]}"
    title = args.title or args.name or "workflow"
    emit = ctx.io.emit
    if emit:
        await emit(
            WorkflowStartedEvent(
                session_id=ctx.session_state.session_id,
                run_id=ctx.run.run_id,
                workflow_id=workflow_id,
                parent_tool_call_id=execution.tool_id,
                name=title,
                description=description or "",
                phases=args.phases,
            )
        )

    orchestra = Orchestra.for_ctx(ctx, parent_id=execution.tool_id, workflow_id=workflow_id, name=title)

    async def _finish(status: str, summary: str) -> None:
        if emit:
            await emit(
                WorkflowFinishedEvent(
                    session_id=ctx.session_state.session_id,
                    run_id=ctx.run.run_id,
                    workflow_id=workflow_id,
                    status=status,
                    summary=summary,
                    agent_count=orchestra.spawn_count,
                )
            )

    try:
        result = await run_script(orchestra, script, args.args)
    except asyncio.CancelledError:
        # User stopped the run. CancelledError is a BaseException, so without this
        # it would skip both excepts below and never settle the workflow row —
        # leaving it "running" with a free-running clock. Shield the settle so a
        # second cancellation mid-emit can't drop the terminal event, then re-raise
        # so the tool executor still sees the cancellation.
        await asyncio.shield(_finish("cancelled", "stopped by user"))
        raise
    except SyntaxError:
        await _finish("failed", "script did not compile")
        return ToolResult.failure(
            code="workflow_invalid",
            message="The curated workflow preset did not compile.",
            preview="Script error",
            recovery_action="Report the broken built-in preset; do not retry it unchanged.",
        )
    except Exception:
        await _finish("failed", "workflow execution failed")
        return ToolResult.failure(
            code="workflow_failed",
            message="The curated workflow preset failed during execution.",
            preview="Workflow failed",
            recovery_action="Inspect the workflow run trace or report the broken preset; do not retry blindly.",
        )

    await _finish("completed", "")
    return ToolResult(
        content=_render(result),
        preview=f"Ran workflow: {title}",
        data={"workflow": title, "workflow_id": workflow_id},
    )


WORKFLOW_DESCRIPTION = (
    """\
Run a curated built-in multi-agent workflow by `name`, passing its parameters through `args`.
Available presets include `audit`, `investigate`, `panel`, and `implement`. Example:
workflow(name="audit", args={"target": "apps/server", "depth": "normal"}).
Inline and user-authored Python workflows are disabled. """
    + SPAWN_SURFACE_GUIDANCE
)

workflow_tool = tool(
    display_name="Workflow",
    description=WORKFLOW_DESCRIPTION,
    input_model=WorkflowInput,
    policy=ToolPolicy(action=ToolAction.EXECUTE, scope=ToolScope.INTERNAL, permissions=frozenset({"skill_registry"})),
    execute=run_workflow,
    # "workflow" (not "agent") so the desktop renders it as a workflow card from
    # the tool call itself — independent of the streamed workflow-domain events.
    kind="workflow",
)
