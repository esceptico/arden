import asyncio
from collections.abc import Callable
from dataclasses import replace
from html import escape
from inspect import iscoroutinefunction
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from arden.integrations.base import IntegrationConnectionDescriptor, IntegrationConnectionError
from arden.integrations.registry import IntegrationRegistry
from arden.tools.core import ToolResult, tool
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import ToolAction, ToolPolicy, ToolScope


@runtime_checkable
class _ConnectionVerifier(Protocol):
    def verify_connection(self) -> object: ...


@runtime_checkable
class _AccountConnectionVerifier(Protocol):
    def verify_account(self, account_ref: str) -> object: ...


async def _run_verifier(check: Callable[..., object], *args: str) -> None:
    if iscoroutinefunction(check):
        result = await check(*args)
    else:
        result = await asyncio.to_thread(check, *args)
    if result is not None:
        raise TypeError("Connection verifiers must return None")


class ConnectionService:
    """Live view of registered integration capabilities and connection state."""

    def __init__(self, registry: IntegrationRegistry):
        self._registry = registry

    def list_connections(self) -> list[IntegrationConnectionDescriptor]:
        return self._registry.list_connections()

    def get_disconnected(self, connection_ref: str) -> IntegrationConnectionDescriptor | None:
        descriptor = self._registry.get_connection(connection_ref)
        if descriptor is None or descriptor.state == "connected":
            return None
        return descriptor

    def recovery_descriptor(self, error: IntegrationConnectionError) -> IntegrationConnectionDescriptor | None:
        descriptor = self._registry.get_connection(error.integration_id)
        if descriptor is None:
            return None
        return replace(
            descriptor,
            state=error.reason,
            detail=error.detail,
            required_scopes=error.required_scopes or descriptor.required_scopes,
            account_ref=error.account_ref,
        )

    async def verify_connection(
        self,
        integration_id: str,
        *,
        account_ref: str | None = None,
    ) -> IntegrationConnectionDescriptor:
        descriptor = self._registry.get_connection(integration_id)
        if descriptor is None or descriptor.state != "connected":
            raise IntegrationConnectionError(
                integration_id=integration_id,
                reason=descriptor.state if descriptor and descriptor.state != "connected" else "degraded",
                detail=f"{descriptor.label if descriptor else integration_id} is not connected.",
            )
        client = self._registry.get_client(integration_id)
        if client is None:
            raise RuntimeError(f"{descriptor.label} is connected but has no provider client")
        if not isinstance(client, _ConnectionVerifier):
            raise TypeError(f"{type(client).__name__} must implement verify_connection")
        if account_ref is not None:
            if not isinstance(client, _AccountConnectionVerifier):
                raise TypeError(f"{type(client).__name__} must implement verify_account")
            check = client.verify_account
        else:
            check = client.verify_connection
        args = (account_ref,) if account_ref is not None else ()
        await _run_verifier(check, *args)
        return descriptor

    @staticmethod
    def expose_tools(execution: ToolExecution, descriptor: IntegrationConnectionDescriptor) -> None:
        allowed = execution.ctx.run.allowed_tool_names
        if allowed is not None:
            allowed.update(descriptor.tool_names)
        execution.ctx.run.loaded_tools.update(descriptor.tool_names)


def render_connection_catalog(descriptors: list[IntegrationConnectionDescriptor]) -> str | None:
    disconnected = [descriptor for descriptor in descriptors if descriptor.state != "connected"]
    if not disconnected:
        return None
    rows = "\n".join(
        (
            f'<integration connection_ref="{escape(row.integration_id, quote=True)}" '
            f'state="{escape(row.state, quote=True)}">'
            f"{escape(row.capability, quote=True)}</integration>"
        )
        for row in disconnected
    )
    return (
        "## AVAILABLE CONNECTIONS\n"
        "Only request a connection when the user's explicit request requires it and no connected tool can "
        "satisfy the request. Do not suggest integrations speculatively. Use the exact connection_ref below; "
        "never infer another integration or connection from keywords or error text.\n"
        "<available_connections>\n"
        f"{rows}\n"
        "</available_connections>"
    )


class ConnectionRequestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connection_ref: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
        description="Exact connection_ref from <available_connections>.",
    )
    reason: str = Field(
        max_length=500,
        description="Short explanation of why the user's explicit request requires this capability.",
    )


async def connection_request(execution: ToolExecution, args: ConnectionRequestInput) -> ToolResult:
    service = execution.ctx.get_client("connections", ConnectionService)
    if service is None:
        raise RuntimeError("Connection service is not configured")
    descriptor = service.get_disconnected(args.connection_ref)
    if descriptor is None:
        return ToolResult.failure(
            code="connection_not_available",
            message="That integration is not available for connection in this run.",
            preview="Connection unavailable",
            recovery_action="Use a connected tool or continue without this integration.",
        )

    accepted = await execution.request_connection(
        descriptor,
        source="suggestion",
        detail=args.reason,
    )
    if not accepted:
        return ToolResult(
            content=f"The user chose not to connect {descriptor.label}. Continue without it.",
            preview="Connection declined",
        )

    service.expose_tools(execution, descriptor)
    return ToolResult(
        content=f"{descriptor.label} is connected. Continue the request using its tools.",
        preview=f"Connected {descriptor.label}",
    )


connection_request_tool = tool(
    display_name="Request Connection",
    display_description="Ask the user to connect an integration.",
    description=(
        "Ask the user to connect one exact registered integration from <available_connections>. "
        "Use only when the user's explicit request requires that capability and no connected tool can satisfy it. "
        "Never call this for speculative recommendations or an integration absent from the allowlist."
    ),
    input_model=ConnectionRequestInput,
    policy=ToolPolicy(
        action=ToolAction.EXECUTE,
        scope=ToolScope.EXTERNAL,
        requires_user_interaction=True,
        permissions=frozenset({"connections"}),
        destructive=False,
        open_world=True,
        idempotent=False,
    ),
    execute=connection_request,
)
