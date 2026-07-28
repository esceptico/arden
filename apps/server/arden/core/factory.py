from dataclasses import dataclass
from time import monotonic
from typing import Self

from pydantic import BaseModel

from arden.agent import Agent, AgentHooks, RunBudget
from arden.agent.ledger import SharedLedger
from arden.context.models import AreaContext, SessionState
from arden.core.compaction_model_request_middleware import CompactionModelRequestMiddleware
from arden.core.compactor import Compactor, SummaryCompactor
from arden.core.deferred_tools_middleware import DeferredToolsModelRequestMiddleware
from arden.core.llm_client import llm_client
from arden.core.model_context_budget import ToolResultContextBudgetMiddleware
from arden.core.spawner import create_spawn_fn
from arden.core.tool_executor import ArdenToolExecutor
from arden.core.usage_tracker import UsageTracker
from arden.llm.models import get_model
from arden.server.state import RunRegistry
from arden.tools.core.context import (
    ApprovalControls,
    BackgroundTaskRegistry,
    ChildIOFactory,
    IOBridge,
    RunContext,
    ToolContext,
)
from arden.tools.deferred import tool_schema_names
from arden.tools.executor import ToolExecutor


@dataclass(frozen=True)
class AgentConfig:
    model: str
    research_model: str | None
    max_depth: int
    workflow_model: str | None = None
    max_iterations: int | None = None
    max_tool_calls: int | None = None
    max_wall_time_seconds: float | None = None
    max_cost: float | None = None
    max_output_tokens: int | None = None
    reasoning_effort: str | None = None
    model_reasoning_efforts: dict[str, str] | None = None
    research_reasoning_effort: str | None = None
    workflow_reasoning_effort: str | None = None
    deferred_tools: bool = True
    approval_timeout_seconds: int = 300
    compactor: Compactor | None = None

    @classmethod
    def from_config(cls, config, *, model: str | None = None) -> Self:
        return cls(
            model=model or config.chat_model,
            research_model=config.research_model,
            workflow_model=config.workflow_model,
            max_depth=config.max_depth,
            max_iterations=config.agent_max_iterations,
            max_tool_calls=config.agent_max_tool_calls,
            max_wall_time_seconds=config.agent_max_wall_time_seconds,
            max_cost=config.agent_max_cost,
            max_output_tokens=config.agent_max_output_tokens,
            reasoning_effort=config.reasoning_effort_for(model or config.chat_model),
            model_reasoning_efforts=dict(config.model_reasoning_efforts),
            research_reasoning_effort=config.role_setup("research").reasoning_effort,
            workflow_reasoning_effort=config.role_setup("workflow").reasoning_effort,
            deferred_tools=config.deferred_tools,
            approval_timeout_seconds=config.approval_timeout_seconds,
            compactor=SummaryCompactor(
                threshold=config.compression_threshold,
                max_messages=config.max_messages,
                keep_ratio=config.compression_keep_ratio,
                summary_max_tokens=config.summary_max_tokens,
            ),
        )


