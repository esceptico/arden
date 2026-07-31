import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from arden.constants import AGENT_MAX_CONCURRENT
from arden.core.agent_types import resolve_agent_type
from arden.core.isolation import IsolationLevel
from arden.core.llm_client import llm_client
from arden.core.prompts import UNTRUSTED_DATA_RULE
from arden.logging import get_logger
from arden.observability import observed_trace
from arden.orchestra.schema import model_from_schema
from arden.tools.core.scope import ToolFilter

_logger = get_logger(__name__)

# Global across all Orchestra instances so the cap holds under nested/concurrent
# workflows — a per-instance semaphore would let depth D run up to N*D agents.
_GLOBAL_SEM = asyncio.Semaphore(AGENT_MAX_CONCURRENT)

# Runaway guard for dynamic/looping scripts: one workflow can't spawn more than
# this many agents. (A pure-Python loop with no spawns is still bounded by the
# run's wall-time/cost budget and the tool's caller.)
_MAX_WORKFLOW_SPAWNS = 200

# Workflow workers are leaves: deny them the spawn tools so a workflow can't
# re-enter itself or fan out uncontrolled subagents (least privilege + bounds
# recursion). A workflow that needs delegation expresses it as another phase.
_WORKFLOW_EXCLUDE_TOOLS = frozenset({"workflow", "research", "background"})

WORKFLOW_AGENT_PROMPT = (
    "You are a focused worker agent inside a deterministic workflow. "
    "Do exactly the task you are given, using tools as needed, then return a "
    "concise final answer. " + UNTRUSTED_DATA_RULE
)

_FORMATTER_PROMPT = (
    "Convert the provided worker answer into the requested structured result. "
    "Preserve the worker's facts. Do not invent missing fields. " + UNTRUSTED_DATA_RULE
)

Thunk = Callable[[], Awaitable[Any]]
# A parallel unit may be a bare awaitable (e.g. agent("x")) or a thunk that
# returns one (() => agent("x")). Accepting both keeps dynamic scripts simple —
# `parallel([agent(a), agent(b)])` reads naturally, no lambdas required.
Unit = Awaitable[Any] | Thunk
Stage = Callable[[Any, Any, int], Awaitable[Any]]


class WorkflowSpawnLimit(RuntimeError):
    """Raised when the per-run spawn cap is hit. Distinct so _safe re-raises it
    instead of degrading the runaway guard into a silent None."""


class WorkflowBudgetExceeded(RuntimeError):
    """Raised when the run's output-token ceiling is hit before a spawn. Like
    WorkflowSpawnLimit, _safe re-raises it so a fan-out aborts instead of
    silently degrading to None."""


class WorkflowStructuredOutputMissing(RuntimeError):
    """Raised when a schema formatter returns an invalid structured response."""


class WorkflowStructuredFormatError(RuntimeError):
    """Raised when the formatter LLM fails before returning a response."""


class TokenBudget:
    """Read-through view of the run's shared RunBudget, handed to dynamic scripts
    as `budget`. `spent()` re-reads the live shared counter on every call, so a
    script can scale fan-out to what's left: `while budget.total and
    budget.remaining() > 50_000: ...`."""

    def __init__(self, budget: Any):
        self._b = budget

    @property
    def total(self) -> int | None:
        return None if self._b is None else self._b.total

    def spent(self) -> int:
        return 0 if self._b is None else self._b.output_tokens

    def remaining(self) -> float:
        if self._b is None or self._b.total is None:
            return float("inf")
        return max(0, self._b.total - self._b.output_tokens)


async def _safe(unit: Unit) -> Any:
    try:
        return await (unit() if callable(unit) else unit)
    except (WorkflowSpawnLimit, WorkflowBudgetExceeded, WorkflowStructuredOutputMissing, WorkflowStructuredFormatError):
        # Resource guards + the structured-output contract must abort the whole
        # fan-out, not be swallowed into a None the model reads as a partial fail.
        raise
    except Exception as exc:
        _logger.warning("workflow unit failed: %s", exc)
        return None


