from dataclasses import replace
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel

from arden.agent import (
    Agent,
    Choice,
    CompletionResponse,
    FinishReason,
    Message,
    ProviderToolCall,
    Result,
    StopReason,
    Usage,
)
from arden.agent.model_request import ModelRequest, apply_model_request_middlewares
from arden.agent.types.tool_choice import ToolChoiceMode
from arden.context.models import SessionState
from arden.core.compaction_model_request_middleware import CompactionModelRequestMiddleware
from arden.core.deferred_tools_middleware import DeferredToolsModelRequestMiddleware
from arden.core.factory import AgentConfig, create_agent
from arden.core.model_context_budget import (
    MODEL_TOOL_RESULT_KEEP_FULL_CHARS,
    ToolResultContextBudgetMiddleware,
)
from arden.core.spawner import create_spawn_fn
from arden.core.tool_executor import ArdenToolExecutor
from arden.integrations.core import APP_CONTROL, SESSIONS
from arden.tools.core import ToolAction, ToolPolicy, ToolResult, ToolScope, tool
from arden.tools.core.context import BackgroundTaskRegistry, IOBridge, RunContext, ToolContext, ToolExecution
from arden.tools.core.registry import ToolRegistry
from arden.tools.core.scope import tools
from arden.tools.core.types import ApprovalInfo
from arden.tools.deferred import (
    build_deferred_tools_prompt,
    build_deferred_tools_prompt_for_schemas,
    build_native_deferred_tools_prompt_for_schemas,
    is_deferred_tool,
    load_tools_tool,
    tool_search_tool,
)
from arden.tools.executor import ToolExecutor
from tests.helpers import MockCompletionClient, MockLLMClient, make_text_response, make_tool_response

READ_INTERNAL_POLICY = ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL)
WRITE_INTERNAL_POLICY = ToolPolicy(action=ToolAction.WRITE, scope=ToolScope.INTERNAL, requires_approval=True)
READ_EXTERNAL_POLICY = ToolPolicy(action=ToolAction.READ, scope=ToolScope.EXTERNAL)
WRITE_EXTERNAL_POLICY = ToolPolicy(action=ToolAction.WRITE, scope=ToolScope.EXTERNAL, requires_approval=True)


class SearchInput(BaseModel):
    query: str = ""


async def fake_search(execution: ToolExecution, args: SearchInput) -> ToolResult:
    return ToolResult(content=f"searched: {args.query}", preview="searched")


async def fake_echo(execution: ToolExecution, args: SearchInput) -> ToolResult:
    return ToolResult(content=args.query, preview="echo")


async def fake_approval(execution: ToolExecution, args: SearchInput) -> ApprovalInfo:
    return ApprovalInfo(description="Test mutation", preview=args.query, diff=None)


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register("load_tools", load_tools_tool, source="_system")
    registry.register("tool_search", tool_search_tool, source="_system")
    registry.register(
        "echo",
        tool(
            description="Always visible echo",
            input_model=SearchInput,
            policy=READ_INTERNAL_POLICY,
            execute=fake_echo,
        ),
        source="_system",
    )
    registry.register(
        "write_file",
        tool(
            description="Write a local file",
            input_model=SearchInput,
            policy=WRITE_INTERNAL_POLICY,
            approval=fake_approval,
            execute=fake_search,
        ),
        source="_system",
    )
    registry.register(
        "edit_file",
        tool(
            description="Edit a local file",
            input_model=SearchInput,
            policy=WRITE_INTERNAL_POLICY,
            approval=fake_approval,
            execute=fake_search,
        ),
        source="_system",
    )
    registry.register(
        "background",
        tool(
            description="Spawn a background agent",
            input_model=SearchInput,
            policy=READ_INTERNAL_POLICY,
            execute=fake_search,
            kind="agent",
        ),
        source="_system",
    )
    registry.register(
        "cancel_agent",
        tool(
            description="Stop a running agent",
            input_model=SearchInput,
            policy=WRITE_INTERNAL_POLICY,
            approval=fake_approval,
            execute=fake_search,
        ),
        source="_background",
    )
    registry.register(
        "notify",
        tool(
            description="Send a notification",
            input_model=SearchInput,
            policy=WRITE_EXTERNAL_POLICY,
            approval=fake_approval,
            execute=fake_search,
        ),
        source="_notifications",
    )
    registry.register(
        "set_directives",
        tool(
            description="Update persistent behavior directives",
            input_model=SearchInput,
            policy=WRITE_INTERNAL_POLICY,
            approval=fake_approval,
            execute=fake_search,
        ),
        source="_directives",
    )
    registry.register(
        "slack_search",
        tool(
            description="Search Slack messages across the workspace",
            input_model=SearchInput,
            policy=READ_EXTERNAL_POLICY,
            execute=fake_search,
        ),
        source="slack",
    )
    return registry


def _request(registry: ToolRegistry) -> ModelRequest:
    return ModelRequest(
        step=0,
        messages=[],
        model="test",
        tools=registry.get_schemas(),
        deferred_tools=[],
        tool_choice=ToolChoiceMode.AUTO,
        reasoning_effort=None,
        previous_response=None,
    )


async def _identity(req: ModelRequest) -> ModelRequest:
    return req


class _Executor:
    def __init__(self, registry: ToolRegistry):
        self.registry = registry

    @property
    def tool_services(self):
        return {}

    def get_tools(self, **kwargs):
        return self.registry.get_schemas(**kwargs)


