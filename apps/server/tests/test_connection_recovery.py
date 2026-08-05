import stat
from datetime import UTC, datetime
from threading import get_ident

import pytest
from pydantic import BaseModel

from arden.context.models import SessionState
from arden.core.tool_executor import ArdenToolExecutor
from arden.integrations.base import (
    IntegrationConnectionDescriptor,
    IntegrationConnectionError,
    IntegrationOperationError,
)
from arden.integrations.gmail.client import GmailSource
from arden.integrations.google_auth import auth as google_auth
from arden.integrations.google_auth import storage as google_storage
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


async def _empty_connection_suspension(**_kwargs):
    return None


async def _persist_connection(**_kwargs):
    return None


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
        io=IOBridge(
            emit=emit,
            pending_connections=pending,
            record_connection=_persist_connection,
            resolve_connection=_persist_connection,
            get_suspension=_empty_connection_suspension,
            consume_suspension=_persist_connection,
        ),
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
    source = GmailSource(account_ref="me@example.test", token_path=tmp_path / "gmail_token.json")
    monkeypatch.setattr(source, "_get_credentials", lambda: type("Credentials", (), {"scopes": []})())

    with pytest.raises(IntegrationConnectionError) as raised:
        source.require_send_scope()

    assert raised.value.integration_id == "gmail"
    assert raised.value.reason == "scope_required"
    assert raised.value.required_scopes


def test_gmail_send_scope_requirement_returns_none_when_satisfied(monkeypatch, tmp_path):
    source = GmailSource(account_ref="me@example.test", token_path=tmp_path / "gmail_token.json")
    monkeypatch.setattr(
        source,
        "_get_credentials",
        lambda: type("Credentials", (), {"scopes": ["https://www.googleapis.com/auth/gmail.send"]})(),
    )

    assert source.require_send_scope() is None


def test_gmail_read_does_not_swallow_typed_connection_failure(monkeypatch, tmp_path):
    source = GmailSource(account_ref="me@example.test", token_path=tmp_path / "gmail_token.json")
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


def test_google_runtime_auth_never_starts_oauth(monkeypatch, tmp_path):
    def fail_oauth(*_args, **_kwargs):
        raise AssertionError("runtime credential loading must not start OAuth")

    monkeypatch.setattr(google_auth.InstalledAppFlow, "from_client_secrets_file", fail_oauth)

    with pytest.raises(IntegrationConnectionError) as raised:
        google_auth.get_google_credentials(tmp_path / "missing.json", integration_id="gmail")

    assert raised.value.reason == "auth_required"


def test_google_runtime_auth_types_invalid_token(monkeypatch, tmp_path):
    token_path = tmp_path / "invalid.json"
    token_path.write_text("invalid")
    monkeypatch.setattr(
        google_auth.Credentials,
        "from_authorized_user_file",
        lambda _path: (_ for _ in ()).throw(ValueError("provider parser detail")),
    )

    with pytest.raises(IntegrationConnectionError) as raised:
        google_auth.get_google_credentials(token_path, integration_id="gmail")

    assert raised.value.reason == "auth_required"
    assert "provider parser detail" not in str(raised.value)


def test_google_refresh_transport_failure_is_typed(monkeypatch, tmp_path):
    token_path = tmp_path / "gmail.json"
    token_path.write_text("{}")

    class ExpiredCredentials:
        valid = False
        expired = True
        refresh_token = "refresh"

        def refresh(self, _request):
            raise google_auth.TransportError("network detail")

    monkeypatch.setattr(
        google_auth.Credentials,
        "from_authorized_user_file",
        lambda _path: ExpiredCredentials(),
    )

    with pytest.raises(IntegrationOperationError) as raised:
        google_auth.get_google_credentials(token_path, integration_id="gmail")

    assert raised.value.code == "provider_error"
    assert raised.value.retryable is True
    assert "network detail" not in str(raised.value)


def test_google_refresh_persistence_failure_is_typed(monkeypatch, tmp_path):
    token_path = tmp_path / "gmail.json"
    token_path.write_text("{}")

    class RefreshedCredentials:
        def __init__(self):
            self.valid = False
            self.expired = True
            self.refresh_token = "refresh"

        def refresh(self, _request):
            self.valid = True

        def to_json(self):
            raise ValueError("serialization detail")

    monkeypatch.setattr(
        google_auth.Credentials,
        "from_authorized_user_file",
        lambda _path: RefreshedCredentials(),
    )

    with pytest.raises(IntegrationOperationError) as raised:
        google_auth.get_google_credentials(token_path, integration_id="gmail")

    assert raised.value.code == "persistence_error"
    assert raised.value.retryable is False
    assert "serialization detail" not in str(raised.value)


