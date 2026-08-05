import ast
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from arden.agent import ToolOutcome, ToolOutcomeStatus, ToolResult
from arden.context.models import SessionState
from arden.tools.core.context import BackgroundTaskRegistry, IOBridge, RunContext, ToolContext, ToolExecution
from arden.tools.core.registry import ToolRegistry
from arden.tools.notify import NotifyInput, notify

ARDEN_ROOT = Path(__file__).parents[1] / "arden"


def _execution(*, services=None) -> ToolExecution:
    return ToolExecution(
        tool_id="call-1",
        tool_name="notify",
        ctx=ToolContext(
            session_state=SessionState(session_id="test", started_at=datetime.now(UTC)),
            registry=ToolRegistry(),
            run=RunContext(run_id="run-1"),
            io=IOBridge(),
            services=services or {},
            background_tasks=BackgroundTaskRegistry(session_id="test"),
        ),
    )


def test_tool_result_rejects_contradictory_error_and_outcome_status():
    with pytest.raises(ValueError, match="failed outcome"):
        ToolResult(
            content="bad",
            preview="bad",
            outcome=ToolOutcome(status=ToolOutcomeStatus.FAILED),
        )
    with pytest.raises(ValueError, match="successful outcome"):
        ToolResult(
            content="bad",
            preview="bad",
            is_error=True,
            outcome=ToolOutcome(status=ToolOutcomeStatus.SUCCEEDED),
        )


def test_agent_facing_tool_failures_always_include_recovery_guidance():
    missing: list[str] = []
    for path in ARDEN_ROOT.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "failure" or not isinstance(node.func.value, ast.Name):
                continue
            if node.func.value.id != "ToolResult":
                continue
            if "recovery_action" not in {keyword.arg for keyword in node.keywords}:
                missing.append(f"{path.relative_to(ARDEN_ROOT)}:{node.lineno}")

    assert missing == []


@pytest.mark.asyncio
async def test_notify_total_delivery_failure_is_typed_failure():
    attempts = 0

    class FailingNotifier:
        channel = "work"

        async def send(self, subject: str, body: str) -> None:
            nonlocal attempts
            attempts += 1
            raise RuntimeError("provider secret")

    result = await notify(
        _execution(services={"notifiers": SimpleNamespace(notifiers={"work": FailingNotifier()})}),
        NotifyInput(subject="Build", body="Failed"),
    )

    assert result.is_error
    assert result.outcome is not None and result.outcome.error is not None
    assert attempts == 1
    assert result.outcome.status is ToolOutcomeStatus.UNCERTAIN
    assert result.outcome.error.code == "mutation_uncertain"
    assert result.outcome.error.retryable is False
    assert "Inspect the target channels" in result.outcome.error.recovery_action
    assert "provider secret" not in result.content