class AlwaysCompacts:
    def should_compact(self, messages: list[dict], model: str, last_input_tokens: int | None) -> bool:
        return True

    async def maybe_compact(
        self,
        messages: list[dict],
        model: str,
        last_input_tokens: int | None,
        *,
        rehydration_state: dict | None = None,
    ) -> list[dict] | None:
        return [{"role": "system", "content": "compacted"}]


class PromptAwareCompactor(AlwaysCompacts):
    def __init__(self):
        self.prompt_context = None
        self.include_tool_messages = None

    def with_prompt_context(self, prompt_context: str, *, include_tool_messages: bool = False):
        self.prompt_context = prompt_context
        self.include_tool_messages = include_tool_messages
        return self


class RecordingCompactor:
    def __init__(self):
        self.seen: list[int | None] = []

    def should_compact(self, messages: list[dict], model: str, last_input_tokens: int | None) -> bool:
        self.seen.append(last_input_tokens)
        return False

    async def maybe_compact(
        self,
        messages: list[dict],
        model: str,
        last_input_tokens: int | None,
        *,
        rehydration_state: dict | None = None,
    ) -> list[dict] | None:
        return None


def test_run_context_rehydration_snapshot_round_trip():
    run = RunContext(
        run_id="run",
        deferred_tools_enabled=True,
        loaded_tools={"slack_search", "background"},
        loop_task_id="loop-1",
        active_plan_ref="plan:abc",
    )

    snapshot = run.to_rehydration_state(
        pending_approvals=["call-1"],
        background_tasks=[{"task_id": "bg-1", "command": "research"}],
    )

    restored = RunContext(run_id="run", deferred_tools_enabled=True)
    restored.apply_rehydration_state(snapshot)

    assert restored.loaded_tools == set()
    assert restored.loop_task_id == "loop-1"
    assert restored.active_plan_ref == "plan:abc"
    assert "loaded_tools" not in snapshot
    assert snapshot["pending_approval_ids"] == ["call-1"]
    assert snapshot["background_tasks"] == [{"task_id": "bg-1", "command": "research"}]


def test_tool_context_builds_compaction_rehydration_state():
    run = RunContext(run_id="run", loaded_tools={"slack_search"}, active_plan_ref="plan:abc")
    io = IOBridge(pending_approvals={"call-1": object()})  # type: ignore[dict-item]
    background = BackgroundTaskRegistry(session_id="s")
    background._commands["bg-1"] = "research"

    ctx = ToolContext(
        session_state=SessionState(session_id="s", started_at=datetime.now(UTC)),
        registry=_registry(),
        run=run,
        io=io,
        background_tasks=background,
    )

    assert ctx.to_rehydration_state() == {
        "pending_approval_ids": ["call-1"],
        "background_tasks": [{"task_id": "bg-1", "command": "research"}],
        "active_plan_ref": "plan:abc",
        "loop_task_id": None,
        "declined_connections": [],
    }


@pytest.mark.asyncio
async def test_compaction_uses_persisted_input_tokens_on_first_request():
    registry = _registry()
    compactor = RecordingCompactor()
    middleware = CompactionModelRequestMiddleware(
        compactor=compactor,
        initial_input_tokens=314_000,
    )

    await middleware(_request(registry), _identity)

    assert compactor.seen == [314_000]


