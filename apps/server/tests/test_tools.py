"""Tool execution tests — real tool logic, controlled inputs."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from pydantic import BaseModel, Field

import arden.database as database
import arden.tools.executor as tool_executor_module
import arden.tools.research_artifacts as research_artifacts_module
from arden.agent import ToolEffect, ToolOutcome, ToolOutcomeStatus, ToolResult, ToolVerification
from arden.agent.types.tool_call import FunctionCall, PendingToolCall
from arden.agent.types.tool_call import ToolCall as AgentToolCall
from arden.agent.types.tools import ToolSourceRef
from arden.constants import OFFLOAD_PREVIEW_CHARS, OFFLOAD_THRESHOLD
from arden.context.models import SessionState
from arden.context.store import SessionStore
from arden.core.tool_executor import ArdenToolExecutor
from arden.events.sse import ToolCallResultEvent
from arden.integrations.base import Integration
from arden.server.bus import StreamRecord
from arden.services.chat import _recover_durable_tool_calls
from arden.tool_call_metadata import DISPLAY_TITLE_ARG
from arden.tools.automation import automation_create_tool, loop_done_tool, loop_schedule_wakeup_tool
from arden.tools.background import agent_cancel_tool
from arden.tools.bash import bash_tool, is_blocked_command
from arden.tools.core import EmptyInput, Tool, ToolCall, ToolNext, tool
from arden.tools.core.background_tasks import BackgroundTaskRegistry
from arden.tools.core.context import (
    ApprovalControls,
    IOBridge,
    RunContext,
    ToolContext,
    ToolExecution,
)
from arden.tools.core.registry import ToolRegistry
from arden.tools.core.types import (
    APPROVAL_WAIVED,
    ApprovalInfo,
    ApprovalWaived,
    ToolAction,
    ToolOverrideDecision,
    ToolPlacement,
    ToolPolicy,
    ToolScope,
)
from arden.tools.discover import discover_user_tools
from arden.tools.executor import ToolExecutor
from arden.tools.notify import notify_tool
from arden.tools.todos import todo_update_tool
from arden.tools.workflow import workflow_tool

READ_INTERNAL_POLICY = ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL)
WRITE_INTERNAL_APPROVAL_POLICY = ToolPolicy(
    action=ToolAction.WRITE,
    scope=ToolScope.INTERNAL,
    requires_approval=True,
)


def test_tool_result_failure_has_typed_outcome():
    result = ToolResult.failure(
        code="invalid_arguments",
        message="Arguments did not match the schema.",
        preview="Validation error",
        recovery_action="Retry with the required fields.",
    )

    assert result.is_error is True
    assert result.outcome is not None
    assert result.outcome.status == ToolOutcomeStatus.FAILED
    assert result.outcome.to_dict() == {
        "status": "failed",
        "error": {
            "code": "invalid_arguments",
            "retryable": False,
            "recovery_action": "Retry with the required fields.",
        },
    }


def test_tool_outcome_serializes_effect_receipt_and_verification():
    outcome = ToolOutcome(
        status=ToolOutcomeStatus.SUCCEEDED,
        effect=ToolEffect(operation="send", target="message:42", before_ref="draft:1", after_ref="sent:42"),
        verification=ToolVerification(
            postcondition="Message is visible in the destination",
            observed="message:42 fetched successfully",
            confidence=1.0,
        ),
        receipt="provider-receipt-42",
    )

    assert outcome.to_dict() == {
        "status": "succeeded",
        "effect": {
            "operation": "send",
            "target": "message:42",
            "before_ref": "draft:1",
            "after_ref": "sent:42",
        },
        "verification": {
            "postcondition": "Message is visible in the destination",
            "observed": "message:42 fetched successfully",
            "confidence": 1.0,
        },
        "receipt": "provider-receipt-42",
    }


def test_tool_policy_model_defaults():
    policy = ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL)

    assert policy.action == ToolAction.READ
    assert policy.scope == ToolScope.INTERNAL
    assert policy.requires_approval is False
    assert policy.requires_user_interaction is False
    assert policy.permissions == frozenset()
    assert policy.timeout_seconds is None
    assert policy.audit is True
    assert policy.max_result_chars is None
    assert policy.offload is True
    assert policy.allow_approval_bypass is True
    assert policy.destructive is None
    assert policy.open_world is None
    assert policy.idempotent is None


def test_function_tool_metadata_exposes_policy():
    async def handler(execution, args):
        return ToolResult(content="ok")

    t = tool(
        description="Reads internal state.",
        execute=handler,
        policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL),
    )

    metadata = t.get_metadata("read_state")

    assert metadata["policy"] == {
        "action": "read",
        "scope": "internal",
        "placement": "server",
        "requires_approval": False,
        "requires_user_interaction": False,
        "approval_mode": "never",
        "deferred": False,
        "permissions": [],
        "timeout_seconds": None,
        "audit": True,
        "max_result_chars": None,
        "offload": True,
        "allow_approval_bypass": True,
        "concurrency_group": None,
        "destructive": None,
        "open_world": None,
        "idempotent": None,
    }


def test_tool_metadata_can_use_compact_display_copy_without_changing_agent_docs():
    async def handler(execution, args):
        return ToolResult(content="ok")

    agent_description = (
        "Create an automation with detailed trigger, scheduling, approval, model, "
        "and execution guidance intended for the agent."
    )
    display_description = "Create a task that runs automatically on a schedule or event."
    t = tool(
        description=agent_description,
        display_description=display_description,
        execute=handler,
        policy=ToolPolicy(action=ToolAction.WRITE, scope=ToolScope.INTERNAL),
    )

    assert t.get_metadata("automation_create")["description"] == display_description
    assert t.to_dict("automation_create")["function"]["description"] == agent_description
    registry = ToolRegistry(tool_overrides={"automation_create": ToolOverrideDecision.ASK})
    registry.register("automation_create", t, source="_automation")
    assert registry.get_metadata()[0]["description"] == display_description


def test_create_automation_metadata_uses_compact_settings_copy():
    assert automation_create_tool.get_metadata("automation_create")["description"] == (
        "Create a task that runs automatically on a schedule or event."
    )
    assert automation_create_tool.to_dict("automation_create")["function"]["description"].startswith(
        "Create an automation — a task the agent runs autonomously."
    )


def test_registered_tool_metadata_fits_compact_settings_rows():
    metadata = ToolExecutor().get_tool_metadata()
    oversized = {
        item["name"]: item["description"]
        for item in metadata
        if item.get("source") not in {"mcp", "user"} and (len(item["description"]) > 100 or "\n" in item["description"])
    }

    assert oversized == {}


def test_all_registered_tools_have_policy():
    executor = ToolExecutor()
    for name, tool_obj in executor.registry.tools.items():
        assert isinstance(tool_obj.policy, ToolPolicy), name


def test_workflow_tool_is_execute_policy():
    assert workflow_tool.policy.action == ToolAction.EXECUTE


def test_tool_overrides_hide_deny_and_patch_approval_policy():
    async def handler(execution, args):
        return ToolResult(content="ok")

    async def approval(execution, args):
        return ApprovalInfo(description="Write state", preview="ok", diff=None)

    registry = ToolRegistry(
        tool_overrides={
            "read_state": ToolOverrideDecision.ASK,
            "write_state": ToolOverrideDecision.APPROVE,
            "blocked": ToolOverrideDecision.DENY,
        }
    )
    _register_tools(
        registry,
        {
            "read_state": tool(
                description="Read state.",
                execute=handler,
                policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL),
            ),
            "write_state": tool(
                description="Write state.",
                execute=handler,
                policy=ToolPolicy(
                    action=ToolAction.WRITE,
                    scope=ToolScope.INTERNAL,
                    requires_approval=True,
                    destructive=False,
                    open_world=False,
                    idempotent=True,
                ),
                approval=approval,
            ),
            "blocked": tool(
                description="Blocked.",
                execute=handler,
                policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL),
            ),
        },
    )

    schemas = registry.get_schemas()
    names = {s["function"]["name"] for s in schemas}
    metadata = {item["name"]: item for item in registry.get_metadata()}

    assert names == {"read_state", "write_state"}
    assert metadata["read_state"]["policy"]["requires_approval"] is True
    assert metadata["read_state"]["override"] == "ask"
    assert metadata["write_state"]["policy"]["requires_approval"] is False
    assert metadata["write_state"]["policy"]["destructive"] is False
    assert metadata["write_state"]["policy"]["open_world"] is False
    assert metadata["write_state"]["policy"]["idempotent"] is True
    assert metadata["write_state"]["override"] == "approve"
    assert metadata["blocked"]["override"] == "deny"


@pytest.mark.asyncio
async def test_tool_override_deny_blocks_execution():
    async def handler(execution, args):
        return ToolResult(content="should not run")

    registry = ToolRegistry(tool_overrides={"blocked": ToolOverrideDecision.DENY})
    _register_tools(
        registry,
        {
            "blocked": tool(
                description="Blocked.",
                execute=handler,
                policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL),
            )
        },
    )

    result = await registry.execute("blocked", _make_execution("blocked"), {})

    assert result.is_error is True
    assert result.preview == "Denied by settings"
    assert result.outcome is not None
    assert result.outcome.status == ToolOutcomeStatus.DENIED
    assert result.outcome.error is not None
    assert result.outcome.error.code == "permission_denied"


@pytest.mark.asyncio
async def test_tool_execution_enforces_required_runtime_capabilities():
    called = False

    async def handler(execution, args):
        nonlocal called
        called = True
        return ToolResult(content="should not run")

    registry = ToolRegistry()
    _register_tools(
        registry,
        {
            "write_state": tool(
                description="Write configured state.",
                execute=handler,
                policy=ToolPolicy(
                    action=ToolAction.WRITE,
                    scope=ToolScope.INTERNAL,
                    permissions=frozenset({"configured_store"}),
                ),
            )
        },
    )

    result = await registry.execute("write_state", _make_execution("write_state"), {})

    assert called is False
    assert result.is_error is True
    assert result.preview == "Tool unavailable"
    assert result.outcome is not None
    assert result.outcome.status == ToolOutcomeStatus.DENIED
    assert result.outcome.error is not None
    assert result.outcome.error.code == "permission_denied"


@pytest.mark.asyncio
async def test_tool_execution_allows_present_runtime_capabilities():
    async def handler(execution, args):
        return ToolResult(content="ran", preview="ran")

    registry = ToolRegistry()
    _register_tools(
        registry,
        {
            "read_state": tool(
                description="Read configured state.",
                execute=handler,
                policy=ToolPolicy(
                    action=ToolAction.READ,
                    scope=ToolScope.INTERNAL,
                    permissions=frozenset({"configured_store"}),
                ),
            )
        },
    )
    execution = _make_execution("read_state")
    execution.ctx.services["configured_store"] = object()

    result = await registry.execute("read_state", execution, {})

    assert result.content == "ran"


@pytest.mark.asyncio
async def test_tool_override_ask_headless_bypasses_skip_approvals():
    """ASK override + headless (no UI) + skip_approvals → bypasses (fires)."""
    registry = ToolRegistry(tool_overrides={"read_state": ToolOverrideDecision.ASK})
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=registry,
        run=RunContext(run_id="run-1", approval_controls=ApprovalControls(skip_approvals=True)),
        io=IOBridge(),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
    )

    rejection = await ToolExecution(tool_id="call-1", tool_name="read_state", ctx=ctx).request_approval("Approve")

    assert rejection is None


@pytest.mark.asyncio
async def test_tool_override_ask_with_ui_connected_still_requires_approval():
    """ASK override + UI connected + skip_approvals → still blocks (does NOT bypass)."""
    registry = ToolRegistry(tool_overrides={"read_state": ToolOverrideDecision.ASK})
    pending: dict[str, asyncio.Future] = {}

    async def emit(event):
        pass

    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=registry,
        run=RunContext(run_id="run-1", approval_controls=ApprovalControls(skip_approvals=True)),
        io=IOBridge(emit=emit, pending_approvals=pending, approval_timeout_seconds=0.001),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
    )

    rejection = await ToolExecution(tool_id="call-1", tool_name="read_state", ctx=ctx).request_approval("Approve")

    # Did not short-circuit at the bypass — went on to await approval and timed out.
    assert rejection is not None
    assert rejection.feedback == "Approval timed out"


@pytest.mark.asyncio
async def test_tool_override_deny_blocks_in_headless_run():
    """DENY is enforced upstream (registry.execute), independent of skip_approvals."""

    async def handler(execution, args):
        return ToolResult(content="should not run")

    registry = ToolRegistry(tool_overrides={"blocked": ToolOverrideDecision.DENY})
    _register_tools(
        registry,
        {
            "blocked": tool(
                description="Blocked.",
                execute=handler,
                policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL),
            )
        },
    )
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=registry,
        run=RunContext(run_id="run-1", approval_controls=ApprovalControls(skip_approvals=True)),
        io=IOBridge(),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
    )

    result = await registry.execute("blocked", ToolExecution(tool_id="call-1", tool_name="blocked", ctx=ctx), {})

    assert result.is_error is True
    assert result.preview == "Denied by settings"


@pytest.mark.asyncio
async def test_non_overridden_tool_skip_approvals_bypasses():
    """Regression guard: non-overridden tool + skip_approvals → bypasses."""
    registry = ToolRegistry()
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=registry,
        run=RunContext(run_id="run-1", approval_controls=ApprovalControls(skip_approvals=True)),
        io=IOBridge(),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
    )

    rejection = await ToolExecution(tool_id="call-1", tool_name="some_tool", ctx=ctx).request_approval("Approve")

    assert rejection is None


@pytest.mark.asyncio
async def test_bash_cannot_bypass_approval_in_headless_run():
    registry = ToolRegistry()
    _register_tools(registry, {"bash": bash_tool})
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=registry,
        run=RunContext(run_id="run-1", approval_controls=ApprovalControls(skip_approvals=True)),
        io=IOBridge(),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
    )

    rejection = await ToolExecution(tool_id="call-1", tool_name="bash", ctx=ctx).request_approval("Run shell command")

    assert rejection is not None
    assert rejection.feedback == "No UI connected — cannot approve"


def _make_execution(tool_name: str = "test") -> ToolExecution:
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=ToolRegistry(),
        run=RunContext(run_id="run-1"),
        io=IOBridge(),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
    )
    return ToolExecution(tool_id="t1", tool_name=tool_name, ctx=ctx)


def _make_tool_context(registry: ToolRegistry, session_id: str = "test") -> ToolContext:
    return ToolContext(
        session_state=SessionState(session_id=session_id, started_at=datetime.now(UTC)),
        registry=registry,
        run=RunContext(run_id="run-1"),
        io=IOBridge(),
        background_tasks=BackgroundTaskRegistry(session_id=session_id),
    )


@pytest.fixture(autouse=True)
def _isolate_result_files(tmp_path, monkeypatch):
    import arden.core.tool_result_files as trf
    import arden.tools.research_artifacts as research_artifacts

    monkeypatch.setattr(trf, "RESULTS_BASE", tmp_path / "tool-results")
    monkeypatch.setattr(research_artifacts, "ARDEN_DIR", tmp_path / ".arden")


@pytest_asyncio.fixture
async def session_store(tmp_path: Path):
    conn = await database.connect(tmp_path / "sessions.db")
    read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
    store = SessionStore(conn, read_conn, event_conn=conn)
    await store.init_schema()
    yield store
    await read_conn.close()
    await conn.close()


def _register_tools(registry: ToolRegistry, tools: dict[str, Tool]) -> None:
    for name, candidate in tools.items():
        registry.register(name, candidate)


# --- Bash safety checks ---


def test_blocked_commands():
    assert is_blocked_command("rm -rf /")
    assert is_blocked_command("rm -rf ~")
    assert is_blocked_command("dd if=/dev/zero")
    assert not is_blocked_command("ls -la")
    assert not is_blocked_command("echo hello")


# --- Bash tool ---
# bash is client-placed: the device executor runs the command. The Python
# body only refuses when no client execution backend is wired.


@pytest.mark.asyncio
async def test_bash_tool_refuses_in_process_execution():
    execution = _make_execution("bash")
    result = await bash_tool.execute(execution, command="echo test123")
    assert result.is_error
    assert result.outcome is not None and result.outcome.error is not None
    assert result.outcome.error.code == "no_client_execution"


def test_bash_tool_is_client_placed():
    assert bash_tool.policy.placement == ToolPlacement.CLIENT


@pytest.mark.asyncio
async def test_update_todos_tool_emits_todo_event():
    emitted = []

    async def emit(event):
        emitted.append(event)

    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=ToolRegistry(),
        run=RunContext(run_id="run-1"),
        io=IOBridge(emit=emit),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
    )
    execution = ToolExecution(tool_id="call-todos", tool_name="todo_update", ctx=ctx)

    result = await todo_update_tool.execute(
        execution,
        explanation="Track the rollout.",
        items=[
            {"content": "Research prior art", "status": "completed"},
            {"content": "Implement server tool", "status": "in_progress"},
            {"content": "Polish desktop UI", "status": "pending"},
        ],
    )

    assert result.preview == "1/3 done"
    assert result.data == {
        "items": [
            {"content": "Research prior art", "status": "completed"},
            {"content": "Implement server tool", "status": "in_progress"},
            {"content": "Polish desktop UI", "status": "pending"},
        ],
        "explanation": "Track the rollout.",
    }
    assert [event.type.value for event in emitted] == ["todo_updated"]
    assert emitted[0].run_id == "run-1"
    assert emitted[0].tool_call_id == "call-todos"


@pytest.mark.asyncio
async def test_update_todos_rejects_multiple_in_progress_items():
    registry = ToolRegistry()
    _register_tools(registry, {"todo_update": todo_update_tool})

    result = await registry.execute(
        "todo_update",
        _make_execution("todo_update"),
        {
            "items": [
                {"content": "First", "status": "in_progress"},
                {"content": "Second", "status": "in_progress"},
            ],
        },
    )

    assert result.is_error
    assert result.preview == "Validation error"
    assert result.outcome is not None
    assert result.outcome.error is not None
    assert result.outcome.error.code == "invalid_arguments"


@pytest.mark.asyncio
async def test_tool_validation_rejects_unknown_arguments_even_for_legacy_models():
    class LegacyInput(BaseModel):
        value: str

    called = False

    async def execute(execution: ToolExecution, args: LegacyInput) -> ToolResult:
        nonlocal called
        called = True
        return ToolResult(content=args.value, preview=args.value)

    registry = ToolRegistry()
    registry.register(
        "legacy",
        tool(
            description="Legacy model.",
            input_model=LegacyInput,
            execute=execute,
            policy=READ_INTERNAL_POLICY,
        ),
    )

    result = await registry.execute(
        "legacy",
        _make_execution("legacy"),
        {"value": "expected", "ignored_typo": "must fail"},
    )

    assert called is False
    assert result.is_error
    assert result.outcome is not None and result.outcome.error is not None
    assert result.outcome.error.code == "invalid_arguments"
    assert "ignored_typo" in result.content


# --- Function tools ---


@pytest.mark.asyncio
async def test_function_tool_execute_without_args():
    async def current_time(execution: ToolExecution, args: EmptyInput) -> ToolResult:
        assert execution.tool_name == "current_time"
        return ToolResult(content="now", preview="now")

    registry = ToolRegistry()
    _register_tools(
        registry,
        {
            "current_time": tool(
                description="Get the current time.",
                execute=current_time,
                policy=READ_INTERNAL_POLICY,
            )
        },
    )

    result = await registry.execute("current_time", _make_execution("current_time"), {})

    assert result == ToolResult(content="now", preview="now")
    schema = registry.get("current_time").to_dict("current_time")["function"]
    assert schema["name"] == "current_time"
    assert schema["description"] == "Get the current time."
    # The optional UI action-title hint is injected into every tool's schema;
    # this no-arg tool therefore exposes just the namespaced key. It is stripped before
    # execute(), so the tool itself never receives it.
    assert set(schema["parameters"]["properties"]) == {DISPLAY_TITLE_ARG}
    assert schema["parameters"]["required"] == []


class EchoInput(BaseModel):
    text: str = Field(min_length=1, description="Text to echo.")


class NestedFilter(BaseModel):
    model_config = {"extra": "forbid"}

    score: int = Field(ge=1, le=10)


class ComplexInput(BaseModel):
    model_config = {"extra": "forbid"}

    filter: NestedFilter
    mode: str | int


def test_function_tool_preserves_complete_normalized_schema():
    async def handler(execution, args):
        return ToolResult(content="ok", preview="ok")

    value = tool(
        description="Complex input.",
        input_model=ComplexInput,
        execute=handler,
        policy=READ_INTERNAL_POLICY,
    ).to_dict("complex")["function"]["parameters"]

    assert "$defs" not in value
    assert "$ref" not in str(value)
    assert value["additionalProperties"] is False
    assert value["properties"]["filter"]["additionalProperties"] is False
    assert value["properties"]["filter"]["properties"]["score"]["minimum"] == 1
    assert value["properties"]["filter"]["properties"]["score"]["maximum"] == 10
    assert value["properties"]["mode"]["anyOf"] == [{"type": "string"}, {"type": "integer"}]


@pytest.mark.asyncio
async def test_function_tool_execute_with_pydantic_args():
    async def echo(execution: ToolExecution, args: EchoInput) -> ToolResult:
        assert execution.tool_name == "echo"
        return ToolResult(content=f"echo: {args.text}", preview=args.text)

    registry = ToolRegistry()
    _register_tools(
        registry,
        {
            "echo": tool(
                description="Echo text.",
                input_model=EchoInput,
                execute=echo,
                policy=READ_INTERNAL_POLICY,
            )
        },
    )

    result = await registry.execute("echo", _make_execution("echo"), {"text": "hello"})

    assert result.content == "echo: hello"
    assert result.preview == "hello"
    schema = registry.get("echo").to_dict("echo")
    assert schema["function"]["parameters"]["properties"]["text"]["description"] == "Text to echo."
    assert schema["function"]["parameters"]["required"] == ["text"]


@pytest.mark.asyncio
async def test_function_tool_uses_registry_validation():
    async def echo(execution: ToolExecution, args: EchoInput) -> ToolResult:
        return ToolResult(content=args.text, preview=args.text)

    registry = ToolRegistry()
    _register_tools(
        registry,
        {
            "echo": tool(
                description="Echo text.",
                input_model=EchoInput,
                execute=echo,
                policy=READ_INTERNAL_POLICY,
            )
        },
    )

    result = await registry.execute("echo", _make_execution("echo"), {"text": ""})

    assert result.is_error
    assert result.preview == "Validation error"
    assert "Invalid arguments" in result.content


@pytest.mark.asyncio
async def test_function_tool_supports_approval_callback():
    async def approve(execution: ToolExecution, args: EchoInput) -> ApprovalInfo:
        return ApprovalInfo(description=f"Echo {args.text}", preview=args.text, diff=None)

    async def echo(execution: ToolExecution, args: EchoInput) -> ToolResult:
        return ToolResult(content=args.text, preview=args.text)

    registry = ToolRegistry()
    _register_tools(
        registry,
        {
            "echo": tool(
                description="Echo text.",
                input_model=EchoInput,
                execute=echo,
                approval=approve,
                policy=WRITE_INTERNAL_APPROVAL_POLICY,
            )
        },
    )

    result = await registry.execute("echo", _make_execution("echo"), {"text": "hello"})

    assert result.preview == "Rejected"
    assert result.content == "User rejected this action and said: No UI connected — cannot approve"


@pytest.mark.asyncio
async def test_function_tool_preflight_runs_before_approval_and_execution():
    events: list[str] = []

    async def preflight(execution: ToolExecution, args: EchoInput) -> ToolResult:
        events.append("preflight")
        return ToolResult.failure(
            code="precondition_required",
            message="Read first.",
            preview="Read required",
        )

    async def approve(execution: ToolExecution, args: EchoInput) -> ApprovalInfo:
        events.append("approval")
        return ApprovalInfo(description="Echo", preview=args.text, diff=None)

    async def echo(execution: ToolExecution, args: EchoInput) -> ToolResult:
        events.append("execute")
        return ToolResult(content=args.text, preview=args.text)

    registry = ToolRegistry(tool_overrides={"echo": ToolOverrideDecision.ASK})
    _register_tools(
        registry,
        {
            "echo": tool(
                description="Echo text.",
                input_model=EchoInput,
                execute=echo,
                preflight=preflight,
                approval=approve,
                policy=WRITE_INTERNAL_APPROVAL_POLICY,
            )
        },
    )

    result = await registry.execute("echo", _make_execution("echo"), {"text": "hello"})

    assert result.outcome is not None and result.outcome.error is not None
    assert result.outcome.error.code == "precondition_required"
    assert events == ["preflight"]


def test_function_tool_requires_explicit_approval_preview_callback():
    async def echo(execution: ToolExecution, args: EchoInput) -> ToolResult:
        return ToolResult(content=args.text, preview=args.text)

    with pytest.raises(ValueError, match="approval preview"):
        tool(
            description="Echo text.",
            input_model=EchoInput,
            execute=echo,
            policy=WRITE_INTERNAL_APPROVAL_POLICY,
        )


@pytest.mark.asyncio
async def test_approval_callback_that_cannot_preview_fails_closed():
    async def approve(execution: ToolExecution, args: EchoInput) -> None:
        return None

    async def echo(execution: ToolExecution, args: EchoInput) -> ToolResult:
        raise AssertionError("tool must not execute without a preview")

    registry = ToolRegistry()
    _register_tools(
        registry,
        {
            "echo": tool(
                description="Echo text.",
                input_model=EchoInput,
                execute=echo,
                approval=approve,
                policy=WRITE_INTERNAL_APPROVAL_POLICY,
            )
        },
    )

    result = await registry.execute("echo", _make_execution("echo"), {"text": "hello"})

    assert result.is_error
    assert result.outcome is not None and result.outcome.error is not None
    assert result.outcome.error.code == "approval_preview_unavailable"


@pytest.mark.asyncio
async def test_approval_callback_can_waive_this_call():
    """APPROVAL_WAIVED short-circuits before request_approval, so the tool runs
    with no UI wired — which returning None (no safe preview) never does."""

    async def approve(execution: ToolExecution, args: EchoInput) -> ApprovalWaived:
        return APPROVAL_WAIVED

    async def echo(execution: ToolExecution, args: EchoInput) -> ToolResult:
        return ToolResult(content=args.text, preview=args.text)

    registry = ToolRegistry()
    _register_tools(
        registry,
        {
            "echo": tool(
                description="Echo text.",
                input_model=EchoInput,
                execute=echo,
                approval=approve,
                policy=WRITE_INTERNAL_APPROVAL_POLICY,
            )
        },
    )

    result = await registry.execute("echo", _make_execution("echo"), {"text": "hello"})

    assert not result.is_error
    assert result.content == "hello"


@pytest.mark.asyncio
async def test_bash_approval_always_describes_command_and_working_directory():
    execution = _make_execution("bash")

    approval = await bash_tool.approval_info(execution, command="echo hello", working_dir="/tmp")

    assert approval is not None
    assert approval.preview == "Command: echo hello\nCWD: /tmp"


@pytest.mark.asyncio
async def test_tool_approval_timeout_expires_pending_request():
    async def echo(execution: ToolExecution, args: EchoInput) -> ToolResult:
        return ToolResult(content=args.text, preview=args.text)

    async def approve_echo(execution: ToolExecution, args: EchoInput) -> ApprovalInfo:
        return ApprovalInfo(description="Echo text", preview=args.text, diff=None)

    registry = ToolRegistry()
    _register_tools(
        registry,
        {
            "echo": tool(
                description="Echo text.",
                input_model=EchoInput,
                execute=echo,
                policy=WRITE_INTERNAL_APPROVAL_POLICY,
                approval=approve_echo,
            )
        },
    )
    emitted = []
    recorded = []
    resolved = []
    pending: dict[str, asyncio.Future] = {}

    async def emit(event):
        emitted.append(event)

    async def record_approval(**kwargs):
        recorded.append(kwargs)

    async def resolve_approval(**kwargs):
        resolved.append(kwargs)

    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=registry,
        run=RunContext(run_id="run-1"),
        io=IOBridge(
            emit=emit,
            pending_approvals=pending,
            record_approval=record_approval,
            resolve_approval=resolve_approval,
            approval_timeout_seconds=0.001,
        ),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
    )
    execution = ToolExecution(tool_id="call-1", tool_name="echo", ctx=ctx)

    rejection = await execution.request_approval("Echo hello", preview="hello")

    assert rejection is not None
    assert rejection.feedback == "Approval timed out"
    assert pending == {}
    assert len(emitted) == 1
    assert recorded[0]["action"] == "write"
    assert recorded[0]["scope"] == "internal"
    assert recorded[0]["expires_at"] is not None
    assert resolved == [
        {
            "run_id": "run-1",
            "tool_call_id": "call-1",
            "status": "expired",
            "result_feedback": "Approval timed out",
            "source": "timeout",
        }
    ]


@pytest.mark.asyncio
async def test_tool_approval_cancellation_resolves_pending_request():
    emitted = []
    recorded = []
    resolved = []
    pending: dict[str, asyncio.Future] = {}

    async def emit(event):
        emitted.append(event)

    async def record_approval(**kwargs):
        recorded.append(kwargs)

    async def resolve_approval(**kwargs):
        resolved.append(kwargs)

    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=ToolRegistry(),
        run=RunContext(run_id="run-1"),
        io=IOBridge(
            emit=emit,
            pending_approvals=pending,
            record_approval=record_approval,
            resolve_approval=resolve_approval,
        ),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
    )
    execution = ToolExecution(tool_id="call-1", tool_name="echo", ctx=ctx)

    task = asyncio.create_task(execution.request_approval("Echo hello", preview="hello"))
    for _ in range(20):
        if "call-1" in pending and emitted:
            break
        await asyncio.sleep(0)

    assert "call-1" in pending
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert pending == {}
    assert len(recorded) == 1
    assert resolved == [
        {
            "run_id": "run-1",
            "tool_call_id": "call-1",
            "status": "cancelled",
            "result_feedback": "Approval cancelled",
            "source": "cancel",
        }
    ]


@pytest.mark.asyncio
async def test_tool_approval_record_failure_fails_closed_before_emit():
    emitted = []
    resolved = []
    pending: dict[str, asyncio.Future] = {}

    async def emit(event):
        emitted.append(event)

    async def record_approval(**kwargs):
        raise RuntimeError("store write failed")

    async def resolve_approval(**kwargs):
        resolved.append(kwargs)

    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=ToolRegistry(),
        run=RunContext(run_id="run-1"),
        io=IOBridge(
            emit=emit,
            pending_approvals=pending,
            record_approval=record_approval,
            resolve_approval=resolve_approval,
            approval_timeout_seconds=0.001,
        ),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
    )
    execution = ToolExecution(tool_id="call-1", tool_name="echo", ctx=ctx)

    rejection = await execution.request_approval("Echo hello", preview="hello")

    assert rejection is not None
    assert rejection.feedback == "Approval could not be recorded — action cancelled"
    result = rejection.to_result()
    assert result.outcome is not None and result.outcome.error is not None
    assert result.outcome.error.code == "approval_persistence_failed"
    assert result.outcome.error.diagnostic_ref
    assert pending == {}
    assert emitted == []
    assert resolved == []


@pytest.mark.asyncio
async def test_tool_approval_resolve_failure_after_approval_fails_closed():
    emitted = []
    pending: dict[str, asyncio.Future] = {}

    async def emit(event):
        emitted.append(event)

    async def resolve_approval(**kwargs):
        raise RuntimeError("store write failed")

    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=ToolRegistry(),
        run=RunContext(run_id="run-1"),
        io=IOBridge(
            emit=emit,
            pending_approvals=pending,
            resolve_approval=resolve_approval,
        ),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
    )
    execution = ToolExecution(tool_id="call-1", tool_name="echo", ctx=ctx)

    task = asyncio.create_task(execution.request_approval("Echo hello", preview="hello"))
    for _ in range(20):
        if "call-1" in pending and emitted:
            break
        await asyncio.sleep(0)

    pending["call-1"].set_result({"approved": True, "result": "ok"})

    rejection = await task
    assert rejection is not None
    assert rejection.feedback == "Approval could not be persisted — action cancelled"
    result = rejection.to_result()
    assert result.outcome is not None and result.outcome.error is not None
    assert result.outcome.error.code == "approval_persistence_failed"
    assert result.outcome.error.diagnostic_ref
    assert pending == {}


@pytest.mark.asyncio
async def test_approval_gated_tools_render_the_payload():
    execution = _make_execution()

    notify = await notify_tool.approval_info(
        execution,
        subject="Build failed",
        body="The release build failed in the signing step.",
        names=["work-telegram"],
    )
    wakeup = await loop_schedule_wakeup_tool.approval_info(execution, delay_seconds=120)
    done = await loop_done_tool.approval_info(execution, reason="The requested condition is satisfied")
    cancel = await agent_cancel_tool.approval_info(execution, agent_ref="agent~abc123")

    assert notify is not None
    assert notify.description == "Notify via work-telegram: Build failed"
    assert notify.preview == "The release build failed in the signing step."
    assert wakeup is not None and wakeup.preview == "Delay: 120 seconds"
    assert done is not None and done.preview == "The requested condition is satisfied"
    assert cancel is not None and cancel.preview == "Agent: agent~abc123"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "resolution", "feedback"),
    [
        ("approved", {"approved": True, "result": "ok"}, None),
        ("rejected", {"approved": False, "result": "no"}, "no"),
    ],
)
async def test_tool_approval_reuses_durable_resolution(status, resolution, feedback):
    emitted = []
    pending: dict[str, asyncio.Future] = {}

    async def emit(event):
        emitted.append(event)

    async def get_suspension(**kwargs):
        return {"status": status, "resolution": resolution}

    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=ToolRegistry(),
        run=RunContext(run_id="run-1"),
        io=IOBridge(
            emit=emit,
            pending_approvals=pending,
            get_suspension=get_suspension,
        ),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
    )

    rejection = await ToolExecution(tool_id="call-1", tool_name="echo", ctx=ctx).request_approval("Echo hello")

    assert (rejection.feedback if rejection else None) == feedback
    assert emitted == []
    assert pending == {}


@pytest.mark.asyncio
async def test_durable_approval_marks_suspension_consumed_before_execution():
    consumed = []

    async def get_suspension(**kwargs):
        return {"status": "approved", "resolution": {"approved": True, "result": "ok"}}

    async def consume_suspension(**kwargs):
        consumed.append(kwargs)

    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=ToolRegistry(),
        run=RunContext(run_id="run-1"),
        io=IOBridge(get_suspension=get_suspension, consume_suspension=consume_suspension),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
    )

    assert await ToolExecution(tool_id="call-1", tool_name="echo", ctx=ctx).request_approval("Echo") is None
    assert consumed == [{"run_id": "run-1", "suspension_id": "call-1"}]


@pytest.mark.asyncio
async def test_durable_approval_consume_failure_fails_closed():
    async def get_suspension(**kwargs):
        return {"status": "approved", "resolution": {"approved": True, "result": "ok"}}

    async def consume_suspension(**kwargs):
        raise RuntimeError("store write failed")

    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
        registry=ToolRegistry(),
        run=RunContext(run_id="run-1"),
        io=IOBridge(get_suspension=get_suspension, consume_suspension=consume_suspension),
        background_tasks=BackgroundTaskRegistry(session_id="test"),
    )

    rejection = await ToolExecution(tool_id="call-1", tool_name="echo", ctx=ctx).request_approval("Echo")

    assert rejection is not None
    assert rejection.feedback == "Approval could not be consumed — action cancelled"
    result = rejection.to_result()
    assert result.outcome is not None and result.outcome.error is not None
    assert result.outcome.error.code == "approval_persistence_failed"
    assert result.outcome.error.diagnostic_ref


@pytest.mark.asyncio
async def test_function_tool_rejects_non_tool_result_output():
    async def bad_output(execution: ToolExecution, args: EmptyInput) -> str:
        return "not a ToolResult"

    registry = ToolRegistry()
    _register_tools(
        registry,
        {
            "bad_output": tool(
                description="Return the wrong type.",
                execute=bad_output,
                policy=READ_INTERNAL_POLICY,
            )
        },
    )

    with pytest.raises(TypeError, match="function tool handlers must return ToolResult"):
        await registry.execute("bad_output", _make_execution("bad_output"), {})


@pytest.mark.asyncio
async def test_arden_tool_executor_policy_offload_false_keeps_retrieval_pointer():
    large_content = "x" * (OFFLOAD_THRESHOLD + 1)

    async def large_result(execution: ToolExecution, args: EmptyInput) -> ToolResult:
        return ToolResult(content=large_content, preview="large")

    registry = ToolRegistry()
    registry.register(
        "large_result",
        tool(
            description="Return a large result.",
            execute=large_result,
            policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL, offload=False),
        ),
    )
    executor = ArdenToolExecutor(ToolExecutor().with_registry(registry), _make_tool_context(registry))

    result = await executor.execute("large_result", {}, "call-1")

    assert result.content != large_content
    assert "file_read" in result.content
    assert result.data["truncated"] is True
    assert result.data["raw_ref"].startswith("sha256:")
    assert result.preview == "large"
    assert not result.is_error
    assert result.outcome is not None
    assert result.outcome.status == ToolOutcomeStatus.SUCCEEDED


def test_arden_tool_executor_bounds_recovered_result_before_model_history(tmp_path, monkeypatch):
    import arden.core.tool_result_files as trf

    monkeypatch.setattr(trf, "RESULTS_BASE", tmp_path / "tool-results")
    registry = ToolRegistry()
    executor = ArdenToolExecutor(
        ToolExecutor().with_registry(registry),
        _make_tool_context(registry, session_id="recovered-session"),
    )
    full = "recovered payload\n" * 10_000

    result = executor.prepare_recovered_result(
        ToolResult(content=full, preview="recovered"),
        "call-recovered",
    )

    assert full not in result.content
    assert "file_read" in result.content
    path = trf.result_payload_file_path("recovered-session", "call-recovered")
    assert path.exists()
    assert json.loads(path.read_text())["content"] == full
    assert result.data["truncated"] is True
    assert result.data["raw_ref"].startswith("sha256:")


@pytest.mark.asyncio
async def test_durable_large_result_recovers_without_nested_serialization(
    session_store: SessionStore,
    tmp_path,
    monkeypatch,
):
    import arden.core.raw_tool_results as raw_tool_results
    import arden.core.tool_result_files as trf

    monkeypatch.setattr(trf, "RESULTS_BASE", tmp_path / "tool-results")
    monkeypatch.setattr(raw_tool_results, "RAW_TOOL_RESULTS_BASE", tmp_path / "blobs")
    large_content = "durable recovery evidence\n" * 10_000
    continuation = {"has_more": True, "next_offset": 200}
    source_ref = ToolSourceRef(
        provider="wiki",
        kind="page",
        ref="wiki:durable",
        title="Durable evidence",
    )

    async def large_result(execution: ToolExecution, args: EmptyInput) -> ToolResult:
        return ToolResult(
            content=large_content,
            preview="durable evidence",
            data=continuation,
            source_refs=(source_ref,),
        )

    registry = ToolRegistry()
    registry.register(
        "large_result",
        tool(
            description="Return a large durable result.",
            execute=large_result,
            policy=ToolPolicy(
                action=ToolAction.READ,
                scope=ToolScope.INTERNAL,
                audit=False,
                offload=True,
            ),
        ),
    )
    executor = ArdenToolExecutor(
        ToolExecutor().with_registry(registry),
        _make_tool_context(registry, session_id="recovered-session"),
    )
    bounded = await executor.execute("large_result", {}, "call-recovered")
    assert bounded.data is not None

    await session_store.record_tool_call_started(
        run_id="run-1",
        session_id="recovered-session",
        tool_call_id="call-recovered",
        tool_name="large_result",
        action="read",
        scope="internal",
    )
    await session_store.record_tool_call_finished(
        run_id="run-1",
        tool_call_id="call-recovered",
        status="success",
        result_preview=bounded.preview,
        outcome=bounded.outcome.to_dict() if bounded.outcome else None,
    )
    await session_store.events.record_session_event(
        StreamRecord(
            seq=1,
            session_id="recovered-session",
            event=ToolCallResultEvent(
                tool_call_id="call-recovered",
                name="large_result",
                content=bounded.content,
                preview=bounded.preview,
                data=bounded.data,
                outcome=bounded.outcome.to_dict() if bounded.outcome else None,
            ),
        )
    )
    trf.result_file_path("recovered-session", "call-recovered").unlink()
    trf.result_payload_file_path("recovered-session", "call-recovered").unlink()
    pending = PendingToolCall(
        tool_call=AgentToolCall(
            id="call-recovered",
            type="function",
            function=FunctionCall(name="large_result", arguments="{}"),
        ),
        name="large_result",
        args={},
    )

    recovered = await _recover_durable_tool_calls(
        session_store,
        "run-1",
        [pending],
        prepare_result=executor.prepare_recovered_result,
    )

    result = recovered["call-recovered"]
    assert large_content not in result.content
    assert "file_read" in result.content
    assert result.data is not None
    assert result.data["has_more"] is True
    assert result.data["next_offset"] == 200
    assert result.source_refs == (source_ref,)

    restored = ToolResult.from_serialized_payload(
        trf.result_payload_file_path("recovered-session", "call-recovered").read_text()
    )
    assert restored is not None
    assert restored.content == large_content
    assert restored.data == continuation
    assert restored.source_refs == (source_ref,)


@pytest.mark.asyncio
async def test_arden_tool_executor_bounds_large_structured_data():
    async def large_result(execution: ToolExecution, args: EmptyInput) -> ToolResult:
        return ToolResult(
            content="summary",
            preview="large",
            data={
                "rows": [{"body": "x" * 100_000}],
                "has_more": True,
                "next_offset": 100,
                "next_secret": "must-not-escape",
            },
        )

    registry = ToolRegistry()
    registry.register(
        "large_result",
        tool(
            description="Return large structured data.",
            execute=large_result,
            policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL, offload=False),
        ),
    )
    executor = ArdenToolExecutor(ToolExecutor().with_registry(registry), _make_tool_context(registry))

    result = await executor.execute("large_result", {}, "call-data")

    assert len(json.dumps(result.data)) < 5_000
    assert result.data["truncated"] is True
    assert result.data["raw_ref"].startswith("sha256:")
    assert result.data["has_more"] is True
    assert result.data["next_offset"] == 100
    assert "next_secret" not in result.data
    assert "file_read" in result.content
    emitted = json.dumps({"content": result.content, "data": result.data, "model_content": result.model_content})
    assert len(emitted) < 20_000


@pytest.mark.asyncio
async def test_arden_tool_executor_keeps_readable_raw_and_exact_payload_paths_distinct(tmp_path, monkeypatch):
    import arden.core.raw_tool_results as raw_tool_results
    import arden.core.tool_result_files as trf

    monkeypatch.setattr(trf, "RESULTS_BASE", tmp_path / "tool-results")
    monkeypatch.setattr(raw_tool_results, "RAW_TOOL_RESULTS_BASE", tmp_path / "blobs")
    large_content = "readable result\n" * 5_000
    continuation = {"has_more": True, "next_offset": 200}
    source_ref = ToolSourceRef(
        provider="wiki",
        kind="page",
        ref="wiki:offload",
        title="Offloaded evidence",
    )

    async def large_result(execution: ToolExecution, args: EmptyInput) -> ToolResult:
        return ToolResult(
            content=large_content,
            preview="large",
            data=continuation,
            source_refs=(source_ref,),
        )

    registry = ToolRegistry()
    registry.register(
        "large_result",
        tool(
            description="Return large content and structured data.",
            execute=large_result,
            policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL, offload=True),
        ),
    )
    executor = ArdenToolExecutor(
        ToolExecutor().with_registry(registry),
        _make_tool_context(registry, session_id="distinct-session"),
    )

    result = await executor.execute("large_result", {}, "call-distinct")

    raw_path = trf.result_file_path("distinct-session", "call-distinct")
    payload_path = trf.result_payload_file_path("distinct-session", "call-distinct")
    assert raw_path != payload_path
    assert raw_path.read_text() == large_content
    exact = ToolResult.from_serialized_payload(payload_path.read_text())
    assert exact is not None
    assert exact.content == large_content
    assert exact.data == continuation
    assert exact.source_refs == (source_ref,)
    assert str(raw_path) in result.content
    assert len(result.serialized_payload().encode("utf-8")) <= OFFLOAD_THRESHOLD


def test_tool_result_decoder_rejects_unmarked_envelope_shaped_raw_json():
    raw = json.dumps(
        {
            "content": "ordinary external JSON",
            "preview": "looks like an envelope",
            "is_error": False,
            "data": {},
            "model_content": [],
            "source_refs": [],
            "outcome": None,
        }
    )

    assert ToolResult.from_serialized_payload(raw) is None
    encoded = ToolResult(content=raw, preview="outer").serialized_payload()
    decoded = ToolResult.from_serialized_payload(encoded)
    assert decoded is not None
    assert decoded.content == raw


@pytest.mark.asyncio
async def test_double_offload_preserves_envelope_shaped_raw_json_as_content(tmp_path, monkeypatch):
    import arden.core.raw_tool_results as raw_tool_results
    import arden.core.tool_result_files as trf

    monkeypatch.setattr(trf, "RESULTS_BASE", tmp_path / "tool-results")
    monkeypatch.setattr(raw_tool_results, "RAW_TOOL_RESULTS_BASE", tmp_path / "blobs")
    raw = json.dumps(
        {
            "content": "inner content",
            "preview": "inner preview",
            "is_error": False,
            "data": {"body": "x" * 60_000},
            "model_content": [],
            "source_refs": [],
            "outcome": None,
        }
    )
    outer_data = {"duplicate": "y" * 60_000}

    async def large_result(execution: ToolExecution, args: EmptyInput) -> ToolResult:
        return ToolResult(content=raw, preview="outer preview", data=outer_data)

    registry = ToolRegistry()
    registry.register(
        "large_result",
        tool(
            description="Return envelope-shaped raw JSON.",
            execute=large_result,
            policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL, offload=True),
        ),
    )
    executor = ArdenToolExecutor(
        ToolExecutor().with_registry(registry),
        _make_tool_context(registry, session_id="collision-session"),
    )

    result = await executor.execute("large_result", {}, "call-collision")

    exact = ToolResult.from_serialized_payload(
        trf.result_payload_file_path("collision-session", "call-collision").read_text()
    )
    assert exact is not None
    assert exact.content == raw
    assert exact.preview == "outer preview"
    assert exact.data == outer_data
    assert len(result.serialized_payload().encode("utf-8")) <= OFFLOAD_THRESHOLD


def test_branch_path_rewrite_preserves_provider_sidecars_and_call_arguments(tmp_path, monkeypatch):
    import arden.core.tool_result_files as trf

    monkeypatch.setattr(trf, "RESULTS_BASE", tmp_path / "tool-results")
    source_session = "source"
    target_session = "branch"
    source_path = str(trf.result_file_path(source_session, "call-1"))
    target_path = str(trf.result_file_path(target_session, "call-1"))
    messages = [
        {
            "role": "assistant",
            "content": f"Provider text mentions {source_path}",
            "openai_response_items": [{"type": "message", "content": [{"text": source_path}]}],
            "anthropic_content": [{"type": "text", "text": source_path}],
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "file_read", "arguments": json.dumps({"path": source_path})},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call-1", "content": f"Read more at {source_path}"},
        {
            "role": "assistant",
            "content": f"[Session State Handoff]\nEvidence: {source_path}",
            "compaction": {"kind": "session_handoff", "note": source_path},
        },
    ]

    rewritten = trf.rewrite_result_paths(messages, source_session, target_session)

    assert rewritten[0] == messages[0]
    assert rewritten[1]["content"] == f"Read more at {target_path}"
    assert target_path in rewritten[2]["content"]
    assert rewritten[2]["compaction"]["note"] == target_path
    assert rewritten is not messages


@pytest.mark.asyncio
async def test_terminal_recovery_falls_back_to_exact_payload_file_without_manifest(
    session_store: SessionStore, tmp_path, monkeypatch
):
    import arden.core.raw_tool_results as raw_tool_results
    import arden.core.tool_result_files as trf

    monkeypatch.setattr(trf, "RESULTS_BASE", tmp_path / "tool-results")
    monkeypatch.setattr(raw_tool_results, "RAW_TOOL_RESULTS_BASE", tmp_path / "blobs")
    large_content = "recover me exactly\n" * 10_000

    async def large_result(execution: ToolExecution, args: EmptyInput) -> ToolResult:
        return ToolResult(content=large_content, preview="recoverable")

    registry = ToolRegistry()
    registry.register(
        "large_result",
        tool(
            description="Return a large result.",
            execute=large_result,
            policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL, audit=True, offload=False),
        ),
    )
    ctx = _make_tool_context(registry, session_id="recovery-session")
    ctx.services["store"] = session_store
    executor = ArdenToolExecutor(ToolExecutor().with_registry(registry), ctx)
    await executor.execute("large_result", {}, "call-recovery-fallback")
    assert (
        await session_store.events.get_tool_result_for_call(run_id="run-1", tool_call_id="call-recovery-fallback")
        is None
    )
    pending = PendingToolCall(
        tool_call=AgentToolCall(
            id="call-recovery-fallback",
            type="function",
            function=FunctionCall(name="large_result", arguments="{}"),
        ),
        name="large_result",
        args={},
    )

    recovered = await _recover_durable_tool_calls(
        session_store,
        "run-1",
        [pending],
        prepare_result=executor.prepare_recovered_result,
    )

    result = recovered["call-recovery-fallback"]
    assert large_content not in result.content
    assert "file_read" in result.content
    exact = ToolResult.from_serialized_payload(
        trf.result_payload_file_path("recovery-session", "call-recovery-fallback").read_text()
    )
    assert exact is not None
    assert exact.content == large_content


@pytest.mark.asyncio
async def test_terminal_recovery_rejects_untyped_manifest_content(session_store: SessionStore, tmp_path, monkeypatch):
    import arden.core.raw_tool_results as raw_tool_results

    monkeypatch.setattr(raw_tool_results, "RAW_TOOL_RESULTS_BASE", tmp_path / "blobs")
    blob = raw_tool_results.persist_raw_tool_result("legacy raw content")
    await session_store.record_tool_call_started(
        run_id="run-1",
        session_id="recovery-session",
        tool_call_id="call-untyped",
        tool_name="bash",
        action="read",
        scope="internal",
    )
    await session_store.events.record_session_event(
        StreamRecord(
            seq=1,
            session_id="recovery-session",
            event=ToolCallResultEvent(
                tool_call_id="call-untyped",
                name="bash",
                content="bounded preview",
                preview="bounded",
                data=blob.to_internal_data(),
            ),
        )
    )
    await session_store.record_tool_call_finished(
        run_id="run-1",
        tool_call_id="call-untyped",
        status="success",
        result_preview="bounded",
        outcome={"status": "succeeded"},
    )
    pending = PendingToolCall(
        tool_call=AgentToolCall(
            id="call-untyped",
            type="function",
            function=FunctionCall(name="bash", arguments="{}"),
        ),
        name="bash",
        args={},
    )

    recovered = await _recover_durable_tool_calls(session_store, "run-1", [pending])

    result = recovered["call-untyped"]
    assert result.outcome.status == ToolOutcomeStatus.UNCERTAIN
    assert result.outcome.error.code == "durable_result_corrupt"
    assert "legacy raw content" not in result.content


@pytest.mark.asyncio
async def test_arden_tool_executor_rejects_registered_tool_outside_run_allowlist():
    called = False

    async def hidden(execution: ToolExecution, args: EmptyInput) -> ToolResult:
        nonlocal called
        called = True
        return ToolResult(content="secret", preview="secret")

    registry = ToolRegistry()
    registry.register(
        "hidden",
        tool(
            description="Hidden tool.",
            execute=hidden,
            policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL),
        ),
    )
    ctx = _make_tool_context(registry)
    ctx.run.allowed_tool_names = {"visible"}
    executor = ArdenToolExecutor(ToolExecutor().with_registry(registry), ctx)

    result = await executor.execute("hidden", {}, "call-1")

    assert result.is_error
    assert result.preview == "Tool not allowed"
    assert result.outcome is not None
    assert result.outcome.status == ToolOutcomeStatus.DENIED
    assert result.outcome.error is not None
    assert result.outcome.error.code == "permission_denied"
    assert called is False


@pytest.mark.asyncio
async def test_arden_tool_executor_offload_clamps_single_long_line_preview():
    large_content = "x" * (OFFLOAD_THRESHOLD + 1)

    async def large_result(execution: ToolExecution, args: EmptyInput) -> ToolResult:
        return ToolResult(content=large_content, preview="large")

    registry = ToolRegistry()
    registry.register(
        "large_result",
        tool(
            description="Return a large result.",
            execute=large_result,
            policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL),
        ),
    )
    executor = ArdenToolExecutor(ToolExecutor().with_registry(registry), _make_tool_context(registry))

    result = await executor.execute("large_result", {}, "call-1")

    assert len(result.content) < OFFLOAD_PREVIEW_CHARS + 500
    assert "truncated" in result.content
    assert "saved to" in result.content
    assert "file_read" in result.content


@pytest.mark.asyncio
async def test_arden_tool_executor_policy_max_result_chars_truncates_before_model_result():
    async def long_error(execution: ToolExecution, args: EmptyInput) -> ToolResult:
        return ToolResult(content="abcdefghijklmnopqrstuvwxyz", preview="original", is_error=True)

    registry = ToolRegistry()
    registry.register(
        "long_error",
        tool(
            description="Return a long error.",
            execute=long_error,
            policy=ToolPolicy(
                action=ToolAction.READ,
                scope=ToolScope.INTERNAL,
                max_result_chars=10,
                offload=False,
            ),
        ),
    )
    executor = ArdenToolExecutor(ToolExecutor().with_registry(registry), _make_tool_context(registry))

    result = await executor.execute("long_error", {}, "call-1")

    assert result.content.startswith("abcdefghij... [truncated]")
    assert "file_read" in result.content
    assert result.data["truncated"] is True
    assert result.data["raw_ref"].startswith("sha256:")
    assert result.preview == "original"
    assert result.is_error


@pytest.mark.asyncio
async def test_arden_tool_executor_policy_timeout_seconds_returns_error_result():
    async def slow(execution: ToolExecution, args: EmptyInput) -> ToolResult:
        await asyncio.sleep(1)
        return ToolResult(content="late", preview="late")

    registry = ToolRegistry()
    registry.register(
        "slow",
        tool(
            description="Return too slowly.",
            execute=slow,
            policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL, timeout_seconds=0),
        ),
    )
    executor = ArdenToolExecutor(ToolExecutor().with_registry(registry), _make_tool_context(registry))

    result = await executor.execute("slow", {}, "call-1")

    assert result.content == "Tool call timed out."
    assert result.preview == "Timed out"
    assert result.outcome is not None
    assert result.outcome.error is not None
    assert result.outcome.error.code == "timeout"
    assert result.outcome.error.retryable is True


@pytest.mark.asyncio
async def test_arden_tool_executor_mutation_timeout_is_uncertain_and_not_retryable():
    async def slow(execution: ToolExecution, args: EmptyInput) -> ToolResult:
        await asyncio.sleep(1)
        return ToolResult(content="late", preview="late")

    registry = ToolRegistry()
    registry.register(
        "slow_write",
        tool(
            description="Mutate too slowly.",
            execute=slow,
            policy=ToolPolicy(action=ToolAction.WRITE, scope=ToolScope.EXTERNAL, timeout_seconds=0),
        ),
    )
    executor = ArdenToolExecutor(ToolExecutor().with_registry(registry), _make_tool_context(registry))

    result = await executor.execute("slow_write", {}, "call-write-timeout")

    assert result.outcome.status == ToolOutcomeStatus.UNCERTAIN
    assert result.outcome.error.code == "mutation_timeout"
    assert result.outcome.error.retryable is False
    assert "Verify provider state" in result.outcome.error.recovery_action


@pytest.mark.asyncio
async def test_arden_tool_executor_defaults_external_tool_timeout(monkeypatch):
    import arden.core.tool_executor as core_tool_executor_module

    monkeypatch.setattr(core_tool_executor_module, "DEFAULT_EXTERNAL_TOOL_TIMEOUT_SECONDS", 0.001)

    async def slow(execution: ToolExecution, args: EmptyInput) -> ToolResult:
        await asyncio.sleep(1)
        return ToolResult(content="late", preview="late")

    registry = ToolRegistry()
    registry.register(
        "slow_external",
        tool(
            description="Return too slowly.",
            execute=slow,
            policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.EXTERNAL),
        ),
    )
    executor = ArdenToolExecutor(ToolExecutor().with_registry(registry), _make_tool_context(registry))

    result = await executor.execute("slow_external", {}, "call-1")

    assert result.content == "Tool call timed out."
    assert result.preview == "Timed out"
    assert result.outcome is not None
    assert result.outcome.error is not None
    assert result.outcome.error.code == "timeout"


@pytest.mark.asyncio
async def test_arden_tool_executor_audits_policy_enabled_calls(session_store: SessionStore):
    async def echo(execution: ToolExecution, args: EchoInput) -> ToolResult:
        return ToolResult(content=f"echo: {args.text}", preview="ok")

    registry = ToolRegistry()
    registry.register(
        "echo",
        tool(
            description="Echo text.",
            input_model=EchoInput,
            execute=echo,
            policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL, audit=True),
        ),
    )
    ctx = _make_tool_context(registry, session_id="sess-1")
    ctx.services["store"] = session_store
    executor = ArdenToolExecutor(ToolExecutor().with_registry(registry), ctx)

    result = await executor.execute(
        "echo",
        {"text": "hello", DISPLAY_TITLE_ARG: "Echoing the message"},
        "call-1",
    )
    rows = await session_store.list_tool_calls(run_id="run-1")

    expected_hash = hashlib.sha256(
        json.dumps({"text": "hello"}, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    assert result.preview == "ok"
    assert len(rows) == 1
    assert rows[0]["tool_call_id"] == "call-1"
    assert rows[0]["tool_name"] == "echo"
    assert rows[0]["action"] == "read"
    assert rows[0]["scope"] == "internal"
    assert rows[0]["args_hash"] == expected_hash
    assert rows[0]["status"] == "success"
    assert rows[0]["result_preview"] == "ok"


@pytest.mark.asyncio
async def test_offloaded_result_persisted_to_file(session_store: SessionStore):
    import arden.core.tool_result_files as trf

    big = "data line\n" * 8000  # > OFFLOAD_THRESHOLD

    async def big_read(execution: ToolExecution, args: EmptyInput) -> ToolResult:
        return ToolResult(content=big, preview="big")

    registry = ToolRegistry()
    registry.register(
        "big_offload",
        tool(
            description="Return a big offloadable payload.",
            input_model=EmptyInput,
            execute=big_read,
            policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL, audit=True, offload=True),
        ),
    )
    ctx = _make_tool_context(registry, session_id="sess-1")
    ctx.services["store"] = session_store
    executor = ArdenToolExecutor(ToolExecutor().with_registry(registry), ctx)

    result = await executor.execute("big_offload", {}, "call-7")

    # full result saved to a durable file, recovered via the normal read_file tool by path
    assert "file_read" in result.content
    path = trf.result_file_path("sess-1", "call-7")
    assert str(path) in result.content
    assert path.exists()
    assert path.read_text() == big
    # Offload is session-scoped, not a global pile (the 5GB runaway came from a
    # flat global store).
    assert path.parent == trf.session_results_dir("sess-1")


def test_prune_offload_store_drops_old_keeps_recent(monkeypatch, tmp_path):
    import os
    import time

    import arden.core.tool_result_files as trf

    monkeypatch.setattr(trf, "RESULTS_BASE", tmp_path / "tool-results")
    old = trf.persist_result("old-sess", "call-old", "x" * 100)
    old_payload = trf.persist_result_payload("old-sess", "call-old", '{"content":"x"}')
    new = trf.persist_result("new-sess", "call-new", "y" * 100)
    past = time.time() - (trf.RESULTS_MAX_AGE_SECONDS + 3600)
    os.utime(old, (past, past))
    os.utime(old_payload, (past, past))

    removed = trf.prune_offload_store()

    assert removed == 2
    assert not old.exists()
    assert not old_payload.exists()
    assert new.exists()
    assert not (trf.RESULTS_BASE / "old-sess").exists()  # empty session dir swept


@pytest.mark.asyncio
async def test_offload_preview_keeps_head_and_tail(session_store: SessionStore):
    head_marker = "HEAD_LINE_UNIQUE_MARKER"
    tail_marker = "TAIL_LINE_UNIQUE_MARKER"
    middle = "\n".join(f"filler line {i}" for i in range(20_000))  # well over the offload threshold
    content = f"{head_marker}\n{middle}\n{tail_marker}"

    async def big_ht(execution: ToolExecution, args: EmptyInput) -> ToolResult:
        return ToolResult(content=content, preview="big")

    registry = ToolRegistry()
    registry.register(
        "big_ht",
        tool(
            description="Return a big payload with distinct head and tail.",
            input_model=EmptyInput,
            execute=big_ht,
            policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL, audit=True, offload=True),
        ),
    )
    ctx = _make_tool_context(registry, session_id="sess-1")
    ctx.services["store"] = session_store
    executor = ArdenToolExecutor(ToolExecutor().with_registry(registry), ctx)

    result = await executor.execute("big_ht", {}, "call-ht")

    assert head_marker in result.content  # head preserved
    assert tail_marker in result.content  # tail preserved (errors early, results late)
    assert "lines omitted" in result.content


@pytest.mark.asyncio
async def test_research_artifact_write_then_read(session_store: SessionStore):
    from arden.tools.research_artifacts import (
        ReadResearchArtifactInput,
        WriteResearchArtifactInput,
        read_research_artifact,
        write_research_artifact,
    )

    class _Svc:
        store = session_store

    ctx = _make_tool_context(ToolRegistry(), session_id="s")
    ctx.services["session"] = _Svc()
    ex = ToolExecution(tool_id="t", tool_name="write_research_artifact", ctx=ctx)

    w = await write_research_artifact(ex, WriteResearchArtifactInput(path="sources/inv.md", content="hello world"))
    assert not w.is_error

    r = await read_research_artifact(ex, ReadResearchArtifactInput(path="sources/inv.md"))
    assert not r.is_error
    assert "hello world" in r.content
    assert "fs_path=" in r.content

    from arden.tools.research_artifacts import artifact_scope_dir

    artifact_file = artifact_scope_dir("run-1") / "sources" / "inv.md"
    assert artifact_file.read_text() == "hello world"
    assert (artifact_scope_dir("run-1") / "manifest.json").exists()


def test_research_artifact_manifest_defaults_only_when_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(research_artifacts_module, "ARDEN_DIR", tmp_path / ".arden")
    scope_id = "run-1"
    path = research_artifacts_module._manifest_path(scope_id)

    assert research_artifacts_module._read_manifest(scope_id) == {
        "scope_id": scope_id,
        "artifact_dir": str(research_artifacts_module.artifact_scope_dir(scope_id)),
        "files": [],
    }

    path.parent.mkdir(parents=True)
    malformed = b"{not json}\n"
    path.write_bytes(malformed)

    with pytest.raises(json.JSONDecodeError):
        research_artifacts_module._read_manifest(scope_id)

    assert path.read_bytes() == malformed


def test_research_artifact_manifest_read_error_is_not_replaced(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(research_artifacts_module, "ARDEN_DIR", tmp_path / ".arden")
    scope_id = "run-1"
    path = research_artifacts_module._manifest_path(scope_id)
    path.parent.mkdir(parents=True)
    contents = b'{"files": []}\n'
    path.write_bytes(contents)
    original_read_text = Path.read_text

    def unreadable(self: Path, *args, **kwargs):
        if self == path:
            raise OSError("permission denied")
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unreadable)

    with pytest.raises(OSError, match="permission denied"):
        research_artifacts_module._read_manifest(scope_id)

    assert path.read_bytes() == contents


@pytest.mark.asyncio
async def test_research_artifact_rejects_traversal(session_store: SessionStore):
    from arden.tools.research_artifacts import WriteResearchArtifactInput, write_research_artifact

    class _Svc:
        store = session_store

    ctx = _make_tool_context(ToolRegistry(), session_id="s")
    ctx.services["session"] = _Svc()
    ex = ToolExecution(tool_id="t", tool_name="write_research_artifact", ctx=ctx)

    for bad in ["/abs/path", "../escape", "a/../b"]:
        res = await write_research_artifact(ex, WriteResearchArtifactInput(path=bad, content="x"))
        assert res.is_error, f"expected error for path {bad!r}"


@pytest.mark.asyncio
async def test_research_artifact_size_cap(session_store: SessionStore):
    from arden.tools.research_artifacts import (
        MAX_ARTIFACT_BYTES,
        WriteResearchArtifactInput,
        write_research_artifact,
    )

    class _Svc:
        store = session_store

    ctx = _make_tool_context(ToolRegistry(), session_id="s")
    ctx.services["session"] = _Svc()
    ex = ToolExecution(tool_id="t", tool_name="write_research_artifact", ctx=ctx)

    res = await write_research_artifact(
        ex, WriteResearchArtifactInput(path="big.md", content="é" * (MAX_ARTIFACT_BYTES // 2 + 1))
    )
    assert res.is_error


@pytest.mark.asyncio
async def test_research_artifact_count_cap(session_store: SessionStore):
    from arden.tools.research_artifacts import (
        MAX_ARTIFACTS_PER_SCOPE,
        WriteResearchArtifactInput,
        write_research_artifact,
    )

    class _Svc:
        store = session_store

    ctx = _make_tool_context(ToolRegistry(), session_id="s")
    ctx.services["session"] = _Svc()
    ex = ToolExecution(tool_id="t", tool_name="write_research_artifact", ctx=ctx)

    for i in range(MAX_ARTIFACTS_PER_SCOPE):
        ok = await write_research_artifact(ex, WriteResearchArtifactInput(path=f"f{i}.md", content="x"))
        assert not ok.is_error
    over = await write_research_artifact(ex, WriteResearchArtifactInput(path="overflow.md", content="x"))
    assert over.is_error


@pytest.mark.asyncio
async def test_arden_tool_executor_audits_cancelled_policy_enabled_call(session_store: SessionStore):
    entered = asyncio.Event()

    async def wait_forever(execution: ToolExecution, args: EmptyInput) -> ToolResult:
        entered.set()
        await asyncio.Event().wait()
        return ToolResult(content="done", preview="done")

    registry = ToolRegistry()
    registry.register(
        "wait_forever",
        tool(
            description="Wait until cancelled.",
            execute=wait_forever,
            policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL, audit=True),
        ),
    )
    ctx = _make_tool_context(registry, session_id="sess-1")
    ctx.services["store"] = session_store
    executor = ArdenToolExecutor(ToolExecutor().with_registry(registry), ctx)

    task = asyncio.create_task(executor.execute("wait_forever", {}, "call-1"))
    await entered.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    rows = await session_store.list_tool_calls(run_id="run-1")
    assert len(rows) == 1
    assert rows[0]["tool_call_id"] == "call-1"
    assert rows[0]["status"] == "cancelled"
    assert rows[0]["outcome"]["status"] == "uncertain"
    assert rows[0]["outcome"]["error"]["code"] == "tool_cancelled"
    assert rows[0]["ended_at"] is not None


@pytest.mark.asyncio
async def test_arden_tool_executor_audits_internal_failure_outcome(session_store: SessionStore):
    async def explode(execution: ToolExecution, args: EmptyInput) -> ToolResult:
        raise RuntimeError("boom")

    registry = ToolRegistry()
    registry.register(
        "explode",
        tool(
            description="Fail.",
            execute=explode,
            policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL, audit=True),
        ),
    )
    ctx = _make_tool_context(registry, session_id="sess-1")
    ctx.services["store"] = session_store
    executor = ArdenToolExecutor(ToolExecutor().with_registry(registry), ctx)

    with pytest.raises(RuntimeError, match="boom"):
        await executor.execute("explode", {}, "call-1")

    row = (await session_store.list_tool_calls(run_id="run-1"))[0]
    assert row["status"] == "error"
    assert row["outcome"]["status"] == "uncertain"
    assert row["outcome"]["error"]["code"] == "internal_error"
    assert row["outcome"]["error"]["diagnostic_ref"] == "tool-call:call-1"
    assert "before retrying" in row["outcome"]["error"]["recovery_action"]


@pytest.mark.asyncio
async def test_arden_tool_executor_skips_audit_when_policy_disabled(session_store: SessionStore):
    async def echo(execution: ToolExecution, args: EchoInput) -> ToolResult:
        return ToolResult(content=f"echo: {args.text}", preview="ok")

    registry = ToolRegistry()
    registry.register(
        "echo",
        tool(
            description="Echo text.",
            input_model=EchoInput,
            execute=echo,
            policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL, audit=False),
        ),
    )
    ctx = _make_tool_context(registry, session_id="sess-1")
    ctx.services["store"] = session_store
    executor = ArdenToolExecutor(ToolExecutor().with_registry(registry), ctx)

    result = await executor.execute("echo", {"text": "hello"}, "call-1")

    assert result.preview == "ok"
    assert await session_store.list_tool_calls(run_id="run-1") == []


@pytest.mark.asyncio
async def test_arden_tool_executor_audits_rejected_approval_as_error(session_store: SessionStore):
    async def echo(execution: ToolExecution, args: EchoInput) -> ToolResult:
        return ToolResult(content=args.text, preview=args.text)

    async def approve_echo(execution: ToolExecution, args: EchoInput) -> ApprovalInfo:
        return ApprovalInfo(description="Echo text", preview=args.text, diff=None)

    registry = ToolRegistry()
    registry.register(
        "echo",
        tool(
            description="Echo text.",
            input_model=EchoInput,
            execute=echo,
            policy=WRITE_INTERNAL_APPROVAL_POLICY,
            approval=approve_echo,
        ),
    )
    ctx = _make_tool_context(registry, session_id="sess-1")
    ctx.services["store"] = session_store
    executor = ArdenToolExecutor(ToolExecutor().with_registry(registry), ctx)

    result = await executor.execute("echo", {"text": "hello"}, "call-1")
    rows = await session_store.list_tool_calls(run_id="run-1")

    assert result.preview == "Rejected"
    assert rows[0]["status"] == "error"
    assert rows[0]["result_preview"] == "Rejected"


def test_tool_executor_registers_integration_tool_map(monkeypatch):
    async def current_time(execution: ToolExecution, args: EmptyInput) -> ToolResult:
        return ToolResult(content="now", preview="now")

    time_tools = {
        "current_time": tool(
            description="Get the current time.",
            execute=current_time,
            policy=READ_INTERNAL_POLICY,
        )
    }
    monkeypatch.setattr(
        tool_executor_module,
        "ALL_INTEGRATIONS",
        [Integration(id="_test", label="Test", tools=time_tools)],
    )

    executor = ToolExecutor()

    assert executor.registry.get("current_time") is time_tools["current_time"]


@pytest.mark.asyncio
async def test_tool_registry_runs_middlewares_in_order():
    calls: list[str] = []

    async def middleware_one(call: ToolCall, next_call: ToolNext) -> ToolResult:
        calls.append("one:before")
        result = await next_call(call)
        calls.append("one:after")
        return result

    async def middleware_two(call: ToolCall, next_call: ToolNext) -> ToolResult:
        calls.append("two:before")
        result = await next_call(call)
        calls.append("two:after")
        return result

    async def current_time(execution: ToolExecution, args: EmptyInput) -> ToolResult:
        calls.append("execute")
        return ToolResult(content="now", preview="now")

    registry = ToolRegistry(middlewares=(middleware_one, middleware_two))
    registry.register(
        "current_time",
        tool(description="Get the current time.", execute=current_time, policy=READ_INTERNAL_POLICY),
    )

    result = await registry.execute("current_time", _make_execution("current_time"), {})

    assert result.content == "now"
    assert calls == ["one:before", "two:before", "execute", "two:after", "one:after"]


def test_tool_registry_rejects_duplicate_tool_names():
    async def current_time(execution: ToolExecution, args: EmptyInput) -> ToolResult:
        return ToolResult(content="now", preview="now")

    registry = ToolRegistry()
    registry.register(
        "current_time",
        tool(description="Get the current time.", execute=current_time, policy=READ_INTERNAL_POLICY),
    )

    with pytest.raises(ValueError, match="duplicate tool name"):
        registry.register(
            "current_time",
            tool(description="Get the current time.", execute=current_time, policy=READ_INTERNAL_POLICY),
        )


def test_discover_user_tools_loads_named_tool_map(tmp_path):
    (tmp_path / "custom.py").write_text(
        """
