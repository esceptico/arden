import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

from coolname import generate_slug
from pydantic import BaseModel

from arden.agent import Agent, Result, Role, Usage
from arden.context.models import SessionState
from arden.core.factory import AgentConfig, create_agent
from arden.core.prompts import build_system_prompt
from arden.events.internal import RunCompleted
from arden.events.sse import AutomationProgressEvent, ToolCallResultEvent, ToolCallStartEvent, agent_event_to_sse
from arden.llm.models import supports_native_deferred_tools
from arden.memory.profile import resident_profile
from arden.observability import activate_tracing, observed_trace
from arden.server.bus import SessionBus
from arden.skills.registry import SkillRegistry
from arden.tools.core.context import ApprovalControls
from arden.tools.deferred import (
    build_deferred_tools_prompt_for_schemas,
    build_native_deferred_tools_prompt_for_schemas,
)
from arden.tools.directives import load_directives
from arden.tools.executor import ToolExecutor


@dataclass(frozen=True)
class OperatorDeps:
    executor: ToolExecutor
    config: AgentConfig
    source_details: dict[str, dict]
    create_session: Callable[[], SessionState]
    notifiers: list[dict[str, str]]
    enqueue_run_completed: Callable[[RunCompleted], Awaitable[bool]] | None = None
    skill_registry: SkillRegistry | None = None
    memory_records: object | None = None


@dataclass(frozen=True)
class RunRequest:
    prompt: str
    auto_approve: bool
    source_id: str
    prompt_suffix: str = ""
    model: str | None = None
    skip_approvals: bool = False
    automation_id: str | None = None
    # Decouples the toolset from the approval-flow concern. A caller can keep
    # approvals enabled while granting named additions to the read-only set.
    extra_tool_names: frozenset[str] = frozenset()
    # Allowlist patterns ('*', exact, 'slack_*') applied as the hard outer
    # gate over the registry. For detached runs, None means read-only; an
    # explicit scope is the only arbitrary write/execute capability grant.
    tool_scope: tuple[str, ...] | None = None
    # Pydantic schema for AI SDK-style structured output: the run ends with
    # one constrained completion whose validated dump rides RunResult.structured
    # and RunCompleted.structured_output.
    output_schema: type[BaseModel] | None = None


@dataclass(frozen=True)
class RunResult:
    run_id: str
    output: str | None
    usage: Usage
    structured: dict | None = None


async def _prepare(deps: OperatorDeps, request: RunRequest) -> tuple[Agent, list[dict], str, str]:
    run_id = generate_slug(2)
    session_state = deps.create_session()

    memory_context = await resident_profile(deps.memory_records)

    executor = deps.executor
    # Capability selection is independent from approval bypass. Detached runs
    # are read-only unless the caller supplies an explicit outer scope.
    scope_kw = {"scope": request.tool_scope} if request.tool_scope is not None else {}
    if request.extra_tool_names:
        tools = executor.get_tools(read_only=True, extra_names=request.extra_tool_names, **scope_kw)
    elif request.tool_scope is not None:
        tools = executor.get_tools(scope=request.tool_scope)
    else:
        tools = executor.get_tools(read_only=True)

    agent_config = deps.config
    if request.model:
        agent_config = replace(deps.config, model=request.model)

    native_deferred_tools = supports_native_deferred_tools(agent_config.model)
    deferred_tools_context = (
        (
            build_native_deferred_tools_prompt_for_schemas
            if native_deferred_tools
            else build_deferred_tools_prompt_for_schemas
        )(executor.registry, frozenset(executor.tool_services), tools)
        if agent_config.deferred_tools
        else None
    )
    skills_context = deps.skill_registry.to_prompt_xml() if deps.skill_registry else None

    system_prompt = build_system_prompt(
        source_details=deps.source_details,
        memory_context=memory_context,
        skills_context=skills_context,
        directives=load_directives(),
        notifiers=deps.notifiers or None,
        deferred_tools_context=deferred_tools_context,
        native_deferred_tools=native_deferred_tools,
    )
    system_prompt += request.prompt_suffix

    agent = create_agent(
        executor=executor,
        config=agent_config,
        tools=tools,
        session_state=session_state,
        run_id=run_id,
        approval_controls=ApprovalControls(skip_approvals=request.skip_approvals),
        output_schema=request.output_schema,
    )

    messages = [
        {"role": Role.SYSTEM, "content": system_prompt},
        {"role": Role.USER, "content": request.prompt},
    ]

    return agent, messages, run_id, session_state.session_id


async def _publish_completed(
    deps: OperatorDeps,
    run_id: str,
    session_id: str,
    messages: list,
    usage: Usage,
    result: str | None,
    structured_output: dict | None = None,
) -> None:
    event = RunCompleted(
        run_id=run_id,
        session_id=session_id,
        messages=tuple(messages),
        usage=usage,
        result=result,
        structured_output=structured_output,
    )
    if deps.enqueue_run_completed:
        await deps.enqueue_run_completed(event)


@observed_trace("automation", tags="automation")
async def _run_agent(agent: Agent, messages: list[dict], session_id: str, source_id: str) -> Result:
    activate_tracing(session_id, tags=["automation", source_id])
    return await agent.run(messages)


async def run_agent(deps: OperatorDeps, request: RunRequest) -> RunResult:
    agent, messages, run_id, session_id = await _prepare(deps, request)
    activate_tracing(session_id, tags=["automation", request.source_id])

    agent_result = await _run_agent(agent, messages, session_id, request.source_id)
    await _publish_completed(
        deps, run_id, session_id, messages, agent_result.usage, agent_result.text, agent_result.output
    )

    return RunResult(run_id=run_id, output=agent_result.text, usage=agent_result.usage, structured=agent_result.output)


@observed_trace("automation", tags="automation")
async def _consume_agent_stream(
    agent: Agent,
    messages: list[dict],
    bus: SessionBus,
    task_id: str,
    session_id: str,
    source_id: str,
) -> tuple[str | None, Usage, dict | None]:
    activate_tracing(session_id, tags=["automation", source_id])
    result_text: str | None = None
    usage = Usage()
    structured: dict | None = None
    gen = agent.stream(messages)
    try:
        async for item in gen:
            if isinstance(item, Result):
                result_text = item.text
                usage = item.usage
                structured = item.output
            else:
                sse = agent_event_to_sse(item)
                if isinstance(sse, ToolCallStartEvent):
                    label = sse.display_name or sse.tool_call_name
                    await bus.emit(AutomationProgressEvent(task_id=task_id, status=f"{label}..."))
                elif isinstance(sse, ToolCallResultEvent):
                    label = sse.display_name or sse.name
                    status = f"{label}: {sse.preview}" if sse.preview else label
                    await bus.emit(AutomationProgressEvent(task_id=task_id, status=status))
    except asyncio.CancelledError:
        pass
    return result_text, usage, structured


async def run_agent_streaming(
    deps: OperatorDeps,
    request: RunRequest,
    bus: SessionBus,
    task_id: str,
) -> RunResult:
    agent, messages, run_id, session_id = await _prepare(deps, request)
    activate_tracing(session_id, tags=["automation", request.source_id])

    result_text, usage, structured = await _consume_agent_stream(
        agent, messages, bus, task_id, session_id, request.source_id
    )

    await _publish_completed(deps, run_id, session_id, messages, usage, result_text, structured)

    return RunResult(run_id=run_id, output=result_text, usage=usage, structured=structured)