@pytest.mark.asyncio
async def test_model_context_budget_stubs_oversized_tool_tail_after_compaction():
    # A single result above the keep-full budget is stubbed (in practice offload catches it first).
    huge_result = "x" * (MODEL_TOOL_RESULT_KEEP_FULL_CHARS + 10_000)

    class CompactsToHugeToolTail:
        def should_compact(self, messages, model, last_input_tokens):
            return True

        async def maybe_compact(self, messages, model, last_input_tokens, *, rehydration_state=None):
            return [
                {"role": "system", "content": "system"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "search_text", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call-1", "content": huge_result},
            ]

    prepared = await apply_model_request_middlewares(
        _request(_registry()),
        (
            ToolResultContextBudgetMiddleware(),
            CompactionModelRequestMiddleware(compactor=CompactsToHugeToolTail()),
        ),
    )

    tool_content = prepared.messages[-1]["content"]
    assert len(tool_content) < len(huge_result)
    assert "cleared from context" in tool_content
    assert huge_result not in tool_content


@pytest.mark.asyncio
async def test_model_context_budget_keeps_recent_full_stubs_old():
    big = "x" * (MODEL_TOOL_RESULT_KEEP_FULL_CHARS // 2 + 1000)  # only the most recent fits
    messages = [{"role": "system", "content": "system"}]
    for i in range(4):
        messages.append({"role": "tool", "tool_call_id": f"call-{i}", "content": big})

    async def next_request(req: ModelRequest) -> ModelRequest:
        return ModelRequest(
            step=req.step,
            messages=messages,
            model=req.model,
            tools=req.tools,
            deferred_tools=req.deferred_tools,
            tool_choice=req.tool_choice,
            reasoning_effort=req.reasoning_effort,
            previous_response=req.previous_response,
        )

    prepared = await ToolResultContextBudgetMiddleware()(_request(_registry()), next_request)
    by_id = {m["tool_call_id"]: m["content"] for m in prepared.messages if m.get("role") == "tool"}

    # most recent kept full; older ones collapsed to a short stub
    assert by_id["call-3"] == big
    for cid in ("call-0", "call-1", "call-2"):
        assert "cleared from context" in by_id[cid]
        assert big not in by_id[cid]


@pytest.mark.asyncio
async def test_deferred_middleware_hides_then_reveals_loaded_tools():
    registry = _registry()
    run = RunContext(run_id="run", deferred_tools_enabled=True)
    middleware = DeferredToolsModelRequestMiddleware(registry=registry, run=run, get_services=dict)

    prepared = await middleware(_request(registry), _identity)
    names = {t["function"]["name"] for t in prepared.tools}
    assert "load_tools" in names
    assert "echo" in names
    assert "background" in names
    assert "write_file" not in names
    assert "edit_file" not in names
    assert "cancel_agent" not in names
    assert "notify" not in names
    assert "set_directives" not in names
    assert "slack_search" not in names

    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=registry,
        run=run,
        io=IOBridge(),
    )
    result = await registry.execute(
        "load_tools",
        ToolExecution(tool_id="call_load", tool_name="load_tools", ctx=ctx),
        {"group": "slack"},
    )
    assert not result.is_error
    assert "slack_search" in run.loaded_tools

    prepared = await middleware(_request(registry), _identity)
    names = {t["function"]["name"] for t in prepared.tools}
    assert "slack_search" in names


@pytest.mark.asyncio
async def test_deferred_middleware_uses_native_loading_for_supported_models():
    registry = _registry()
    registry.register(
        "read_wiki_page",
        tool(
            description="Read one wiki page",
            input_model=SearchInput,
            policy=READ_INTERNAL_POLICY,
            execute=fake_search,
        ),
        source="_wiki",
    )
    registry.register(
        "publish_wiki_generated",
        tool(
            description="Publish one generated wiki region",
            input_model=SearchInput,
            policy=WRITE_INTERNAL_POLICY,
            approval=fake_approval,
            execute=fake_search,
        ),
        source="_wiki",
    )
    run = RunContext(run_id="run", deferred_tools_enabled=True)
    middleware = DeferredToolsModelRequestMiddleware(registry=registry, run=run, get_services=dict)

    prepared = await middleware(replace(_request(registry), model="gpt-5.5"), _identity)
    names = {t["function"]["name"] for t in prepared.tools}
    deferred_names = {t["function"]["name"] for t in prepared.deferred_tools}

    assert "load_tools" not in names
    assert "tool_search" in names
    assert "echo" in names
    assert "slack_search" not in names
    assert "read_wiki_page" not in names
    assert "publish_wiki_generated" not in names
    assert "slack_search" in deferred_names
    assert "read_wiki_page" in deferred_names
    assert "publish_wiki_generated" in deferred_names
    assert run.deferred_tool_loader == "tool_search"


@pytest.mark.asyncio
async def test_hidden_tool_recovery_names_the_exposed_loader():
    registry = _registry()
    run = RunContext(run_id="run", deferred_tools_enabled=True)
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=registry,
        run=run,
        io=IOBridge(),
    )
    executor = ArdenToolExecutor(ToolExecutor().with_registry(registry), ctx)

    classic = await executor.execute("slack_search", {"query": "x"}, "classic")
    assert classic.is_error
    assert "load_tools" in classic.content
    assert "tool_search" not in classic.content

    middleware = DeferredToolsModelRequestMiddleware(registry=registry, run=run, get_services=dict)
    await middleware(replace(_request(registry), model="gpt-5.5"), _identity)
    native = await executor.execute("slack_search", {"query": "x"}, "native")
    assert native.is_error
    assert "tool_search" in native.content


@pytest.mark.asyncio
async def test_tool_search_loads_exact_deferred_names():
    registry = _registry()
    run = RunContext(run_id="run", deferred_tools_enabled=True)
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=registry,
        run=run,
        io=IOBridge(),
    )

    result = await registry.execute(
        "tool_search",
        ToolExecution(tool_id="call_search", tool_name="tool_search", ctx=ctx),
        {"query": "select:slack_search"},
    )

    assert not result.is_error
    assert "slack_search" in run.loaded_tools
    assert "Loaded 1 deferred tool(s)" in result.content


@pytest.mark.asyncio
async def test_agent_load_tools_reveals_slack_on_next_model_step():
    registry = _registry()
    run = RunContext(run_id="run", deferred_tools_enabled=True)
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=registry,
        run=run,
        io=IOBridge(),
    )
    executor = _Executor(registry)
    llm = MockCompletionClient(
        [
            make_tool_response("load_tools", {"group": "slack"}, call_id="call_load"),
            make_tool_response("slack_search", {"query": "hello"}, call_id="call_slack"),
            make_text_response("done"),
        ]
    )
    agent = Agent(
        tools=registry.get_schemas(),
        client=MockLLMClient(llm),
        executor=ArdenToolExecutor(executor, ctx),
        model="test-model",
        model_request_middlewares=(
            DeferredToolsModelRequestMiddleware(
                registry=registry,
                run=run,
                get_services=lambda: ctx.services,
            ),
        ),
    )

    result = await agent.run([{"role": "system", "content": "test"}, {"role": "user", "content": "search slack"}])

    assert result.text == "done"
    assert "slack_search" in run.loaded_tools
    first_tools = {t["function"]["name"] for t in llm.calls[0]["tools"]}
    second_tools = {t["function"]["name"] for t in llm.calls[1]["tools"]}
    assert "load_tools" in first_tools
    assert "slack_search" not in first_tools
    assert "load_tools" in second_tools
    assert "slack_search" in second_tools


