import asyncio
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from ntrp.events.sse import WorkflowFinishedEvent, WorkflowStartedEvent
from ntrp.orchestra.dynamic import format_script_traceback, run_script
from ntrp.orchestra.engine import Orchestra
from ntrp.tools.core import ToolResult, tool
from ntrp.tools.core.context import ToolExecution
from ntrp.tools.core.types import ToolAction, ToolPolicy, ToolScope


class WorkflowInput(BaseModel):
    script: str | None = Field(
        default=None,
        description="Deprecated. Inline Python workflows are disabled; run a curated preset by `name`.",
    )
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
        description="Optional phase labels rendered before the preset starts.",
    )
    args: dict = Field(default_factory=dict, description="Parameters passed to the curated preset.")


class SaveWorkflowInput(BaseModel):
    name: str = Field(description="Preset name: lowercase letters/digits/hyphens, starts with a letter (e.g. 'memory-audit').")
    description: str = Field(description="One line — when to use this preset and what `args` it expects.")
    script: str = Field(description="Deprecated executable Python workflow source.")


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
        return ToolResult(content="Error: spawn capability not available", preview="No spawn", is_error=True)

    if args.script is not None:
        return ToolResult(
            content="Inline Python workflows are disabled. Run a curated workflow preset by name.",
            preview="Inline scripts disabled",
            is_error=True,
        )

    # Only named, curated presets may reach the trusted in-process runner.
    script = None
    description = None
    if args.name:
        registry = ctx.services.get("skill_registry")
        if registry is None:
            return ToolResult(content="Error: skill registry not available.", preview="Unavailable", is_error=True)
        meta = registry.get(args.name)
        if meta is not None and meta.kind == "workflow" and meta.location != "builtin":
            return ToolResult(
                content="User-authored Python workflow presets are disabled. Run a curated built-in preset.",
                preview="Untrusted workflow",
                is_error=True,
            )
        script = registry.load_workflow_script(args.name)
        if script is not None:
            description = meta.description
        else:
            presets = ", ".join(
                m.name for m in registry.list_all() if m.kind == "workflow" and m.location == "builtin"
            )
            return ToolResult(
                content=f"No workflow preset named '{args.name}'. Saved presets: {presets or '(none)'}.",
                preview="Unknown preset",
                is_error=True,
            )
    if not script:
        return ToolResult(
            content="Pass the name of a curated built-in workflow preset.",
            preview="No preset",
            is_error=True,
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
    except SyntaxError as exc:
        await _finish("failed", f"script did not compile: {exc}")
        # Self-correcting: hand back the exact compile error so the model can fix + retry.
        return ToolResult(
            content=f"Script did not compile: {exc}\nFix the Python and call the tool again.",
            preview="Script error",
            is_error=True,
        )
    except Exception as exc:
        await _finish("failed", str(exc)[:200])
        # Trimmed to the script's own frames with rebased line numbers, so the
        # model sees the line it wrote — the whole point of a self-correcting tool.
        tb = format_script_traceback(exc, script)
        return ToolResult(
            content=f"Workflow raised {type(exc).__name__}: {exc}\n\n{tb}\nFix the script and call again.",
            preview="Workflow failed",
            is_error=True,
        )

    await _finish("completed", "")
    return ToolResult(
        content=_render(result),
        preview=f"Ran workflow: {title}",
        data={"workflow": title, "workflow_id": workflow_id},
    )


WORKFLOW_DESCRIPTION = """\
Run a curated built-in multi-agent workflow by `name`, passing its parameters through `args`.
Available presets include `audit`, `investigate`, `panel`, and `implement`. Example:
workflow(name="audit", args={"target": "apps/server", "depth": "normal"}).
Inline and user-authored Python workflows are disabled."""

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


async def run_save_workflow(execution: ToolExecution, args: SaveWorkflowInput) -> ToolResult:
    return ToolResult(
        content="Saving executable Python workflows is disabled. Use a reviewed built-in workflow preset.",
        preview="Python workflows disabled",
        is_error=True,
    )


save_workflow_tool = tool(
    display_name="Save Workflow",
    description="Executable Python workflow presets are disabled; this compatibility tool returns an error.",
    input_model=SaveWorkflowInput,
    policy=ToolPolicy(action=ToolAction.WRITE, scope=ToolScope.INTERNAL, permissions=frozenset({"skill_service"})),
    execute=run_save_workflow,
)
