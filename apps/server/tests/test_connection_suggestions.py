import asyncio
from datetime import UTC, datetime

import pytest

from arden.context.models import SessionState
from arden.integrations.base import IntegrationConnectionDescriptor
from arden.tools.connections import (
    ConnectionService,
    RequestConnectionInput,
    render_connection_catalog,
    request_connection,
)
from arden.tools.core.context import BackgroundTaskRegistry, IOBridge, RunContext, ToolContext, ToolExecution
from arden.tools.core.registry import ToolRegistry


def _descriptor(
    integration_id: str = "gmail",
    *,
    state: str = "not_configured",
) -> IntegrationConnectionDescriptor:
    return IntegrationConnectionDescriptor(
        integration_id=integration_id,
        connection_id="google" if integration_id in {"gmail", "calendar"} else integration_id,
        label="Gmail" if integration_id == "gmail" else integration_id.title(),
        capability="Search, read, and send email",
        action="oauth",
        settings_tab="integrations",
        state=state,  # type: ignore[arg-type]
        tool_names=("emails", "read_email", "send_email"),
    )


def test_catalog_lists_only_registered_disconnected_connections():
    text = render_connection_catalog([_descriptor(), _descriptor("slack", state="connected")])

    assert 'integration_id="gmail"' in text
    assert 'integration_id="slack"' not in text
    assert "Only request a connection when the user's explicit request requires it" in text
    assert "Do not suggest integrations speculatively" in text


def test_catalog_escapes_provider_owned_copy():
    descriptor = _descriptor()
    descriptor = IntegrationConnectionDescriptor(**{**descriptor.__dict__, "capability": 'Read <mail> & "messages"'})

    text = render_connection_catalog([descriptor])

    assert "Read &lt;mail&gt; &amp; &quot;messages&quot;" in text


class _Registry:
    def __init__(self, descriptors: list[IntegrationConnectionDescriptor]):
        self._descriptors = descriptors

    def list_connections(self) -> list[IntegrationConnectionDescriptor]:
        return self._descriptors

    def get_connection(self, integration_id: str) -> IntegrationConnectionDescriptor | None:
        return next((row for row in self._descriptors if row.integration_id == integration_id), None)


class _Context:
    def __init__(self, service: ConnectionService):
        self._service = service

    def get_client(self, id: str, client_type):
        if id == "connections" and isinstance(self._service, client_type):
            return self._service
        return None


@pytest.mark.asyncio
async def test_request_connection_rejects_unknown_integration_without_prompting():
    service = ConnectionService(_Registry([_descriptor()]))  # type: ignore[arg-type]
    execution = ToolExecution(tool_id="call-1", tool_name="request_connection", ctx=_Context(service))  # type: ignore[arg-type]

    result = await request_connection(
        execution,
        RequestConnectionInput(integration_id="notion", reason="Need project data"),
    )

    assert result.is_error
    assert result.outcome is not None
    assert result.outcome.error is not None
    assert result.outcome.error.code == "connection_not_available"


@pytest.mark.asyncio
async def test_request_connection_rejects_already_connected_integration_without_prompting():
    service = ConnectionService(_Registry([_descriptor(state="connected")]))  # type: ignore[arg-type]
    execution = ToolExecution(tool_id="call-1", tool_name="request_connection", ctx=_Context(service))  # type: ignore[arg-type]

    result = await request_connection(
        execution,
        RequestConnectionInput(integration_id="gmail", reason="Need email"),
    )

    assert result.is_error
    assert result.outcome is not None
    assert result.outcome.error is not None
    assert result.outcome.error.code == "connection_not_available"


@pytest.mark.asyncio
async def test_accepted_connection_exposes_registered_tools_to_the_current_run():
    pending: dict[str, asyncio.Future] = {}

    async def emit(_event):
        return None

    service = ConnectionService(_Registry([_descriptor()]))  # type: ignore[arg-type]
    run = RunContext(
        run_id="run-1",
        deferred_tools_enabled=True,
        allowed_tool_names={"request_connection"},
    )
    ctx = ToolContext(
        session_state=SessionState(session_id="session-1", started_at=datetime.now(UTC)),
        registry=ToolRegistry(),
        run=run,
        io=IOBridge(emit=emit, pending_connections=pending),
        services={"connections": service},
        background_tasks=BackgroundTaskRegistry(session_id="session-1"),
    )
    execution = ToolExecution(tool_id="call-1", tool_name="request_connection", ctx=ctx)
    task = asyncio.create_task(
        request_connection(
            execution,
            RequestConnectionInput(integration_id="gmail", reason="Need email"),
        )
    )
    for _ in range(20):
        if pending:
            break
        await asyncio.sleep(0)
    pending["call-1"].set_result({"approved": True, "result": "connected"})

    result = await task

    assert not result.is_error
    assert run.allowed_tool_names == {"request_connection", "emails", "read_email", "send_email"}
    assert run.loaded_tools == {"emails", "read_email", "send_email"}
