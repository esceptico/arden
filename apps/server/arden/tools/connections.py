from dataclasses import replace
from html import escape
from inspect import isawaitable

from pydantic import BaseModel, Field

from arden.integrations.base import IntegrationConnectionDescriptor, IntegrationConnectionError
from arden.integrations.registry import IntegrationRegistry
from arden.tools.core import ToolResult, tool
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import ToolAction, ToolPolicy, ToolScope


class ConnectionService:
    """Live view of registered integration capabilities and connection state."""

    def __init__(self, registry: IntegrationRegistry):
        self._registry = registry

    def list_connections(self) -> list[IntegrationConnectionDescriptor]:
        return self._registry.list_connections()

    def get_disconnected(self, integration_id: str) -> IntegrationConnectionDescriptor | None:
        descriptor = self._registry.get_connection(integration_id)
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
        )

    async def verify_connection(self, integration_id: str) -> IntegrationConnectionDescriptor:
        descriptor = self._registry.get_connection(integration_id)
        client = self._registry.get_client(integration_id)
        if descriptor is None or descriptor.state != "connected" or client is None:
            raise IntegrationConnectionError(
                integration_id=integration_id,
                reason=descriptor.state if descriptor and descriptor.state != "connected" else "degraded",
                detail=f"{descriptor.label if descriptor else integration_id} is not connected.",
            )
        check = getattr(client, "verify_connection", None)
        if check is None:
            return descriptor
        try:
            result = check()
            if isawaitable(result):
                await result
        except IntegrationConnectionError:
            raise
        except Exception as exc:
            raise IntegrationConnectionError(
                integration_id=integration_id,
                reason="degraded",
                detail=f"Could not verify {descriptor.label}.",
            ) from exc
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
            f'<integration integration_id="{escape(row.integration_id, quote=True)}" '
            f'connection_id="{escape(row.connection_id, quote=True)}" '
            f'state="{escape(row.state, quote=True)}">'
            f"{escape(row.capability, quote=True)}</integration>"
        )
        for row in disconnected
    )
    return (
        "## AVAILABLE CONNECTIONS\n"
        "Only request a connection when the user's explicit request requires it and no connected tool can "
        "satisfy the request. Do not suggest integrations speculatively. Use the exact integration_id below; "
        "never infer another integration or connection from keywords or error text.\n"
        "<available_connections>\n"
        f"{rows}\n"
        "</available_connections>"
    )


class ConnectionRequestInput(BaseModel):
    integration_id: str = Field(description="Exact integration_id from <available_connections>.")
    reason: str = Field(
        max_length=500,
        description="Short explanation of why the user's explicit request requires this capability.",
    )


async def connection_request(execution: ToolExecution, args: ConnectionRequestInput) -> ToolResult:
    service = execution.ctx.get_client("connections", ConnectionService)
    descriptor = service.get_disconnected(args.integration_id) if service else None
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
        action=ToolAction.READ,
        scope=ToolScope.EXTERNAL,
        permissions=frozenset({"connections"}),
    ),
    execute=connection_request,
)
