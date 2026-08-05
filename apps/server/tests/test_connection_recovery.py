from datetime import UTC, datetime

import pytest
from pydantic import BaseModel

from arden.context.models import SessionState
from arden.core.tool_executor import ArdenToolExecutor
from arden.integrations.base import IntegrationConnectionDescriptor, IntegrationConnectionError
from arden.integrations.gmail.client import GmailSource
from arden.integrations.google_auth import auth as google_auth
from arden.integrations.slack.client import SlackClient
from arden.tools.connections import ConnectionService
from arden.tools.core import ToolResult, tool
from arden.tools.core.context import IOBridge, RunContext, ToolContext, ToolExecution
from arden.tools.core.registry import ToolRegistry
from arden.tools.core.types import ToolAction, ToolPolicy, ToolScope
from arden.tools.executor import ToolExecutor


class EmptyInput(BaseModel):
    pass


class StubConnectionRegistry:
    client = None

    def get_connection(self, integration_id: str) -> IntegrationConnectionDescriptor | None:
        if integration_id != "slack":
            return None
        return IntegrationConnectionDescriptor(
            integration_id="slack",
            connection_id="slack",
            label="Slack",
            capability="Search and send Slack messages",
            action="credentials",
            settings_tab="integrations",
            state="connected",
            tool_names=("search_slack",),
        )

    def get_client(self, integration_id: str):
        return self.client


def _executor(action: ToolAction, handler, *, approved: bool = True) -> ArdenToolExecutor:
    registry = ToolRegistry()
    registry.register(
        "search_slack",
        tool(
            description="Use Slack.",
            input_model=EmptyInput,
            execute=handler,
            policy=ToolPolicy(action=action, scope=ToolScope.EXTERNAL),
        ),
    )
    pending = {}

    async def emit(event):
        pending[event.tool_id].set_result({"approved": approved, "result": ""})

    ctx = ToolContext(
        session_state=SessionState(session_id="session", started_at=datetime.now(UTC)),
        registry=registry,
        run=RunContext(run_id="run"),
        io=IOBridge(emit=emit, pending_connections=pending),
        services={"connections": ConnectionService(StubConnectionRegistry())},
    )
    return ArdenToolExecutor(ToolExecutor().with_registry(registry), ctx)


@pytest.mark.asyncio
async def test_read_tool_retries_once_after_connection_is_repaired():
    calls = 0

    async def handler(execution: ToolExecution, args: EmptyInput) -> ToolResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise IntegrationConnectionError(
                integration_id="slack",
                reason="auth_required",
                detail="Slack token expired",
                retry_safe=True,
            )
        return ToolResult(content="messages", preview="messages")

    result = await _executor(ToolAction.READ, handler).execute("search_slack", {}, "call-1")

    assert calls == 2
    assert result.content == "messages"


@pytest.mark.asyncio
async def test_write_tool_never_retries_automatically_after_connection_is_repaired():
    calls = 0

    async def handler(execution: ToolExecution, args: EmptyInput) -> ToolResult:
        nonlocal calls
        calls += 1
        raise IntegrationConnectionError(
            integration_id="slack",
            reason="auth_required",
            detail="Slack token expired",
            retry_safe=True,
        )

    result = await _executor(ToolAction.WRITE, handler).execute("search_slack", {}, "call-1")

    assert calls == 1
    assert result.is_error
    assert result.outcome.error.code == "connection_retry_required"


@pytest.mark.asyncio
async def test_declined_recovery_returns_typed_failure_without_retry():
    calls = 0

    async def handler(execution: ToolExecution, args: EmptyInput) -> ToolResult:
        nonlocal calls
        calls += 1
        raise IntegrationConnectionError(
            integration_id="slack",
            reason="auth_required",
            detail="Slack token expired",
            retry_safe=True,
        )

    result = await _executor(ToolAction.READ, handler, approved=False).execute("search_slack", {}, "call-1")

    assert calls == 1
    assert result.outcome.error.code == "connection_declined"


def test_slack_auth_error_is_typed_for_recovery():
    client = SlackClient(bot_token="bad")

    with pytest.raises(IntegrationConnectionError) as raised:
        client.transport.raise_for_error("conversations.history", {"error": "token_revoked"}, {})

    assert raised.value.integration_id == "slack"
    assert raised.value.reason == "auth_required"
    assert raised.value.retry_safe is True


def test_slack_missing_scope_error_includes_required_scopes():
    client = SlackClient(bot_token="token")

    with pytest.raises(IntegrationConnectionError) as raised:
        client.transport.raise_for_error(
            "conversations.history",
            {"error": "missing_scope", "needed": "channels:history,groups:history"},
            {},
        )

    assert raised.value.reason == "scope_required"
    assert raised.value.required_scopes == ("channels:history", "groups:history")


def test_gmail_send_scope_failure_is_typed(monkeypatch, tmp_path):
    source = GmailSource(token_path=tmp_path / "gmail_token.json")
    monkeypatch.setattr(source, "_get_credentials", lambda: type("Credentials", (), {"scopes": []})())

    with pytest.raises(IntegrationConnectionError) as raised:
        source.has_send_scope()

    assert raised.value.integration_id == "gmail"
    assert raised.value.reason == "scope_required"
    assert raised.value.required_scopes


def test_gmail_read_does_not_swallow_typed_connection_failure(monkeypatch, tmp_path):
    source = GmailSource(token_path=tmp_path / "gmail_token.json")
    error = IntegrationConnectionError(
        integration_id="gmail",
        reason="auth_required",
        detail="Reconnect Gmail",
        retry_safe=True,
    )
    monkeypatch.setattr(source, "_get_service", lambda: (_ for _ in ()).throw(error))

    with pytest.raises(IntegrationConnectionError):
        source._fetch_message_full("message-1")


def test_google_refresh_failure_is_typed(monkeypatch, tmp_path):
    token_path = tmp_path / "calendar_token.json"
    token_path.write_text("{}")

    class ExpiredCredentials:
        valid = False
        expired = True
        refresh_token = "refresh"

        def refresh(self, request):
            raise google_auth.RefreshError("revoked")

    monkeypatch.setattr(
        google_auth.Credentials,
        "from_authorized_user_file",
        lambda _path: ExpiredCredentials(),
    )

    with pytest.raises(IntegrationConnectionError) as raised:
        google_auth.get_google_credentials(token_path, integration_id="calendar")

    assert raised.value.integration_id == "calendar"
    assert raised.value.reason == "auth_required"
    assert raised.value.retry_safe is True


@pytest.mark.asyncio
async def test_connection_verification_calls_provider_health_check():
    checked = False

    class Client:
        async def verify_connection(self):
            nonlocal checked
            checked = True

    registry = StubConnectionRegistry()
    registry.client = Client()

    await ConnectionService(registry).verify_connection("slack")

    assert checked is True