@pytest.mark.asyncio
async def test_agent_tool_search_reveals_slack_on_next_native_model_step():
    registry = _registry()
    run = RunContext(run_id="run", deferred_tools_enabled=True)
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=registry,
        run=run,
        io=IOBridge(),
    )
    executor = _Executor(registry)
    llm = MockCompletionClient(
        [
            make_tool_response("tool_search", {"query": "select:slack_search"}, call_id="call_search", model="gpt-5.5"),
            make_tool_response("slack_search", {"query": "hello"}, call_id="call_slack", model="gpt-5.5"),
            make_text_response("done", model="gpt-5.5"),
        ]
    )
    agent = Agent(
        tools=registry.get_schemas(),
        client=MockLLMClient(llm),
        executor=ArdenToolExecutor(executor, ctx),
        model="gpt-5.5",
        model_request_middlewares=(
            DeferredToolsModelRequestMiddleware(
                registry=registry,
                run=run,
                get_services=lambda: ctx.services,
            ),
        ),
    )

    result = await agent.run([{"role": "system", "content": "test"}, {"role": "user", "content": "search slack"}])

    assert result.text == "done"
    assert "slack_search" in run.loaded_tools
    first_tools = {t["function"]["name"] for t in llm.calls[0]["tools"]}
    first_deferred = {t["function"]["name"] for t in llm.calls[0]["deferred_tools"]}
    second_tools = {t["function"]["name"] for t in llm.calls[1]["tools"]}
    assert "tool_search" in first_tools
    assert "load_tools" not in first_tools
    assert "slack_search" not in first_tools
    assert "slack_search" in first_deferred
    assert "slack_search" in second_tools


@pytest.mark.asyncio
async def test_agent_accepts_provider_loaded_deferred_tool_call():
    registry = _registry()
    run = RunContext(run_id="run", deferred_tools_enabled=True)
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=registry,
        run=run,
        io=IOBridge(),
    )
    executor = _Executor(registry)
    llm = MockCompletionClient(
        [
            make_tool_response("slack_search", {"query": "hello"}, call_id="call_slack"),
            make_text_response("done"),
        ]
    )
    agent = Agent(
        tools=registry.get_schemas(),
        client=MockLLMClient(llm),
        executor=ArdenToolExecutor(executor, ctx),
        model="test-model",
        model_request_middlewares=(
            DeferredToolsModelRequestMiddleware(
                registry=registry,
                run=run,
                get_services=lambda: ctx.services,
            ),
        ),
    )

    result = await agent.run([{"role": "system", "content": "test"}, {"role": "user", "content": "search slack"}])

    assert result.text == "done"
    assert "slack_search" in run.loaded_tools
    assert any(
        msg.get("role") == "tool" and msg.get("content") == "searched: hello" for msg in llm.calls[1]["messages"]
    )


@pytest.mark.asyncio
async def test_agent_marks_provider_searched_deferred_tools_loaded_for_next_step():
    registry = _registry()
    registry.register(
        "emails",
        tool(
            description="List emails",
            input_model=SearchInput,
            policy=READ_EXTERNAL_POLICY,
            execute=fake_search,
        ),
        source="gmail",
    )
    registry.register(
        "read_email",
        tool(
            description="Read an email",
            input_model=SearchInput,
            policy=READ_EXTERNAL_POLICY,
            execute=fake_search,
        ),
        source="gmail",
    )
    run = RunContext(run_id="run", deferred_tools_enabled=True)
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=registry,
        run=run,
        io=IOBridge(),
    )
    executor = _Executor(registry)
    first = make_tool_response("emails", {"query": "newer_than:1d"}, call_id="call_emails", model="gpt-5.5")
    first.choices[0] = replace(
        first.choices[0],
        message=replace(
            first.choices[0].message,
            provider_tool_calls=[
                ProviderToolCall(
                    id="tsc_1",
                    name="tool_search",
                    arguments='{"tools":["emails"]}',
                    provider_item={
                        "type": "tool_search_call",
                        "id": "tsc_1",
                        "status": "completed",
                        "arguments": {"paths": ["read_email"]},
                    },
                )
            ],
        ),
    )
    llm = MockCompletionClient([first, make_text_response("done", model="gpt-5.5")])
    agent = Agent(
        tools=registry.get_schemas(),
        client=MockLLMClient(llm),
        executor=ArdenToolExecutor(executor, ctx),
        model="gpt-5.5",
        model_request_middlewares=(
            DeferredToolsModelRequestMiddleware(
                registry=registry,
                run=run,
                get_services=lambda: ctx.services,
            ),
        ),
    )

    result = await agent.run([{"role": "system", "content": "test"}, {"role": "user", "content": "read email"}])

    assert result.text == "done"
    assert "emails" in run.loaded_tools
    assert "read_email" in run.loaded_tools
    second_tools = {t["function"]["name"] for t in llm.calls[1]["tools"]}
    assert "read_email" in second_tools


