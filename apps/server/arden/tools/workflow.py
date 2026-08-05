import asyncio
import hashlib
from typing import Annotated, Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints, ValidationError

from arden.context.models import BackgroundStartDisposition
from arden.core.agent_types import SPAWN_SURFACE_GUIDANCE
from arden.core.public_refs import public_ref
from arden.events.sse import WorkflowFinishedEvent, WorkflowStartedEvent
from arden.logging import get_logger
from arden.orchestra.dynamic import run_script
from arden.orchestra.engine import Orchestra
from arden.orchestra.journal import WorkflowJournal
from arden.tools.core import ToolResult, tool
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import ApprovalInfo, ToolAction, ToolPolicy, ToolScope

_logger = get_logger(__name__)


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


NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class _WorkflowArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _InvestigateArgs(_WorkflowArgs):
    target: NonEmpty = "."
    question: NonEmpty
    breadth: Literal["focused", "normal", "wide"] = "normal"
    reader_model: NonEmpty | None = None
    synth_model: NonEmpty | None = None


class _AuditArgs(_WorkflowArgs):
    target: NonEmpty = "."
    dimensions: list[NonEmpty] | None = Field(default=None, min_length=1, max_length=8)
    depth: Literal["quick", "normal", "deep"] = "normal"
    finder_model: NonEmpty | None = None
    synth_model: NonEmpty | None = None


class _PanelArgs(_WorkflowArgs):
    question: NonEmpty
    n: StrictInt = Field(default=3, ge=1, le=5)
    criteria: list[NonEmpty] | None = Field(default=None, min_length=1, max_length=10)
    gen_model: NonEmpty | None = None
    synth_model: NonEmpty | None = None


class _ImplementArgs(_WorkflowArgs):
    spec: NonEmpty
    target: NonEmpty = "."
    depth: Literal["quick", "normal", "deep"] = "normal"
    recon_model: NonEmpty | None = None
    build_model: NonEmpty | None = None


_BUILTIN_WORKFLOW_ARGS: dict[str, type[_WorkflowArgs]] = {
    "audit": _AuditArgs,
    "implement": _ImplementArgs,
    "investigate": _InvestigateArgs,
    "panel": _PanelArgs,
}


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