def test_google_refresh_atomically_replaces_token_with_private_mode(monkeypatch, tmp_path):
    token_path = tmp_path / "internal-account-id.json"
    token_path.write_text("old-token")
    token_path.chmod(0o644)

    class RefreshedCredentials:
        valid = False
        expired = True
        refresh_token = "refresh"
        scopes = ()

        def refresh(self, _request):
            self.valid = True

        def to_json(self):
            return "new-token"

    monkeypatch.setattr(
        google_auth.Credentials,
        "from_authorized_user_file",
        lambda _path: RefreshedCredentials(),
    )

    google_auth.get_google_credentials(token_path, integration_id="gmail")

    assert token_path.read_text() == "new-token"
    assert stat.S_IMODE(token_path.stat().st_mode) == 0o600
    assert list(tmp_path.glob(".internal-account-id.json.*.tmp")) == []


def test_google_refresh_atomic_failure_keeps_previous_token_and_cleans_temp(monkeypatch, tmp_path):
    token_path = tmp_path / "internal-account-id.json"
    token_path.write_text("old-token")

    class RefreshedCredentials:
        valid = False
        expired = True
        refresh_token = "refresh"
        scopes = ()

        def refresh(self, _request):
            self.valid = True

        def to_json(self):
            return "new-token"

    monkeypatch.setattr(
        google_auth.Credentials,
        "from_authorized_user_file",
        lambda _path: RefreshedCredentials(),
    )
    monkeypatch.setattr(
        google_storage.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("replace failed")),
    )

    with pytest.raises(IntegrationOperationError) as raised:
        google_auth.get_google_credentials(token_path, integration_id="gmail")

    assert raised.value.code == "persistence_error"
    assert token_path.read_text() == "old-token"
    assert list(tmp_path.glob(".internal-account-id.json.*.tmp")) == []


def test_google_connection_error_never_exposes_internal_token_filename(tmp_path):
    token_path = tmp_path / "internal-account-id.json"

    with pytest.raises(IntegrationConnectionError) as raised:
        google_auth.get_google_credentials(token_path, integration_id="gmail")

    assert "internal-account-id" not in raised.value.detail


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


@pytest.mark.asyncio
async def test_sync_connection_verification_runs_off_the_event_loop_thread():
    event_loop_thread = get_ident()
    verifier_thread = event_loop_thread

    class Client:
        def verify_connection(self):
            nonlocal verifier_thread
            verifier_thread = get_ident()

    registry = StubConnectionRegistry()
    registry.client = Client()

    await ConnectionService(registry).verify_connection("slack")

    assert verifier_thread != event_loop_thread


@pytest.mark.asyncio
async def test_connection_verification_targets_exact_provider_account():
    checked: list[str] = []

    class Client:
        async def verify_connection(self):
            raise AssertionError("account-targeted verification must use verify_account")

        async def verify_account(self, account_ref: str):
            checked.append(account_ref)

    registry = StubConnectionRegistry()
    registry.client = Client()

    await ConnectionService(registry).verify_connection("slack", account_ref="me@example.test")

    assert checked == ["me@example.test"]


@pytest.mark.asyncio
async def test_account_only_provider_client_violates_verifier_contract():
    class Client:
        async def verify_account(self, _account_ref: str):
            return None

    registry = StubConnectionRegistry()
    registry.client = Client()

    with pytest.raises(TypeError, match="must implement verify_connection"):
        await ConnectionService(registry).verify_connection("slack", account_ref="me@example.test")


@pytest.mark.asyncio
async def test_connection_verification_requires_provider_verifier():
    registry = StubConnectionRegistry()
    registry.client = object()

    with pytest.raises(TypeError, match="must implement verify_connection"):
        await ConnectionService(registry).verify_connection("slack")


@pytest.mark.asyncio
async def test_connected_provider_requires_registered_client():
    registry = StubConnectionRegistry()

    with pytest.raises(RuntimeError, match="connected but has no provider client"):
        await ConnectionService(registry).verify_connection("slack")


@pytest.mark.asyncio
async def test_account_verification_requires_account_verifier():
    class Client:
        def verify_connection(self):
            return None

    registry = StubConnectionRegistry()
    registry.client = Client()

    with pytest.raises(TypeError, match="must implement verify_account"):
        await ConnectionService(registry).verify_connection("slack", account_ref="me@example.test")


@pytest.mark.asyncio
async def test_connection_verification_does_not_reclassify_verifier_bugs():
    class Client:
        def verify_connection(self):
            raise RuntimeError("verifier bug")

    registry = StubConnectionRegistry()
    registry.client = Client()

    with pytest.raises(RuntimeError, match="verifier bug"):
        await ConnectionService(registry).verify_connection("slack")


def test_connection_recovery_descriptor_preserves_semantic_account_ref():
    descriptor = ConnectionService(StubConnectionRegistry()).recovery_descriptor(
        IntegrationConnectionError(
            integration_id="slack",
            reason="auth_required",
            detail="Reconnect this account.",
            account_ref="me@example.test",
        )
    )

    assert descriptor is not None
    assert descriptor.account_ref == "me@example.test"