@pytest.mark.asyncio
async def test_agent_continues_after_provider_only_tool_search_loads_names():
    registry = _registry()
    registry.register(
        "read_wiki_page",
        tool(
            description="Read one wiki page",
            input_model=SearchInput,
            policy=READ_INTERNAL_POLICY,
            execute=fake_search,
        ),
        source="_wiki",
    )
    registry.register(
        "publish_wiki_generated",
        tool(
            description="Publish one generated wiki region",
            input_model=SearchInput,
            policy=WRITE_INTERNAL_POLICY,
            approval=fake_approval,
            execute=fake_search,
        ),
        source="_wiki",
    )
    run = RunContext(run_id="run", deferred_tools_enabled=True)
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=registry,
        run=run,
        io=IOBridge(),
    )
    executor = _Executor(registry)
    first = CompletionResponse(
        choices=[
            Choice(
                message=Message(
                    role="assistant",
                    content=None,
                    tool_calls=None,
                    reasoning_content=None,
                    provider_tool_calls=[
                        ProviderToolCall(
                            id="tsc_1",
                            name="tool_search",
                            arguments='{"tools":["read_wiki_page"]}',
                            result="Matched tools: read_wiki_page",
                        )
                    ],
                ),
                finish_reason=FinishReason.TOOL_CALLS,
            )
        ],
        usage=Usage(),
        model="gpt-5.5",
    )
    llm = MockCompletionClient(
        [
            first,
            make_tool_response(
                "read_wiki_page",
                {"query": "automations/email-updates.md"},
                call_id="call_wiki",
                model="gpt-5.5",
            ),
            make_text_response("done", model="gpt-5.5"),
        ]
    )
    agent = Agent(
        tools=registry.get_schemas(scope=tools.named("read_wiki_page") | tools.named("publish_wiki_generated")),
        client=MockLLMClient(llm),
        executor=ArdenToolExecutor(executor, ctx),
        model="gpt-5.5",
        model_request_middlewares=(
            DeferredToolsModelRequestMiddleware(
                registry=registry,
                run=run,
                get_services=lambda: ctx.services,
            ),
        ),
    )

    result = await agent.run([{"role": "system", "content": "test"}, {"role": "user", "content": "read wiki page"}])

    assert result.text == "done"
    assert "read_wiki_page" in run.loaded_tools
    first_deferred = {tool["function"]["name"] for tool in llm.calls[0]["deferred_tools"]}
    second_tools = {tool["function"]["name"] for tool in llm.calls[1]["tools"]}
    assert first_deferred == {"read_wiki_page", "publish_wiki_generated"}
    assert "read_wiki_page" in second_tools
    assert "publish_wiki_generated" not in second_tools


@pytest.mark.asyncio
async def test_load_tools_can_be_called_again_after_group_is_loaded():
    registry = _registry()
    run = RunContext(run_id="run", deferred_tools_enabled=True)
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=registry,
        run=run,
        io=IOBridge(),
    )
    first = await registry.execute(
        "load_tools",
        ToolExecution(tool_id="call_load_1", tool_name="load_tools", ctx=ctx),
        {"group": "slack"},
    )
    second = await registry.execute(
        "load_tools",
        ToolExecution(tool_id="call_load_2", tool_name="load_tools", ctx=ctx),
        {"group": "slack"},
    )

    assert not first.is_error
    assert not second.is_error
    assert "Already loaded: slack_search" in second.content
    assert "slack_search" in run.loaded_tools


@pytest.mark.asyncio
async def test_compaction_unloads_deferred_tools_and_refreshes_schema():
    registry = _registry()
    run = RunContext(
        run_id="run",
        deferred_tools_enabled=True,
        loaded_tools={"slack_search"},
        active_plan_ref="plan:abc",
    )
    deferred = DeferredToolsModelRequestMiddleware(registry=registry, run=run, get_services=dict)
    compaction = CompactionModelRequestMiddleware(
        compactor=AlwaysCompacts(),
        on_compact=run.loaded_tools.clear,
        get_rehydration_state=lambda: run.to_rehydration_state(),
        apply_rehydration_state=run.apply_rehydration_state,
    )

    prepared = await apply_model_request_middlewares(_request(registry), (deferred, compaction))
    names = {t["function"]["name"] for t in prepared.tools}
    assert "load_tools" in names
    assert "slack_search" not in names
    assert prepared.messages == [{"role": "system", "content": "compacted"}]
    assert run.loaded_tools == set()
    assert run.active_plan_ref == "plan:abc"

    next_prepared = await deferred(_request(registry), _identity)
    next_names = {t["function"]["name"] for t in next_prepared.tools}
    assert "load_tools" in next_names
    assert "slack_search" not in next_names


@pytest.mark.asyncio
async def test_create_agent_compaction_refreshes_deferred_schema():
    registry = _registry()
    loaded = {"slack_search"}
    agent = create_agent(
        executor=_Executor(registry),
        config=AgentConfig(model="test-model", research_model=None, max_depth=3, compactor=AlwaysCompacts()),
        tools=registry.get_schemas(),
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        run_id="run",
        loaded_tools=loaded,
    )
    messages = [{"role": "system", "content": "test"}, {"role": "user", "content": "search slack"}]

    _, tools, _, _, deferred_tools = await agent._prepare(0, messages)

    names = {t["function"]["name"] for t in tools}
    deferred_names = {t["function"]["name"] for t in deferred_tools}
    assert messages == [{"role": "system", "content": "compacted"}]
    assert loaded == set()
    assert "load_tools" in names
    assert "slack_search" not in names
    assert "slack_search" in deferred_names


def test_create_agent_wires_child_io_factory_onto_run_context():
    # Regression: child_io_factory must land on the RunContext the SPAWNER reads
    # (calling_ctx.run.child_io_factory), not on a RunState. The original bug was a
    # dead write to ChatContext.run (a RunState with no such field), so drill-in
    # never engaged and FULL subagents leaked their tool calls to the parent while
    # their child sessions stayed empty.
    async def factory(_params):
        raise AssertionError("sentinel — never invoked")

    agent = create_agent(
        executor=_Executor(_registry()),
        config=AgentConfig(model="test-model", research_model=None, max_depth=3),
        tools=[],
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        run_id="run",
        child_io_factory=factory,
    )
    assert agent._executor._ctx.run.child_io_factory is factory