async def approve_workflow(_execution: ToolExecution, args: WorkflowInput) -> ApprovalInfo:
    name = args.name or "workflow"
    return ApprovalInfo(
        description=f"Run multi-agent workflow {name!r}",
        preview=_render(args.args),
        diff=None,
    )


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

    validator = _BUILTIN_WORKFLOW_ARGS.get(args.name or "")
    if validator is not None:
        try:
            validated_args = validator.model_validate(args.args).model_dump(exclude_none=True)
        except ValidationError as error:
            return ToolResult.failure(
                code="invalid_arguments",
                message=f"Invalid arguments for workflow {args.name!r}: {error}",
                preview="Invalid workflow arguments",
                recovery_action=f"Retry {args.name!r} with the required typed arguments.",
            )
        args = args.model_copy(update={"args": validated_args})

    workflow_id = f"wf-{uuid4().hex[:10]}"
    title = args.title or args.name or "workflow"
    agent_ref = public_ref(title, workflow_id, empty_slug="workflow")
    label = f"Workflow: {title}"
    bg_registry = ctx.background_tasks
    # Uncapped, like awaited spawns: a workflow is ONE curated pipeline bounded
    # by the Orchestra global semaphore; the detached-agent cap exists to bound
    # runaway background() fan-out, not curated presets. workflow_id is
    # uuid-fresh, so reserve can only fail on an impossible duplicate.
    if not bg_registry.reserve(
        workflow_id,
        command=label,
        limit=None,
        agent_ref=agent_ref,
        parent_run_id=ctx.run.run_id,
    ):
        return ToolResult.failure(
            code="conflict",
            message="Workflow could not be registered.",
            preview="Workflow conflict",
            recovery_action="Retry the workflow call.",
        )
    try:
        # Durable roster row up front: deliver_result's completion claim needs
        # it, and the sidebar can show the running workflow like any agent. No
        # spawn_spec — a workflow is not respawned from the outbox on restart.
        start_disposition = await bg_registry.record_started(
            task_id=workflow_id,
            agent_ref=agent_ref,
            command=label,
            parent_run_id=ctx.run.run_id,
            parent_tool_call_id=execution.tool_id,
            agent_type="workflow",
            wait=False,
        )
    except BaseException:
        bg_registry.release(workflow_id)
        raise
    if start_disposition is BackgroundStartDisposition.CANCELLED:
        bg_registry.release(workflow_id)
        return ToolResult.failure(
            code="parent_cancelled",
            message="Workflow was not started because its owning agent is already cancelled.",
            preview="Workflow cancelled",
            recovery_action="Do not retry from the cancelled agent session.",
        )
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

    # workflow_id above is uuid-fresh per execution (UI identity only). The
    # durable identity of a workflow EXECUTION is its parent tool call: a
    # restarted run re-executes the same tool_call_id, so the journal is keyed
    # on it (plus the session for uniqueness and the preset source, so a
    # changed script falls back to a fresh run instead of replaying stale
    # results). Replay is implicitly on: a prefix journaled under this key is
    # served until the first miss; a fresh execution just misses at seq 1.
    journal = None
    invocations = ctx.services.get("invocations")
    if invocations is not None:
        script_sha = hashlib.sha256(script.encode("utf-8")).hexdigest()[:12]
        journal = WorkflowJournal(
            invocations,
            key=f"{ctx.session_state.session_id}:{execution.tool_id}:{script_sha}",
            run_id=ctx.run.run_id,
            session_id=ctx.session_state.session_id,
            tool_call_id=execution.tool_id,
        )
    orchestra = Orchestra.for_ctx(
        ctx, parent_id=execution.tool_id, workflow_id=workflow_id, name=title, journal=journal
    )

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

    async def _deliver(status: str, result_text: str) -> None:
        try:
            await bg_registry.deliver_result(
                task_id=workflow_id,
                agent_ref=agent_ref,
                result=result_text,
                label=label,
                status=status,
                emit=emit,
                parent_tool_call_id=execution.tool_id,
                agent_type="workflow",
                wait=False,
            )
        except Exception:
            _logger.exception("Workflow %s result delivery failed", workflow_id)

    async def _run_detached() -> None:
        # The whole orchestra runs inside this registry task: cancelling it
        # (registry.cancel / the cancel cascade) cancels run_script, whose
        # TaskGroup tears down every in-flight awaited worker spawn.
        try:
            result = await run_script(orchestra, script, args.args)
        except asyncio.CancelledError:
            # Shield the settle + delivery so a second cancellation mid-emit
            # can't drop the terminal event, then re-raise so the registry task
            # settles as cancelled.
            await asyncio.shield(_finish("cancelled", "stopped by user"))
            await asyncio.shield(_deliver("cancelled", f"Workflow '{title}' was cancelled before finishing."))
            raise
        except SyntaxError:
            await _finish("failed", "script did not compile")
            await _deliver(
                "failed",
                f"Workflow '{title}' failed: the curated preset did not compile. "
                "Report the broken built-in preset; do not retry it unchanged.",
            )
            return
        except Exception:
            _logger.exception("Workflow %s failed during execution", workflow_id)
            await _finish("failed", "workflow execution failed")
            await _deliver(
                "failed",
                f"Workflow '{title}' failed during execution. "
                "Inspect the workflow run trace or report the broken preset; do not retry blindly.",
            )
            return
        await _finish("completed", "")
        await _deliver("completed", _render(result))

    task = asyncio.create_task(_run_detached())
    bg_registry.register(workflow_id, task, command=label)

    content = (
        f"Started workflow '{title}'. agent_ref: {agent_ref}. It runs detached; progress appears on its card, "
        "and its final result is delivered here automatically. Do not poll. "
        f'To stop it, call agent_cancel(agent_ref="{agent_ref}"); otherwise continue or end your turn.'
    )
    return ToolResult(
        content=content,
        preview=f"Workflow started: {title}",
        # Tool history persists only allowlisted result metadata; these fields
        # remain UI/SSE state while content carries the model-facing address.
        data={"workflow": title, "workflow_id": workflow_id, "agent_ref": agent_ref},
    )


WORKFLOW_DESCRIPTION = (
    """\
Run a curated built-in multi-agent workflow by `name`, passing its parameters through `args`.
Available presets include `audit`, `investigate`, `panel`, and `implement`. Example:
workflow(name="audit", args={"target": "apps/server", "depth": "normal"}).
The workflow runs detached: this call returns a receipt immediately, progress streams to the
workflow card, and the final result is delivered to this conversation automatically — do not
poll or wait for it. Inline and user-authored Python workflows are disabled. """
    + SPAWN_SURFACE_GUIDANCE
)

workflow_tool = tool(
    display_name="Workflow",
    display_description="Run a built-in multi-agent workflow.",
    description=WORKFLOW_DESCRIPTION,
    input_model=WorkflowInput,
    policy=ToolPolicy(
        action=ToolAction.EXECUTE,
        scope=ToolScope.INTERNAL,
        permissions=frozenset({"skill_registry"}),
        concurrency_group="workflow_agents",
        requires_approval=True,
        deferred=True,
        destructive=True,
        open_world=True,
        idempotent=False,
    ),
    approval=approve_workflow,
    execute=run_workflow,
    # "workflow" (not "agent") so the desktop renders it as a workflow card from
    # the tool call itself — independent of the streamed workflow-domain events.
    kind="workflow",
)