class Orchestra:
    """Deterministic subagent orchestration over ctx.spawn_fn.

    Combinators mirror the Workflow engine: agent() spawns one subagent and,
    when given a schema, returns a validated formatter result; parallel() fans
    out with a barrier; pipeline() runs per-item stage chains with no barrier. Failed units
    degrade to None — asyncio.TaskGroup cancels siblings on the first exception,
    so every unit is wrapped to swallow errors and keep the rest alive.
    """

    def __init__(self, ctx: Any, parent_id: str | None = None, workflow_id: str | None = None, name: str | None = None):
        self.ctx = ctx
        self.parent_id = parent_id
        self.workflow_id = workflow_id
        self.name = name
        self.spawn_count = 0
        self._phase: str | None = None
        # The run's shared RunBudget (same instance as the parent agent + every
        # spawned child), so spent() reflects the whole turn. None when the ctx
        # has no run (test stubs that don't exercise budgeting).
        run = getattr(ctx, "run", None)
        self._budget = getattr(run, "budget", None)
        self.budget_view = TokenBudget(self._budget)
        # Default model for workflow agents (config.workflow_model, falls back
        # to the chat model server-side). A script's explicit agent(model=...)
        # still wins.
        self._default_model = getattr(run, "workflow_model", None)
        # And the effort that role is configured at, when the user set one —
        # otherwise the child falls back to whatever the model is mapped to.
        self._default_reasoning_effort = getattr(run, "workflow_reasoning_effort", None)

    @classmethod
    def for_ctx(
        cls, ctx: Any, parent_id: str | None = None, workflow_id: str | None = None, name: str | None = None
    ) -> "Orchestra":
        return cls(ctx=ctx, parent_id=parent_id, workflow_id=workflow_id, name=name)

    def phase(self, title: str) -> None:
        self._phase = title

    def log(self, message: str) -> None:
        _logger.info("[workflow] %s", message)

    async def agent(
        self,
        task: str,
        *,
        schema: Any = None,
        tools: list[dict] | list[str] | None = None,
        model: str | None = None,
        system_prompt: str | None = None,
        agent_type: str | None = None,
        phase: str | None = None,
    ) -> Any:
        active_phase = phase or self._phase
        # agent_type resolves a shared AgentType: a tool profile (capability +
        # excludes + extra tools) and a persona prompt. An explicit system_prompt /
        # tools the script passes still win over the persona's.
        spec = resolve_agent_type(agent_type) if agent_type else None
        prompt = system_prompt or (spec.prompt if spec else None) or WORKFLOW_AGENT_PROMPT
        scope = spec.scope if spec else None
        type_exclude = spec.exclude if spec else frozenset()
        type_extra = dict(spec.extra_tools) if spec else {}
        label = agent_type or active_phase

        if schema is None:
            return await self._spawn(
                task,
                tools,
                model,
                prompt,
                active_phase,
                agent_type_label=label,
                scope=scope,
                type_exclude=type_exclude,
                extra_tools=type_extra or None,
            )

        # Structured output is a two-step contract: the worker can use normal
        # tools and return prose; a separate formatter pass uses provider-native
        # response_format. This keeps workflow workers from seeing a fake
        # structured_output tool that can conflict with tool filtering.
        out_model = model_from_schema(schema)
        worker_answer = await self._spawn(
            task,
            tools,
            model,
            prompt,
            active_phase,
            agent_type_label=label,
            scope=scope,
            type_exclude=type_exclude,
            extra_tools=type_extra or None,
        )
        formatted = await self._format_structured(task, worker_answer, out_model, model)
        try:
            result = out_model.model_validate_json(formatted)
        except Exception as exc:
            # One repair pass: provider streaming/parsing should have produced
            # JSON text, but keep a bounded correction path for loose providers.
            formatted = await self._format_structured(
                task,
                worker_answer,
                out_model,
                model,
                invalid_output=formatted,
                error=str(exc),
            )
            try:
                result = out_model.model_validate_json(formatted)
            except Exception as repair_exc:
                raise WorkflowStructuredOutputMissing(
                    "workflow formatter did not return valid structured output"
                ) from repair_exc
        # Preserve the contract: a pydantic schema returns the validated instance;
        # a dict schema returns a dict.
        return result if out_model is schema else result.model_dump()

    @observed_trace("workflow.format", tags="workflow")
    async def _format_structured(
        self,
        task: str,
        worker_answer: str,
        out_model: type,
        model: str | None,
        *,
        invalid_output: str | None = None,
        error: str | None = None,
    ) -> str:
        test_formatter = getattr(self.ctx, "format_structured", None)
        if callable(test_formatter):
            return await test_formatter(
                task=task,
                worker_answer=worker_answer,
                response_format=out_model,
                invalid_output=invalid_output,
                error=error,
            )
        if (
            self._budget is not None
            and self._budget.total is not None
            and self._budget.output_tokens >= self._budget.total
        ):
            raise WorkflowBudgetExceeded(
                f"workflow output-token budget of {self._budget.total} exhausted ({self._budget.output_tokens} spent)"
            )
        user = f"Task:\n{task}\n\nWorker answer:\n{worker_answer}"
        if invalid_output is not None:
            user = (
                "Return valid JSON for this schema from the worker answer.\n\n"
                f"{user}\n\nInvalid formatter output:\n{invalid_output}\n\nError: {error or ''}"
            )
        model_id = model or self._default_model or getattr(getattr(self.ctx, "run", None), "model", None)
        if not model_id:
            raise WorkflowStructuredFormatError("workflow formatter has no model")
        try:
            response = await llm_client.complete(
                model=model_id,
                messages=[
                    {"role": "system", "content": _FORMATTER_PROMPT},
                    {"role": "user", "content": user},
                ],
                response_format=out_model,
            )
        except Exception as exc:
            raise WorkflowStructuredFormatError("workflow formatter failed") from exc
        if self._budget is not None:
            self._budget.output_tokens += response.usage.completion_tokens
        return (response.choices[0].message.content or "").strip()

    async def parallel(self, units: list[Unit]) -> list[Any]:
        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(_safe(unit)) for unit in units]
        return [task.result() for task in tasks]

    async def pipeline(self, items: list[Any], *stages: Stage) -> list[Any]:
        async def chain(item: Any, index: int) -> Any:
            current = item
            for stage in stages:
                current = await stage(current, item, index)
                if current is None:
                    return None
            return current

        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(_safe(lambda it=it, i=i: chain(it, i))) for i, it in enumerate(items)]
        return [task.result() for task in tasks]

    @observed_trace("workflow.agent", tags="workflow")
    async def _spawn(
        self,
        task: str,
        tools: list[dict] | list[str] | None,
        model: str | None,
        system_prompt: str | None,
        phase: str | None,
        agent_type_label: str | None = None,
        scope: ToolFilter | None = None,
        type_exclude: frozenset[str] = frozenset(),
        extra_tools: dict[str, Any] | None = None,
    ) -> str:
        # Cap + count every real worker spawn here. Schema formatter/repair
        # passes are internal LLM calls and do not consume spawn slots.
        if self.spawn_count >= _MAX_WORKFLOW_SPAWNS:
            raise WorkflowSpawnLimit(f"workflow exceeded {_MAX_WORKFLOW_SPAWNS} agent spawns (runaway guard)")
        # Hard token ceiling: don't start a new agent once the run's shared budget
        # is spent. The spawned child shares this RunBudget, so this bounds the
        # whole fan-out (a soft-hard cap — in-flight steps may overshoot by one).
        if (
            self._budget is not None
            and self._budget.total is not None
            and self._budget.output_tokens >= self._budget.total
        ):
            raise WorkflowBudgetExceeded(
                f"workflow output-token budget of {self._budget.total} exhausted ({self._budget.output_tokens} spent)"
            )
        self.spawn_count += 1
        lifecycle_id = f"{self.parent_id}:{uuid4().hex[:8]}" if self.parent_id else None
        async with _GLOBAL_SEM:
            spawn = await self.ctx.spawn_fn(
                self.ctx,
                task=task,
                system_prompt=system_prompt,
                tools=tools,
                model_override=model or self._default_model,
                reasoning_effort_override=self._default_reasoning_effort,
                parent_id=self.parent_id,
                isolation=IsolationLevel.FULL,
                agent_type=agent_type_label or phase or "workflow",
                lifecycle_id=lifecycle_id,
                workflow_id=self.workflow_id,
                phase=phase,
                scope=scope,
                exclude_tools=_WORKFLOW_EXCLUDE_TOOLS | type_exclude,
                extra_tools=extra_tools,
            )
        return spawn.text