@pytest.mark.asyncio
async def test_load_group_respects_run_allowed_names():
    registry = _registry()
    registry.register(
        "slack_post",
        tool(
            description="Post to Slack",
            input_model=SearchInput,
            policy=WRITE_EXTERNAL_POLICY,
            approval=fake_approval,
            execute=fake_search,
        ),
        source="slack",
    )
    run = RunContext(
        run_id="run",
        deferred_tools_enabled=True,
        allowed_tool_names={"load_tools", "echo", "slack_search"},
    )
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=registry,
        run=run,
        io=IOBridge(),
    )

    result = await registry.execute(
        "load_tools",
        ToolExecution(tool_id="call_load", tool_name="load_tools", ctx=ctx),
        {"group": "slack"},
    )

    assert not result.is_error
    assert "slack_search" in run.loaded_tools
    assert "slack_post" not in run.loaded_tools
    assert "Not allowed" not in result.content


def test_deferred_prompt_lists_groups_and_tools():
    registry = _registry()
    registry.register(
        "read_wiki_page",
        tool(
            description="Read one wiki page",
            input_model=SearchInput,
            policy=READ_INTERNAL_POLICY,
            execute=fake_search,
        ),
        source="_wiki",
    )
    prompt = build_deferred_tools_prompt(registry, frozenset())
    assert prompt is not None
    assert 'name="slack"' in prompt
    assert "slack_search" in prompt
    assert 'name="background"' in prompt
    assert "cancel_agent" in prompt
    assert 'name="notifications"' in prompt
    assert "notify" in prompt
    assert 'name="directives"' in prompt
    assert "set_directives" in prompt
    assert 'name="files"' in prompt
    assert "write_file" in prompt
    assert "edit_file" in prompt
    assert 'name="wiki"' in prompt
    assert "read_wiki_page" in prompt
    assert "load_tools" in prompt
    assert 'load_tools(group="slack")' in prompt
    assert "Do not use filesystem/time/no-op tool calls" in prompt


def test_deferred_prompt_respects_allowed_tool_schemas():
    registry = _registry()

    prompt = build_deferred_tools_prompt_for_schemas(registry, frozenset(), registry.get_schemas(names={"echo"}))
    assert prompt is None

    prompt = build_deferred_tools_prompt_for_schemas(
        registry,
        frozenset(),
        registry.get_schemas(names={"load_tools", "echo", "slack_search"}),
    )
    assert prompt is not None
    assert 'name="slack"' in prompt
    assert "slack_search" in prompt
    assert "background" not in prompt
    assert "notify" not in prompt
    assert "set_directives" not in prompt
    assert "write_file" not in prompt


def test_native_deferred_prompt_lists_exact_tool_names_without_group_loader():
    registry = _registry()

    prompt = build_native_deferred_tools_prompt_for_schemas(
        registry,
        frozenset(),
        registry.get_schemas(names={"echo", "slack_search"}),
    )

    assert prompt is not None
    assert "tool_search" in prompt
    assert 'tool_search(query="select:slack_search")' in prompt
    assert "slack_search" in prompt
    assert "load_tools" not in prompt
    assert "load_group" not in prompt


@pytest.mark.asyncio
async def test_background_controls_notifications_and_directives_load_by_group_aliases():
    registry = _registry()
    run = RunContext(run_id="run", deferred_tools_enabled=True)
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=registry,
        run=run,
        io=IOBridge(),
    )

    background_result = await registry.execute(
        "load_tools",
        ToolExecution(tool_id="call_bg", tool_name="load_tools", ctx=ctx),
        {"group": "background"},
    )
    notify_result = await registry.execute(
        "load_tools",
        ToolExecution(tool_id="call_notify", tool_name="load_tools", ctx=ctx),
        {"group": "notify"},
    )
    directives_result = await registry.execute(
        "load_tools",
        ToolExecution(tool_id="call_directives", tool_name="load_tools", ctx=ctx),
        {"group": "directives"},
    )

    assert not background_result.is_error
    assert not notify_result.is_error
    assert not directives_result.is_error
    assert "cancel_agent" in run.loaded_tools
    assert "background" not in run.loaded_tools
    assert "notify" in run.loaded_tools
    assert "set_directives" in run.loaded_tools


@pytest.mark.asyncio
async def test_file_actions_load_by_files_group():
    registry = _registry()
    run = RunContext(run_id="run", deferred_tools_enabled=True)
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=registry,
        run=run,
        io=IOBridge(),
    )

    result = await registry.execute(
        "load_tools",
        ToolExecution(tool_id="call_files", tool_name="load_tools", ctx=ctx),
        {"group": "files"},
    )

    assert not result.is_error
    assert "write_file" in run.loaded_tools
    assert "edit_file" in run.loaded_tools


