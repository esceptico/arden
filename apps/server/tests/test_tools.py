"""Tool execution tests — real tool logic, controlled inputs."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import pytest_asyncio
from pydantic import BaseModel, Field

import ntrp.database as database
import ntrp.tools.executor as tool_executor_module
from ntrp.agent import ToolEffect, ToolOutcome, ToolOutcomeStatus, ToolResult, ToolVerification
from ntrp.constants import OFFLOAD_PREVIEW_CHARS, OFFLOAD_THRESHOLD
from ntrp.context.models import SessionState
from ntrp.context.store import SessionStore
from ntrp.core.tool_executor import NtrpToolExecutor
from ntrp.integrations.base import Integration
from ntrp.tool_call_metadata import DISPLAY_TITLE_ARG
from ntrp.tools.automation import loop_done_tool, schedule_wakeup_tool
from ntrp.tools.background import cancel_background_task_tool
from ntrp.tools.bash import bash_tool, execute_bash, is_blocked_command, is_safe_command
from ntrp.tools.core import EmptyInput, Tool, ToolCall, ToolNext, tool
from ntrp.tools.core.context import (
    ApprovalControls,
    BackgroundTaskRegistry,
    IOBridge,
    RunContext,
    ToolContext,
    ToolExecution,
)
from ntrp.tools.core.registry import ToolRegistry
from ntrp.tools.core.types import ApprovalInfo, ToolAction, ToolOverrideDecision, ToolPolicy, ToolScope
from ntrp.tools.discover import discover_user_tools
from ntrp.tools.executor import ToolExecutor
from ntrp.tools.notify import notify_tool
from ntrp.tools.todos import update_todos_tool
from ntrp.tools.workflow import workflow_tool

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
            "requires_approval": False,
            "approval_mode": "never",
        "permissions": [],
        "timeout_seconds": None,
        "audit": True,
        "max_result_chars": None,
        "offload": True,
        "allow_approval_bypass": True,
        "destructive": None,
        "open_world": None,
        "idempotent": None,
    }


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

    rejection = await ToolExecution(tool_id="call-1", tool_name="bash", ctx=ctx).request_approval(
        "Run shell command"
    )

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
    import ntrp.core.tool_result_files as trf
    import ntrp.tools.research_artifacts as research_artifacts

    monkeypatch.setattr(trf, "RESULTS_BASE", tmp_path / "tool-results")
    monkeypatch.setattr(research_artifacts, "NTRP_DIR", tmp_path / ".ntrp")


@pytest_asyncio.fixture
async def session_store(tmp_path: Path):
    conn = await database.connect(tmp_path / "sessions.db")
    read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
    store = SessionStore(conn, read_conn)
    await store.init_schema()
    yield store
    await read_conn.close()
    await conn.close()


def _register_tools(registry: ToolRegistry, tools: dict[str, Tool]) -> None:
    for name, candidate in tools.items():
        registry.register(name, candidate)


# --- Bash safety checks ---


def test_safe_commands():
    assert is_safe_command("ls")
    assert is_safe_command("git status")
    assert is_safe_command("cat file.txt")
    assert is_safe_command("python --version")
    assert not is_safe_command("rm -rf /")
    assert not is_safe_command("sudo reboot")


def test_blocked_commands():
    assert is_blocked_command("rm -rf /")
    assert is_blocked_command("rm -rf ~")
    assert is_blocked_command("dd if=/dev/zero")
    assert not is_blocked_command("ls -la")
    assert not is_blocked_command("echo hello")


# --- Bash execution ---


def test_execute_bash_simple():
    output = execute_bash("echo hello")
    assert "hello" in output.content


def test_execute_bash_stderr():
    output = execute_bash("echo err >&2")
    assert "err" in output.content
    assert "[stderr]" in output.content


def test_execute_bash_exit_code():
    output = execute_bash("exit 1")
    assert "[exit code: 1]" in output.content
    assert output.returncode == 1


def test_execute_bash_timeout():
    output = execute_bash("sleep 10", timeout=1)
    assert "timed out" in output.content.lower()
    assert output.timed_out


# --- Bash tool ---


@pytest.mark.asyncio
async def test_bash_tool_execute():
    execution = _make_execution("bash")
    result = await bash_tool.execute(execution, command="echo test123")
    assert "test123" in result.content


@pytest.mark.asyncio
async def test_bash_tool_nonzero_exit_is_typed_failure():
    result = await bash_tool.execute(_make_execution("bash"), command="exit 7")

    assert result.is_error
    assert result.outcome is not None and result.outcome.error is not None
    assert result.outcome.error.code == "command_failed"


@pytest.mark.asyncio
async def test_bash_tool_timeout_is_typed_failure(monkeypatch):
    monkeypatch.setattr("ntrp.tools.bash.BASH_TIMEOUT", 0.01)
    result = await bash_tool.execute(_make_execution("bash"), command="sleep 1")

    assert result.is_error
    assert result.outcome is not None and result.outcome.error is not None
    assert result.outcome.error.code == "timed_out"


@pytest.mark.asyncio
async def test_bash_tool_blocked():
    execution = _make_execution("bash")
    result = await bash_tool.execute(execution, command="rm -rf /")
    assert result.is_error
    assert "Blocked" in result.content


@pytest.mark.asyncio
async def test_bash_tool_working_dir(tmp_path):
    execution = _make_execution("bash")
    result = await bash_tool.execute(execution, command="pwd", working_dir=str(tmp_path))
    assert str(tmp_path) in result.content


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
    execution = ToolExecution(tool_id="call-todos", tool_name="update_todos", ctx=ctx)

    result = await update_todos_tool.execute(
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
    _register_tools(registry, {"update_todos": update_todos_tool})

    result = await registry.execute(
        "update_todos",
        _make_execution("update_todos"),
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
    wakeup = await schedule_wakeup_tool.approval_info(execution, delay_seconds=120)
    done = await loop_done_tool.approval_info(execution, reason="The requested condition is satisfied")
    cancel = await cancel_background_task_tool.approval_info(execution, task_id="agent-42")

    assert notify is not None
    assert notify.description == "Notify via work-telegram: Build failed"
    assert notify.preview == "The release build failed in the signing step."
    assert wakeup is not None and wakeup.preview == "Delay: 120 seconds"
    assert done is not None and done.preview == "The requested condition is satisfied"
    assert cancel is not None and cancel.preview == "Background task: agent-42"


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
async def test_ntrp_tool_executor_policy_offload_false_keeps_large_result_inline():
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
    executor = NtrpToolExecutor(ToolExecutor().with_registry(registry), _make_tool_context(registry))

    result = await executor.execute("large_result", {}, "call-1")

    assert result.content == large_content
    assert result.preview == "large"
    assert not result.is_error
    assert result.outcome is not None
    assert result.outcome.status == ToolOutcomeStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_ntrp_tool_executor_rejects_registered_tool_outside_run_allowlist():
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
    executor = NtrpToolExecutor(ToolExecutor().with_registry(registry), ctx)

    result = await executor.execute("hidden", {}, "call-1")

    assert result.is_error
    assert result.preview == "Tool not allowed"
    assert result.outcome is not None
    assert result.outcome.status == ToolOutcomeStatus.DENIED
    assert result.outcome.error is not None
    assert result.outcome.error.code == "permission_denied"
    assert called is False


@pytest.mark.asyncio
async def test_ntrp_tool_executor_offload_clamps_single_long_line_preview():
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
    executor = NtrpToolExecutor(ToolExecutor().with_registry(registry), _make_tool_context(registry))

    result = await executor.execute("large_result", {}, "call-1")

    assert len(result.content) < OFFLOAD_PREVIEW_CHARS + 500
    assert "truncated" in result.content
    assert "saved to" in result.content
    assert "read_file" in result.content


@pytest.mark.asyncio
async def test_ntrp_tool_executor_policy_max_result_chars_truncates_before_model_result():
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
    executor = NtrpToolExecutor(ToolExecutor().with_registry(registry), _make_tool_context(registry))

    result = await executor.execute("long_error", {}, "call-1")

    assert result.content == "abcdefghij... [truncated]"
    assert result.preview == "original"
    assert result.is_error


@pytest.mark.asyncio
async def test_ntrp_tool_executor_policy_timeout_seconds_returns_error_result():
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
    executor = NtrpToolExecutor(ToolExecutor().with_registry(registry), _make_tool_context(registry))

    result = await executor.execute("slow", {}, "call-1")

    assert result.content == "Tool call timed out."
    assert result.preview == "Timed out"
    assert result.outcome is not None
    assert result.outcome.error is not None
    assert result.outcome.error.code == "timeout"
    assert result.outcome.error.retryable is True


@pytest.mark.asyncio
async def test_ntrp_tool_executor_defaults_external_tool_timeout(monkeypatch):
    import ntrp.core.tool_executor as core_tool_executor_module

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
    executor = NtrpToolExecutor(ToolExecutor().with_registry(registry), _make_tool_context(registry))

    result = await executor.execute("slow_external", {}, "call-1")

    assert result.content == "Tool call timed out."
    assert result.preview == "Timed out"
    assert result.outcome is not None
    assert result.outcome.error is not None
    assert result.outcome.error.code == "timeout"


@pytest.mark.asyncio
async def test_ntrp_tool_executor_audits_policy_enabled_calls(session_store: SessionStore):
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
    executor = NtrpToolExecutor(ToolExecutor().with_registry(registry), ctx)

    result = await executor.execute("echo", {"text": "hello"}, "call-1")
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
    import ntrp.core.tool_result_files as trf

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
    executor = NtrpToolExecutor(ToolExecutor().with_registry(registry), ctx)

    result = await executor.execute("big_offload", {}, "call-7")

    # full result saved to a durable file, recovered via the normal read_file tool by path
    assert "read_file" in result.content
    path = trf.result_file_path("sess-1", "call-7")
    assert str(path) in result.content
    assert path.exists()
    assert path.read_text() == big
    # offload is session-scoped, not a global pile (the 5GB runaway came from a
    # flat global store), and locatable by id without the session.
    assert path.parent == trf.session_results_dir("sess-1")
    assert trf.find_result_file("call-7") == path


def test_prune_offload_store_drops_old_keeps_recent(monkeypatch, tmp_path):
    import os
    import time

    import ntrp.core.tool_result_files as trf

    monkeypatch.setattr(trf, "RESULTS_BASE", tmp_path / "tool-results")
    old = trf.persist_result("old-sess", "call-old", "x" * 100)
    new = trf.persist_result("new-sess", "call-new", "y" * 100)
    past = time.time() - (trf.RESULTS_MAX_AGE_SECONDS + 3600)
    os.utime(old, (past, past))

    removed = trf.prune_offload_store()

    assert removed == 1
    assert not old.exists()
    assert new.exists()
    assert not (trf.RESULTS_BASE / "old-sess").exists()  # empty session dir swept
    assert trf.find_result_file("call-new") == new  # still locatable by id


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
    executor = NtrpToolExecutor(ToolExecutor().with_registry(registry), ctx)

    result = await executor.execute("big_ht", {}, "call-ht")

    assert head_marker in result.content  # head preserved
    assert tail_marker in result.content  # tail preserved (errors early, results late)
    assert "lines omitted" in result.content


@pytest.mark.asyncio
async def test_research_artifact_write_then_read(session_store: SessionStore):
    from ntrp.tools.research_artifacts import (
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

    from ntrp.tools.research_artifacts import artifact_scope_dir

    artifact_file = artifact_scope_dir("run-1") / "sources" / "inv.md"
    assert artifact_file.read_text() == "hello world"
    assert (artifact_scope_dir("run-1") / "manifest.json").exists()


@pytest.mark.asyncio
async def test_research_artifact_rejects_traversal(session_store: SessionStore):
    from ntrp.tools.research_artifacts import WriteResearchArtifactInput, write_research_artifact

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
    from ntrp.tools.research_artifacts import (
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
        ex, WriteResearchArtifactInput(path="big.md", content="x" * (MAX_ARTIFACT_BYTES + 1))
    )
    assert res.is_error


@pytest.mark.asyncio
async def test_research_artifact_count_cap(session_store: SessionStore):
    from ntrp.tools.research_artifacts import (
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
async def test_ntrp_tool_executor_audits_cancelled_policy_enabled_call(session_store: SessionStore):
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
    executor = NtrpToolExecutor(ToolExecutor().with_registry(registry), ctx)

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
async def test_ntrp_tool_executor_audits_internal_failure_outcome(session_store: SessionStore):
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
    executor = NtrpToolExecutor(ToolExecutor().with_registry(registry), ctx)

    with pytest.raises(RuntimeError, match="boom"):
        await executor.execute("explode", {}, "call-1")

    row = (await session_store.list_tool_calls(run_id="run-1"))[0]
    assert row["status"] == "error"
    assert row["outcome"]["status"] == "failed"
    assert row["outcome"]["error"]["code"] == "internal_error"


@pytest.mark.asyncio
async def test_ntrp_tool_executor_skips_audit_when_policy_disabled(session_store: SessionStore):
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
    executor = NtrpToolExecutor(ToolExecutor().with_registry(registry), ctx)

    result = await executor.execute("echo", {"text": "hello"}, "call-1")

    assert result.preview == "ok"
    assert await session_store.list_tool_calls(run_id="run-1") == []


@pytest.mark.asyncio
async def test_ntrp_tool_executor_audits_rejected_approval_as_error(session_store: SessionStore):
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
    executor = NtrpToolExecutor(ToolExecutor().with_registry(registry), ctx)

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
from ntrp.tools.core import EmptyInput, ToolAction, ToolPolicy, ToolResult, ToolScope, tool


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
