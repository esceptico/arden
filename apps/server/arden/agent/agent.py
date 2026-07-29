import asyncio
import json
import time
from collections.abc import AsyncGenerator, Callable, Sequence
from dataclasses import dataclass
from uuid import uuid4

from judgeval import Tracer

from arden.agent.hooks import AgentHooks
from arden.agent.llm.client import LLMClient
from arden.agent.llm.parsing import normalize_assistant_message, parse_tool_calls, trailing_incomplete_tool_step
from arden.agent.model_request import ModelRequest, ModelRequestMiddleware, apply_model_request_middlewares
from arden.agent.tools.dispatch import dispatch_tools
from arden.agent.tools.executor import AgentToolExecutor
from arden.agent.tools.runner import ToolRunner
from arden.agent.types.events import (
    ReasoningBlock,
    ReasoningDelta,
    ReasoningEnded,
    ReasoningStarted,
    Result,
    TextBlock,
    TextDelta,
    TextEnded,
    TextStarted,
    ToolCompleted,
    ToolInputDelta,
    ToolInputEnded,
    ToolInputStarted,
    ToolStarted,
)
from arden.agent.types.llm import (
    CompletionResponse,
    FinishReason,
    ProviderToolCall,
    ReasoningContentDelta,
    Role,
    ToolCallStreamDelta,
)
from arden.agent.types.stop import StopReason
from arden.agent.types.tool_choice import ToolChoice, ToolChoiceMode
from arden.agent.types.tools import ToolOutcome, ToolOutcomeStatus
from arden.agent.types.usage import Usage
from arden.logging import get_logger

_logger = get_logger(__name__)

AgentEvent = (
    TextStarted
    | TextDelta
    | TextEnded
    | TextBlock
    | ReasoningBlock
    | ReasoningStarted
    | ReasoningDelta
    | ReasoningEnded
    | ToolStarted
    | ToolCompleted
    | ToolInputStarted
    | ToolInputDelta
    | ToolInputEnded
    | Result
)


def _tool_name(tool: dict) -> str | None:
    name = tool.get("function", tool).get("name")
    return name if isinstance(name, str) else None


def _provider_loaded_tool_names(call: ProviderToolCall) -> set[str]:
    names: set[str] = set()
    for raw in ((call.provider_item or {}).get("arguments"), call.arguments):
        args = raw
        if isinstance(raw, str):
            try:
                args = json.loads(raw)
            except json.JSONDecodeError:
                continue
        if not isinstance(args, dict):
            continue
        for key in ("tools", "paths"):
            values = args.get(key)
            if isinstance(values, list):
                names.update(value for value in values if isinstance(value, str))
    return names


@dataclass
class RunBudget:
    tool_calls: int = 0
    # Cumulative output (completion) tokens across the whole run subtree — the
    # same RunBudget instance is shared by the top agent and every spawned child
    # (see core/spawner.py), so this is the turn's total spend. `total` is the
    # optional hard ceiling; once spent reaches it, further LLM steps and spawns
    # are denied.
    output_tokens: int = 0
    total: int | None = None