@pytest.mark.asyncio
async def test_spawned_agents_inherit_deferred_loading(monkeypatch):
    registry = _registry()
    captured = {}

    class FakeExecutor:
        def __init__(self, registry: ToolRegistry):
            self.registry = registry

        @property
        def tool_services(self):
            return {}

        def get_tools(self, **kwargs):
            return self.registry.get_schemas(**kwargs)

    class FakeAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def stream(self, messages):
            captured["messages"] = messages
            yield Result(text="done", stop_reason=StopReason.END_TURN, steps=1, usage=Usage())

    monkeypatch.setattr("arden.core.spawner.Agent", FakeAgent)

    parent_ctx = ToolContext(
        session_state=SessionState(session_id="parent", started_at=datetime.now(UTC)),
        registry=registry,
        run=RunContext(run_id="run", max_depth=3, deferred_tools_enabled=True),
        io=IOBridge(),
    )
    parent_ctx.spawn_fn = create_spawn_fn(
        executor=FakeExecutor(registry),
        model="test-model",
        max_depth=3,
        current_depth=0,
    )

    result = await parent_ctx.spawn_fn(
        parent_ctx,
        task="search slack",
        system_prompt="child prompt",
        tools=registry.get_schemas(),
    )

    assert result.text == "done"
    assert "## DEFERRED TOOLS" in captured["messages"][0]["content"]
    assert "slack_search" in captured["messages"][0]["content"]

    middleware = captured["model_request_middlewares"][0]
    prepared = await middleware(_request(registry), _identity)
    names = {t["function"]["name"] for t in prepared.tools}
    assert "load_tools" in names
    assert "echo" in names
    assert "slack_search" not in names


@pytest.mark.asyncio
async def test_spawned_agent_compaction_refreshes_deferred_schema(monkeypatch):
    registry = _registry()
    captured = {}
    emitted = []

    async def emit(event):
        emitted.append(event)

    class FakeExecutor:
        def __init__(self, registry: ToolRegistry):
            self.registry = registry

        @property
        def tool_services(self):
            return {}

        def get_tools(self, **kwargs):
            return self.registry.get_schemas(**kwargs)

    class FakeAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def stream(self, messages):
            yield Result(text="done", stop_reason=StopReason.END_TURN, steps=1, usage=Usage())

    monkeypatch.setattr("arden.core.spawner.Agent", FakeAgent)

    parent_ctx = ToolContext(
        session_state=SessionState(session_id="parent", started_at=datetime.now(UTC)),
        registry=registry,
        run=RunContext(
            run_id="run",
            max_depth=3,
            deferred_tools_enabled=True,
            loaded_tools={"slack_search"},
        ),
        io=IOBridge(emit=emit),
    )
    parent_ctx.spawn_fn = create_spawn_fn(
        executor=FakeExecutor(registry),
        model="test-model",
        max_depth=3,
        current_depth=0,
        compactor=AlwaysCompacts(),
    )

    result = await parent_ctx.spawn_fn(
        parent_ctx,
        task="search slack",
        system_prompt="child prompt",
        tools=registry.get_schemas(),
    )

    assert result.text == "done"
    prepared = await apply_model_request_middlewares(
        _request(registry),
        captured["model_request_middlewares"],
    )
    names = {t["function"]["name"] for t in prepared.tools}
    assert prepared.messages == [{"role": "system", "content": "compacted"}]
    assert "load_tools" in names
    assert "slack_search" not in names
    assert [event.type.value for event in emitted] == ["task_started", "task_finished"]

    emitted.clear()
    captured.clear()
    result = await parent_ctx.spawn_fn(
        parent_ctx,
        task="search slack",
        system_prompt="child prompt",
        tools=registry.get_schemas(),
        parent_id="call-research",
    )

    assert result.text == "done"
    prepared = await apply_model_request_middlewares(
        _request(registry),
        captured["model_request_middlewares"],
    )
    names = {t["function"]["name"] for t in prepared.tools}
    assert prepared.messages == [{"role": "system", "content": "compacted"}]
    assert "load_tools" in names
    assert "slack_search" not in names
    compaction_events = [event for event in emitted if event.type.value.startswith("compaction_")]
    assert [event.type.value for event in compaction_events] == [
        "compaction_started",
        "compaction_finished",
    ]
    assert [event.parent_tool_call_id for event in compaction_events] == [
        "call-research",
        "call-research",
    ]


@pytest.mark.asyncio
async def test_spawned_agent_compaction_uses_research_handoff_prompt_only_for_research(monkeypatch):
    registry = _registry()
    captured = {}

    class FakeAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def stream(self, messages):
            yield Result(text="done", stop_reason=StopReason.END_TURN, steps=1, usage=Usage())

    monkeypatch.setattr("arden.core.spawner.Agent", FakeAgent)

    generic_compactor = PromptAwareCompactor()
    parent_ctx = ToolContext(
        session_state=SessionState(session_id="parent", started_at=datetime.now(UTC)),
        registry=registry,
        run=RunContext(run_id="run", max_depth=3, deferred_tools_enabled=True),
        io=IOBridge(),
    )
    parent_ctx.spawn_fn = create_spawn_fn(
        executor=_Executor(registry),
        model="test-model",
        max_depth=3,
        current_depth=0,
        compactor=generic_compactor,
    )

    result = await parent_ctx.spawn_fn(
        parent_ctx,
        task="search files",
        system_prompt="child prompt",
        tools=registry.get_schemas(),
    )

    assert result.text == "done"
    assert generic_compactor.prompt_context is None
    assert generic_compactor.include_tool_messages is None

    research_compactor = PromptAwareCompactor()
    parent_ctx.spawn_fn = create_spawn_fn(
        executor=_Executor(registry),
        model="test-model",
        max_depth=3,
        current_depth=0,
        compactor=research_compactor,
    )

    result = await parent_ctx.spawn_fn(
        parent_ctx,
        task="search files",
        system_prompt="child prompt",
        tools=registry.get_schemas(),
        kind="research",
        compaction_prompt_context="research",
        include_tool_messages_in_compaction=True,
    )

    assert result.text == "done"
    assert "Research Agent Handoff" in research_compactor.prompt_context
    assert research_compactor.include_tool_messages is True


