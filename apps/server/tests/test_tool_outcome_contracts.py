from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from arden.agent import ToolOutcome, ToolOutcomeStatus, ToolResult
from arden.context.models import SessionState
from arden.tools.core.context import BackgroundTaskRegistry, IOBridge, RunContext, ToolContext, ToolExecution
from arden.tools.core.registry import ToolRegistry
from arden.tools.notify import NotifyInput, notify


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


@pytest.mark.asyncio
async def test_notify_total_delivery_failure_is_typed_failure(monkeypatch):
    class FailingNotifier:
        channel = "work"

        async def send(self, subject: str, body: str) -> None:
            raise RuntimeError("provider secret")

    async def send_once(notifier, subject, body):
        await notifier.send(subject, body)

    monkeypatch.setattr("arden.tools.notify._send_with_retry", send_once)
    result = await notify(
        _execution(services={"notifiers": SimpleNamespace(notifiers={"work": FailingNotifier()})}),
        NotifyInput(subject="Build", body="Failed"),
    )

    assert result.is_error
    assert result.outcome is not None and result.outcome.error is not None
    assert result.outcome.error.code == "provider_error"
    assert "provider secret" not in result.content