class Agent:
    def __init__(
        self,
        tools: list[dict],
        client: LLMClient,
        executor: AgentToolExecutor,
        model: str,
        max_iterations: int | None = None,
        max_tool_calls: int | None = None,
        max_wall_time_seconds: float | None = None,
        max_cost: float | None = None,
        max_depth: int = 3,
        current_depth: int = 0,
        parent_id: str | None = None,
        tool_choice: ToolChoice = ToolChoiceMode.AUTO,
        reasoning_effort: str | None = None,
        prompt_cache_key: str | None = None,
        hooks: AgentHooks | None = None,
        model_request_middlewares: Sequence[ModelRequestMiddleware] = (),
        cost_calculator: Callable[[CompletionResponse], float] | None = None,
        cost_getter: Callable[[], float] | None = None,
        clock: Callable[[], float] = time.monotonic,
        started_at: float | None = None,
        budget: RunBudget | None = None,
    ):
        if max_cost is not None and cost_calculator is None and cost_getter is None:
            raise ValueError("max_cost requires cost_calculator or cost_getter")

        self.tools = tools
        self.client = client
        self.model = model
        self.max_iterations = max_iterations
        self.max_tool_calls = max_tool_calls
        self.max_wall_time_seconds = max_wall_time_seconds
        self.max_cost = max_cost
        self.max_depth = max_depth
        self.current_depth = current_depth
        self.parent_id = parent_id
        self.tool_choice = tool_choice
        self.reasoning_effort = reasoning_effort
        self.prompt_cache_key = prompt_cache_key
        self.hooks = hooks or AgentHooks()
        self.model_request_middlewares = tuple(model_request_middlewares)
        self.cost_calculator = cost_calculator
        self.cost_getter = cost_getter
        self._clock = clock
        self._started_at = started_at
        self._budget = budget or RunBudget()
        self._executor = executor
        self._runner = ToolRunner(executor=executor, depth=current_depth, parent_id=parent_id)
        self._last_response: CompletionResponse | None = None
        self._last_text_id: str | None = None
        self._last_streamed_tool_input_ids: set[str] = set()
        self._usage = Usage()
        self._cost = 0.0

    async def stream(self, messages: list[dict]) -> AsyncGenerator[AgentEvent]:
        if self.current_depth >= self.max_depth:
            yield self._result("", StopReason.MAX_DEPTH, 0)
            return

        started_at = self._started_at if self._started_at is not None else self._clock()
        step = 0
        result_text = ""
        try:
            if incomplete_step := trailing_incomplete_tool_step(messages):
                recovered = (
                    await self.hooks.recover_tool_calls(incomplete_step.pending_calls)
                    if self.hooks.recover_tool_calls
                    else {}
                )
                calls_to_execute = [
                    call for call in incomplete_step.pending_calls if call.tool_call.id not in recovered
                ]
                missing_ids = {call.tool_call.id for call in incomplete_step.pending_calls}
                missing_raw_calls = [call for call in incomplete_step.raw_tool_calls if call.id in missing_ids]
                self._record_tool_calls(len(calls_to_execute))
                async for event in dispatch_tools(
                    self._runner,
                    messages,
                    calls_to_execute,
                    missing_raw_calls,
                    recovered_results=recovered,
                ):
                    yield event
                step = 1

            while True:
                await self._drain_pending(messages)

                if self.max_iterations is not None and step >= self.max_iterations:
                    yield self._result(result_text, StopReason.MAX_ITERATIONS, step, messages)
                    return

                if reason := self._budget_stop_reason(started_at):
                    yield self._result(result_text, reason, step, messages)
                    return

                budget_reminder = self._budget_pressure_reminder()
                (
                    step_model,
                    step_tools,
                    step_tool_choice,
                    step_reasoning_effort,
                    step_deferred_tools,
                ) = await self._prepare(step, messages)

                if reason := self._budget_stop_reason(started_at):
                    yield self._result(result_text, reason, step, messages)
                    return

                async for event in self._call_llm(
                    messages,
                    step_model,
                    step_tools,
                    step_tool_choice,
                    step_reasoning_effort,
                    step_deferred_tools,
                    budget_reminder=budget_reminder,
                ):
                    yield event

                response_message = self._last_response.choices[0].message
                if reason := self._budget_stop_reason(started_at):
                    if response_message.tool_calls:
                        self._append_budget_denials(messages, response_message.tool_calls, reason)
                    yield self._result(result_text, reason, step, messages)
                    return

                # Model hit its per-response max_tokens mid-generation. Tool calls in a
                # truncated response are unreliable, and looping just re-truncates (a
                # runaway). Surface the partial text and stop.
                if self._last_response.choices[0].finish_reason == FinishReason.LENGTH:
                    result_text = (response_message.content or "").strip()
                    yield self._result(result_text, StopReason.MAX_OUTPUT_LENGTH, step, messages)
                    return

                if not response_message.tool_calls:
                    if self._mark_provider_loaded_tools(
                        response_message.provider_tool_calls or [], step_deferred_tools
                    ):
                        step += 1
                        if self.hooks.on_step_finish:
                            await self.hooks.on_step_finish(step, self._last_response, messages)
                        continue
                    # A user message may have arrived during this LLM turn.
                    # Drain it before declaring the run finished so the agent
                    # can respond to it instead of stranding it.
                    before = len(messages)
                    await self._drain_pending(messages)
                    if len(messages) > before:
                        continue
                    result_text = (response_message.content or "").strip()
                    yield self._result(result_text, StopReason.END_TURN, step)
                    return

                if text := (response_message.content or "").strip():
                    yield TextBlock(
                        depth=self.current_depth,
                        parent_id=self.parent_id,
                        message_id=self._last_text_id,
                        content=text,
                    )

                calls = parse_tool_calls(response_message.tool_calls)
                if step_deferred_tools and hasattr(self._executor, "mark_provider_loaded_tools"):
                    deferred_names = {name for tool in step_deferred_tools if (name := _tool_name(tool))}
                    loaded_names = {call.name for call in calls if call.name in deferred_names}
                    for provider_call in response_message.provider_tool_calls or []:
                        loaded_names.update(_provider_loaded_tool_names(provider_call) & deferred_names)
                    self._executor.mark_provider_loaded_tools(loaded_names)
                if self._would_exceed_tool_budget(len(calls)):
                    self._append_budget_denials(messages, response_message.tool_calls, StopReason.MAX_TOOL_CALLS)
                    yield self._result(result_text, StopReason.MAX_TOOL_CALLS, step, messages)
                    return
                self._record_tool_calls(len(calls))

                streamed_tool_input_ids = self._last_streamed_tool_input_ids
                async for event in dispatch_tools(self._runner, messages, calls, response_message.tool_calls):
                    if isinstance(event, ToolStarted) and event.tool_id in streamed_tool_input_ids:
                        continue
                    yield event

                if reason := self._budget_stop_reason(started_at):
                    if reason == StopReason.MAX_TOKEN_BUDGET:
                        try:
                            async for event in self._call_llm(
                                messages,
                                step_model,
                                [],
                                ToolChoiceMode.NONE,
                                step_reasoning_effort,
                                [],
                                budget_reminder=(
                                    "Output-token budget exhausted. Return the final answer now from the tool results "
                                    "already present. No new tool calls, no apology, no process recap. If incomplete, "
                                    "report partial findings and gaps."
                                ),
                            ):
                                yield event
                            response_message = self._last_response.choices[0].message
                            result_text = (response_message.content or "").strip()
                        except Exception:
                            result_text = ""
                    yield self._result(result_text, reason, step, messages)
                    return

                step += 1
                if self.hooks.on_step_finish:
                    await self.hooks.on_step_finish(step, self._last_response, messages)

        except asyncio.CancelledError:
            result_text = "Cancelled."
            yield self._result(result_text, StopReason.CANCELLED, step, messages)
            raise

        finally:
            if self.hooks.on_finish:
                await self.hooks.on_finish(result_text, step, messages)

    @Tracer.observe(span_type="agent", span_name="agent.run", record_input=False)
    async def run(self, messages: list[dict]) -> Result:
        result = self._result("", StopReason.END_TURN, 0)
        async for event in self.stream(messages):
            if isinstance(event, Result):
                result = event
        return result

    # -- internals --

    def _result(self, text: str, reason: StopReason, step: int, messages: list[dict] | None = None) -> Result:
        if messages is not None and reason != StopReason.END_TURN and not text.strip():
            text = self._fallback_result_text(messages, reason) or text
        return Result(text=text, stop_reason=reason, steps=step, usage=self._usage)

    def _mark_provider_loaded_tools(
        self,
        provider_tool_calls: Sequence[ProviderToolCall],
        deferred_tools: list[dict] | None,
    ) -> set[str]:
        if not deferred_tools or not hasattr(self._executor, "mark_provider_loaded_tools"):
            return set()
        deferred_names = {name for tool in deferred_tools if (name := _tool_name(tool))}
        loaded_names: set[str] = set()
        for provider_call in provider_tool_calls:
            loaded_names.update(_provider_loaded_tool_names(provider_call) & deferred_names)
        if loaded_names:
            self._executor.mark_provider_loaded_tools(loaded_names)
        return loaded_names

    def _fallback_result_text(self, messages: list[dict], reason: StopReason) -> str:
        tool_outputs = [str(msg.get("content") or "").strip() for msg in messages if msg.get("role") == Role.TOOL]
        tool_outputs = [item for item in tool_outputs if item]
        if not tool_outputs:
            return ""
        excerpt = "\n\n".join(f"- {item}" for item in tool_outputs[-6:])
        if len(excerpt) > 4000:
            excerpt = excerpt[:4000].rstrip() + "\n... [truncated]"
        return f"Stopped because {reason.value} was reached after tool work. Recent tool results:\n{excerpt}"

    def _budget_stop_reason(self, started_at: float) -> StopReason | None:
        if self.max_tool_calls is not None and self._budget.tool_calls >= self.max_tool_calls:
            return StopReason.MAX_TOOL_CALLS
        if self.max_wall_time_seconds is not None and self._clock() - started_at >= self.max_wall_time_seconds:
            return StopReason.MAX_WALL_TIME
        if self.max_cost is not None and self._current_cost() >= self.max_cost:
            return StopReason.MAX_COST
        if self._budget.total is not None and self._budget.output_tokens >= self._budget.total:
            return StopReason.MAX_TOKEN_BUDGET
        return None

    def _budget_pressure_reminder(self) -> str | None:
        if self._budget.total is None or self._budget.total <= 0:
            return None
        remaining = max(0, self._budget.total - self._budget.output_tokens)
        ratio = remaining / self._budget.total
        prefix = f"Output-token budget pressure: {remaining} of {self._budget.total} tokens remain. "
        if ratio <= 0.05:
            return prefix + "Finalize with current evidence now. Return partial findings if incomplete."
        if ratio <= 0.10:
            return prefix + "Wrap up now; do not start broad new work. Preserve a useful final answer."
        if ratio <= 0.25:
            return prefix + "Budget is limited; prioritize finishing the current objective."
        return None

    def _current_cost(self) -> float:
        if self.cost_getter:
            return self.cost_getter()
        return self._cost

    def _would_exceed_tool_budget(self, call_count: int) -> bool:
        return self.max_tool_calls is not None and self._budget.tool_calls + call_count > self.max_tool_calls

    def _record_tool_calls(self, call_count: int) -> None:
        self._budget.tool_calls += call_count

    def _append_budget_denials(self, messages: list[dict], tool_calls, reason: StopReason) -> None:
        content = self._budget_denial_content(reason)
        for tc in tool_calls:
            messages.append({"role": Role.TOOL, "tool_call_id": tc.id, "content": content})

    def _budget_denial_content(self, reason: StopReason) -> str:
        if reason == StopReason.MAX_TOOL_CALLS:
            assert self.max_tool_calls is not None
            return f"Tool call denied: max tool-call budget of {self.max_tool_calls} would be exceeded."
        if reason == StopReason.MAX_WALL_TIME:
            return "Tool call denied: max wall-time budget exceeded."
        if reason == StopReason.MAX_COST:
            return "Tool call denied: max cost budget exceeded."
        if reason == StopReason.MAX_TOKEN_BUDGET:
            return f"Tool call denied: max output-token budget of {self._budget.total} exceeded."
        return f"Tool call denied: {reason.value}."

    async def _drain_pending(self, messages: list[dict]) -> None:
        if not self.hooks.get_pending_messages:
            return
        pending = await self.hooks.get_pending_messages()
        if pending:
            messages.extend(pending)

    async def _prepare(
        self, step: int, messages: list[dict]
    ) -> tuple[str, list[dict], ToolChoice, str | None, list[dict]]:
        prepared = await apply_model_request_middlewares(
            ModelRequest(
                step=step,
                messages=messages,
                model=self.model,
                tools=self.tools,
                deferred_tools=[],
                tool_choice=self.tool_choice,
                reasoning_effort=self.reasoning_effort,
                previous_response=self._last_response,
            ),
            self.model_request_middlewares,
        )

        if prepared.messages is not messages:
            messages.clear()
            messages.extend(prepared.messages)

        return prepared.model, prepared.tools, prepared.tool_choice, prepared.reasoning_effort, prepared.deferred_tools

    async def _call_llm(
        self,
        messages: list[dict],
        model: str,
        tools: list[dict],
        tool_choice: ToolChoice,
        reasoning_effort: str | None,
        deferred_tools: list[dict] | None = None,
        *,
        budget_reminder: str | None = None,
    ) -> AsyncGenerator[AgentEvent]:
        try:
            self._last_streamed_tool_input_ids = set()
            response = None
            # Stable id assigned at the start of the assistant turn. Used both
            # in the SSE `message_id` for streaming clients and persisted on
            # the saved message so callers (e.g. branching) can reference it.
            text_id = f"text-{uuid4().hex[:10]}"
            text_started = False
            text_chunks: list[str] = []
            text_ended = False
            reasoning_id = f"reasoning-{uuid4().hex[:10]}"
            reasoning_started = False
            reasoning_parts: list[str] = []
            streamed_tool_inputs: dict[int, dict[str, object]] = {}
            streamed_tool_input_ids: set[str] = set()
            streamed_provider_tool_ids: set[str] = set()

            def close_text(content: str | None = None) -> TextEnded | None:
                nonlocal text_ended
                if not text_started or text_ended:
                    return None
                text_ended = True
                return TextEnded(
                    depth=self.current_depth,
                    parent_id=self.parent_id,
                    message_id=text_id,
                    content="".join(text_chunks) if content is None else content,
                )

            def get_tool_input(index: int) -> dict[str, object]:
                if index not in streamed_tool_inputs:
                    streamed_tool_inputs[index] = {
                        "tool_id": None,
                        "name": None,
                        "args": "",
                        "started": False,
                        "ended": False,
                    }
                return streamed_tool_inputs[index]

            kwargs = {"tool_choice": tool_choice}
            if reasoning_effort is not None:
                kwargs["reasoning_effort"] = reasoning_effort
            if self.prompt_cache_key is not None:
                kwargs["prompt_cache_key"] = self.prompt_cache_key
            request_messages = (
                [*messages, {"role": Role.SYSTEM, "content": budget_reminder}] if budget_reminder else messages
            )
            if deferred_tools:
                kwargs["deferred_tools"] = deferred_tools
            async for item in self.client.stream(request_messages, model, tools, **kwargs):
                if isinstance(item, str):
                    if not text_started:
                        text_started = True
                        yield TextStarted(
                            depth=self.current_depth,
                            parent_id=self.parent_id,
                            message_id=text_id,
                        )
                    text_chunks.append(item)
                    yield TextDelta(
                        depth=self.current_depth,
                        parent_id=self.parent_id,
                        message_id=text_id,
                        content=item,
                    )
                elif isinstance(item, ReasoningContentDelta):
                    content = item.content.lstrip() if not reasoning_parts else item.content
                    if not content:
                        continue
                    reasoning_parts.append(content)
                    if not reasoning_started:
                        reasoning_started = True
                        yield ReasoningStarted(
                            depth=self.current_depth,
                            parent_id=self.parent_id,
                            message_id=reasoning_id,
                        )
                    yield ReasoningDelta(
                        depth=self.current_depth,
                        parent_id=self.parent_id,
                        message_id=reasoning_id,
                        content=content,
                    )
                elif isinstance(item, ToolCallStreamDelta):
                    state = get_tool_input(item.index)
                    if item.tool_id:
                        state["tool_id"] = item.tool_id
                    if item.name:
                        state["name"] = item.name
                    if item.arguments_delta:
                        state["args"] = str(state["args"]) + item.arguments_delta

                    tool_id = state["tool_id"]
                    name = state["name"]
                    if tool_id and name and not state["started"]:
                        if event := close_text():
                            yield event
                        state["started"] = True
                        streamed_tool_input_ids.add(str(tool_id))
                        meta = self._executor.get_meta(str(name))
                        yield ToolInputStarted(
                            depth=self.current_depth,
                            parent_id=self.parent_id,
                            tool_id=str(tool_id),
                            name=str(name),
                            display_name=meta.display_name if meta else str(name),
                            kind=meta.kind if meta else "tool",
                            icon=meta.icon if meta else None,
                            noun=meta.noun if meta else None,
                            source=meta.source if meta else None,
                        )
                        if args := str(state["args"]):
                            yield ToolInputDelta(
                                depth=self.current_depth,
                                parent_id=self.parent_id,
                                tool_id=str(tool_id),
                                delta=args,
                            )
                    elif item.arguments_delta and state["started"] and tool_id:
                        yield ToolInputDelta(
                            depth=self.current_depth,
                            parent_id=self.parent_id,
                            tool_id=str(tool_id),
                            delta=item.arguments_delta,
                        )

                    if item.done and state["started"] and not state["ended"] and tool_id:
                        state["ended"] = True
                        yield ToolInputEnded(
                            depth=self.current_depth,
                            parent_id=self.parent_id,
                            tool_id=str(tool_id),
                        )
                elif isinstance(item, ProviderToolCall):
                    if not item.done and item.id in streamed_provider_tool_ids:
                        continue
                    if not item.done:
                        if event := close_text():
                            yield event
                        streamed_provider_tool_ids.add(item.id)
                        yield self._provider_tool_started(item)
                    else:
                        if item.id not in streamed_provider_tool_ids:
                            if event := close_text():
                                yield event
                            streamed_provider_tool_ids.add(item.id)
                            yield self._provider_tool_started(item)
                        yield self._provider_tool_completed(item)
                elif isinstance(item, CompletionResponse):
                    response = item
        except asyncio.CancelledError:
            if event := close_text():
                yield event
            raise
        except Exception as exc:
            if event := close_text():
                yield event
            if self.hooks.on_error:
                await self.hooks.on_error(exc)
            raise

        if response is None:
            if event := close_text():
                yield event
            raise RuntimeError("LLM stream ended without a CompletionResponse")

        for state in streamed_tool_inputs.values():
            tool_id = state["tool_id"]
            if state["started"] and not state["ended"] and tool_id:
                state["ended"] = True
                yield ToolInputEnded(
                    depth=self.current_depth,
                    parent_id=self.parent_id,
                    tool_id=str(tool_id),
                )
        self._last_streamed_tool_input_ids = streamed_tool_input_ids

        self._last_response = response
        self._usage += response.usage
        self._budget.output_tokens += response.usage.completion_tokens
        if self.cost_calculator:
            self._cost += self.cost_calculator(response)
        if reasoning_started:
            yield ReasoningEnded(depth=self.current_depth, parent_id=self.parent_id, message_id=reasoning_id)
        elif reasoning := response.choices[0].message.reasoning_content:
            yield ReasoningBlock(depth=self.current_depth, parent_id=self.parent_id, content=reasoning)
        if response.choices[0].message.provider_tool_calls:
            if event := close_text(response.choices[0].message.content or ""):
                yield event
        for call in response.choices[0].message.provider_tool_calls or []:
            events = (
                (self._provider_tool_completed(call),)
                if call.id in streamed_provider_tool_ids
                else self._provider_tool_events(call)
            )
            for event in events:
                yield event
        assistant_msg = normalize_assistant_message(response.choices[0].message)
        if text_started:
            # Persist the same id we streamed, so the desktop client's
            # locally-keyed message and the saved row share an id. Branching
            # by message id then doesn't need a history refresh.
            assistant_msg["client_id"] = text_id
            self._last_text_id = text_id
        else:
            self._last_text_id = None
        messages.append(assistant_msg)
        if event := close_text(response.choices[0].message.content or ""):
            yield event
        if self.hooks.on_response:
            await self.hooks.on_response(response)

    def _provider_tool_events(self, call: ProviderToolCall) -> tuple[ToolStarted, ToolCompleted]:
        return (
            self._provider_tool_started(call),
            self._provider_tool_completed(call),
        )

    def _provider_tool_started(self, call: ProviderToolCall) -> ToolStarted:
        return ToolStarted(
            depth=self.current_depth,
            parent_id=self.parent_id,
            tool_id=call.id,
            name=call.name,
            args=self._provider_tool_args(call.arguments),
            display_name="Search Tools" if call.name == "tool_search" else call.name,
            kind="tool",
            icon="search",
            noun=None,
            source="provider",
        )

    def _provider_tool_completed(self, call: ProviderToolCall) -> ToolCompleted:
        return ToolCompleted(
            depth=self.current_depth,
            parent_id=self.parent_id,
            tool_id=call.id,
            name=call.name,
            result=call.result,
            preview=call.result,
            duration_ms=0,
            is_error=False,
            data={"provider_executed": True},
            display_name="Search Tools" if call.name == "tool_search" else call.name,
            kind="tool",
            outcome=ToolOutcome(status=ToolOutcomeStatus.SUCCEEDED),
        )

    def _provider_tool_args(self, raw: str) -> dict:
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {"input": raw}
        return parsed if isinstance(parsed, dict) else {"input": parsed}