@pytest.mark.asyncio
async def test_spawned_agent_extra_tools_are_child_only(monkeypatch):
    registry = _registry()
    captured = {}

    class ExtraInput(BaseModel):
        value: str = ""

    async def extra_tool(execution: ToolExecution, args: ExtraInput) -> ToolResult:
        return ToolResult(content=args.value, preview="extra")

    child_helper_tool = tool(
        description="Child-only helper.",
        input_model=ExtraInput,
        policy=READ_INTERNAL_POLICY,
        execute=extra_tool,
    )

    class FakeAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def stream(self, messages):
            yield Result(text="done", stop_reason=StopReason.END_TURN, steps=1, usage=Usage())

    monkeypatch.setattr("arden.core.spawner.Agent", FakeAgent)

    executor = ToolExecutor().with_registry(registry)
    parent_ctx = ToolContext(
        session_state=SessionState(session_id="parent", started_at=datetime.now(UTC)),
        registry=registry,
        run=RunContext(run_id="run", max_depth=3, deferred_tools_enabled=True),
        io=IOBridge(),
    )
    parent_ctx.spawn_fn = create_spawn_fn(
        executor=executor,
        model="test-model",
        max_depth=3,
        current_depth=0,
    )

    result = await parent_ctx.spawn_fn(
        parent_ctx,
        task="search files",
        system_prompt="child prompt",
        extra_tools={"child_helper": child_helper_tool},
    )

    tool_names = {schema["function"]["name"] for schema in captured["tools"]}
    assert result.text == "done"
    assert registry.get("child_helper") is None
    assert "child_helper" in tool_names
    assert captured["executor"]._executor.registry.get("child_helper") is child_helper_tool

    prepared = await apply_model_request_middlewares(
        ModelRequest(
            step=0,
            messages=[],
            model="test",
            tools=captured["tools"],
            deferred_tools=[],
            tool_choice=ToolChoiceMode.AUTO,
            reasoning_effort=None,
            previous_response=None,
        ),
        captured["model_request_middlewares"],
    )
    prepared_names = {schema["function"]["name"] for schema in prepared.tools}
    assert "child_helper" in prepared_names


@pytest.mark.asyncio
async def test_spawned_agent_clamps_tool_tail_after_compaction(monkeypatch):
    registry = _registry()
    captured = {}
    huge_result = "x" * (MODEL_TOOL_RESULT_KEEP_FULL_CHARS + 10_000)

    class CompactsToHugeToolTail:
        def __init__(self):
            self.seen_messages = None

        def should_compact(self, messages, model, last_input_tokens):
            return True

        async def maybe_compact(self, messages, model, last_input_tokens, *, rehydration_state=None):
            self.seen_messages = messages
            return [
                {"role": "system", "content": "system"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "search_text", "arguments": "{}"},
                        }
                    ],
                },
                {"role": "tool", "tool_call_id": "call-1", "content": huge_result},
            ]

    class FakeAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def stream(self, messages):
            yield Result(text="done", stop_reason=StopReason.END_TURN, steps=1, usage=Usage())

    monkeypatch.setattr("arden.core.spawner.Agent", FakeAgent)

    parent_ctx = ToolContext(
        session_state=SessionState(session_id="parent", started_at=datetime.now(UTC)),
        registry=registry,
        run=RunContext(run_id="run", max_depth=3, deferred_tools_enabled=True),
        io=IOBridge(),
    )
    compactor = CompactsToHugeToolTail()
    parent_ctx.spawn_fn = create_spawn_fn(
        executor=_Executor(registry),
        model="test-model",
        max_depth=3,
        current_depth=0,
        compactor=compactor,
    )

    result = await parent_ctx.spawn_fn(
        parent_ctx,
        task="search files",
        system_prompt="child prompt",
        tools=registry.get_schemas(),
    )

    assert result.text == "done"
    prepared = await apply_model_request_middlewares(
        ModelRequest(
            step=0,
            messages=[{"role": "tool", "tool_call_id": "call-input", "content": huge_result}],
            model="test",
            tools=registry.get_schemas(),
            deferred_tools=[],
            tool_choice=ToolChoiceMode.AUTO,
            reasoning_effort=None,
            previous_response=None,
        ),
        captured["model_request_middlewares"],
    )
    tool_content = prepared.messages[-1]["content"]
    assert len(tool_content) < len(huge_result)
    assert "cleared from context" in tool_content
    assert huge_result not in tool_content
    assert compactor.seen_messages is not None
    assert huge_result not in str(compactor.seen_messages)


@pytest.mark.asyncio
async def test_app_control_loads_by_app_group():
    registry = ToolRegistry()
    registry.register("load_tools", load_tools_tool, source="_system")
    for name, tool_obj in APP_CONTROL.tools.items():
        registry.register(name, tool_obj, source=APP_CONTROL.id)
    run = RunContext(run_id="run", deferred_tools_enabled=True)
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=registry,
        run=run,
        io=IOBridge(),
        services={"session": object(), "app_control": object()},
    )

    result = await registry.execute(
        "load_tools",
        ToolExecution(tool_id="call_app", tool_name="load_tools", ctx=ctx),
        {"group": "app"},
    )

    assert not result.is_error
    assert set(APP_CONTROL.tools) <= run.loaded_tools


def test_session_read_tools_are_not_deferred():
    registry = ToolRegistry()
    for name, tool_obj in SESSIONS.tools.items():
        registry.register(name, tool_obj, source=SESSIONS.id)

    assert all(not is_deferred_tool(name, registry) for name in SESSIONS.tools)