def create_agent(
    *,
    executor: ToolExecutor,
    config: AgentConfig,
    tools: list[dict],
    session_state: SessionState,
    run_id: str,
    io: IOBridge | None = None,
    approval_controls: ApprovalControls | None = None,
    extra_auto_approve: set[str] | None = None,
    background_tasks: BackgroundTaskRegistry | None = None,
    loaded_tools: set[str] | None = None,
    loop_task_id: str | None = None,
    automation_id: str | None = None,
    parent_tracker: UsageTracker | None = None,
    initial_input_tokens: int | None = None,
    run_registry: "RunRegistry | None" = None,
    area_context: AreaContext | None = None,
    token_budget: int | None = None,
    child_io_factory: ChildIOFactory | None = None,
    output_schema: type[BaseModel] | None = None,
) -> Agent:
    started_at = monotonic()
    # Per-turn directive ("+500k") overrides the configured default ceiling.
    budget = RunBudget(total=token_budget if token_budget is not None else config.max_output_tokens)
    run_ctx = RunContext(
        run_id=run_id,
        # Wire the child-session factory onto the RunContext the SPAWNER actually
        # reads (calling_ctx.run). Without it child_io never engages → FULL
        # subagents run foreground (tools leak to the parent, child sessions stay
        # empty). The spawner propagates this to every descendant run.
        child_io_factory=child_io_factory,
        max_depth=config.max_depth,
        max_iterations=config.max_iterations,
        max_tool_calls=config.max_tool_calls,
        max_wall_time_seconds=config.max_wall_time_seconds,
        max_cost=config.max_cost,
        started_at=started_at,
        extra_auto_approve=extra_auto_approve or set(),
        approval_controls=approval_controls or ApprovalControls(),
        research_model=config.research_model,
        workflow_model=config.workflow_model,
        research_reasoning_effort=config.research_reasoning_effort,
        workflow_reasoning_effort=config.workflow_reasoning_effort,
        deferred_tools_enabled=config.deferred_tools,
        loaded_tools=loaded_tools if loaded_tools is not None else set(),
        allowed_tool_names=tool_schema_names(tools),
        loop_task_id=loop_task_id,
        automation_id=automation_id,
        budget=budget,
    )

    bg_tasks = background_tasks or BackgroundTaskRegistry(session_id=session_state.session_id)

    tool_ctx = ToolContext(
        session_state=session_state,
        registry=executor.registry,
        run=run_ctx,
        io=io or IOBridge(),
        services=executor.tool_services,
        area=area_context,
        ledger=SharedLedger(),
        background_tasks=bg_tasks,
        run_registry=run_registry,
        parent_tracker=parent_tracker,
    )
    tool_ctx.spawn_fn = create_spawn_fn(
        executor=executor,
        model=config.model,
        max_depth=config.max_depth,
        current_depth=0,
        reasoning_effort=config.reasoning_effort,
        model_reasoning_efforts=config.model_reasoning_efforts or {},
        compactor=config.compactor,
        max_iterations=config.max_iterations,
        max_tool_calls=config.max_tool_calls,
        max_wall_time_seconds=config.max_wall_time_seconds,
        max_cost=config.max_cost,
        started_at=started_at,
        budget=budget,
    )

    arden_executor = ArdenToolExecutor(executor, tool_ctx, ledger=tool_ctx.ledger)

    return Agent(
        tools=tools,
        client=llm_client,
        executor=arden_executor,
        model=config.model,
        max_iterations=config.max_iterations,
        max_tool_calls=config.max_tool_calls,
        max_wall_time_seconds=config.max_wall_time_seconds,
        max_cost=config.max_cost,
        max_depth=config.max_depth,
        reasoning_effort=config.reasoning_effort,
        prompt_cache_key=session_state.session_id,
        hooks=AgentHooks(),
        model_request_middlewares=(
            DeferredToolsModelRequestMiddleware(
                registry=executor.registry,
                run=run_ctx,
                get_services=lambda: executor.tool_services,
            ),
            ToolResultContextBudgetMiddleware(),
            CompactionModelRequestMiddleware(
                compactor=config.compactor,
                on_compact=run_ctx.loaded_tools.clear,
                get_rehydration_state=tool_ctx.to_rehydration_state,
                apply_rehydration_state=run_ctx.apply_rehydration_state,
                emit=tool_ctx.io.emit,
                run_id=run_id,
                initial_input_tokens=initial_input_tokens,
            ),
        ),
        cost_calculator=_response_cost,
        cost_getter=(lambda: parent_tracker.cost) if parent_tracker is not None else None,
        started_at=started_at,
        budget=budget,
        output_schema=output_schema,
    )


def _response_cost(response) -> float:
    try:
        return get_model(response.model).pricing.cost(response.usage)
    except ValueError:
        return 0.0