from arden.tools.core import EmptyInput, ToolAction, ToolPolicy, ToolResult, ToolScope, tool


async def hello(execution, args: EmptyInput) -> ToolResult:
    return ToolResult(content="hello", preview="hello")


tools = {
    "hello": tool(
        description="Say hello.",
        execute=hello,
        policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL),
    ),
}
""".strip()
    )

    discovered = discover_user_tools(tmp_path)

    assert set(discovered) == {"hello"}
    assert discovered["hello"].to_dict("hello")["function"]["name"] == "hello"


@pytest.mark.asyncio
async def test_current_time_uses_configured_timezone(monkeypatch):
    from types import SimpleNamespace

    from arden.tools.time import current_time_tool

    monkeypatch.setattr("arden.tools.time.get_config", lambda: SimpleNamespace(timezone="Asia/Tokyo"))
    result = await current_time_tool.execute(_make_execution("current_time"))

    assert result.data["timezone"] == "Asia/Tokyo"
    assert result.content.endswith("+09:00")
    assert result.data["utc"].endswith("+00:00")


@pytest.mark.asyncio
async def test_child_agent_approval_carries_attribution():
    async def echo(execution: ToolExecution, args: EchoInput) -> ToolResult:
        return ToolResult(content=args.text, preview=args.text)

    async def approve_echo(execution: ToolExecution, args: EchoInput) -> ApprovalInfo:
        return ApprovalInfo(description="Echo text", preview=args.text, diff=None)

    registry = ToolRegistry()
    _register_tools(
        registry,
        {
            "echo": tool(
                description="Echo text.",
                input_model=EchoInput,
                execute=echo,
                policy=WRITE_INTERNAL_APPROVAL_POLICY,
                approval=approve_echo,
            )
        },
    )
    emitted = []
    recorded = []
    pending: dict[str, asyncio.Future] = {}

    async def emit(event):
        emitted.append(event)
        # Resolve immediately so request_approval settles without a timeout.
        pending["call-1"].set_result({"approved": True, "result": "", "source": "user"})

    async def record_approval(**kwargs):
        recorded.append(kwargs)

    async def resolve_approval(**kwargs):
        recorded.append(kwargs)

    child_state = SessionState(session_id="parent::ab12cd34", started_at=datetime.now(UTC))
    child_state.agent_type = "research"
    child_state.name = "Scan the docs"
    child_state.parent_session_id = "parent"
    ctx = ToolContext(
        session_state=child_state,
        registry=registry,
        run=RunContext(run_id="run-1"),
        io=IOBridge(
            emit=emit,
            pending_approvals=pending,
            record_approval=record_approval,
            resolve_approval=resolve_approval,
        ),
        background_tasks=BackgroundTaskRegistry(session_id="parent::ab12cd34"),
    )
    execution = ToolExecution(tool_id="call-1", tool_name="echo", ctx=ctx)

    rejection = await execution.request_approval("Echo hello", preview="hello")

    assert rejection is None
    # Durable record carries the requester identity.
    request = recorded[0]
    assert request["description"] == "Echo hello"
    assert request["agent_type"] == "research"
    assert request["agent_name"] == "Scan the docs"
    assert request["parent_session_id"] == "parent"
    # The card event says which run resolves it and which agent asked.
    event = emitted[0]
    assert event.run_id == "run-1"
    assert event.session_id == "parent::ab12cd34"
    assert event.agent_type == "research"
    assert event.agent_name == "Scan the docs"
    assert event.action == "write"
    assert event.expires_at is not None
    # The resolution records who decided.
    resolution = recorded[1]
    assert resolution["status"] == "approved"
    assert resolution["source"] == "user"
