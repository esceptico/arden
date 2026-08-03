import asyncio

from arden.integrations.base import IntegrationHealth, ToolProviderStatus
from arden.logging import get_logger
from arden.mcp.errors import describe_mcp_error
from arden.mcp.models import parse_server_config
from arden.mcp.session import MCPServerSession
from arden.mcp.tool import MCPTool

_logger = get_logger(__name__)


class MCPManager:
    def __init__(self):
        self._sessions: dict[str, MCPServerSession] = {}
        self._tools: list[MCPTool] = []
        self._errors: dict[str, str] = {}

    @property
    def tools(self) -> list[MCPTool]:
        return self._tools

    @property
    def errors(self) -> dict[str, str]:
        return dict(self._errors)

    @property
    def sessions(self) -> dict[str, MCPServerSession]:
        return self._sessions

    async def connect(self, server_configs: dict[str, dict]) -> None:
        for name, raw in server_configs.items():
            if raw.get("enabled") is False:
                continue
            try:
                config = parse_server_config(name, raw)
            except ValueError as e:
                _logger.warning("Invalid MCP config for %r: %s", name, e)
                self._errors[name] = str(e)
                continue

            session = MCPServerSession(config)
            try:
                await session.connect()
                self._sessions[name] = session
                for mcp_tool in session.tools:
                    self._tools.append(
                        MCPTool(
                            name,
                            mcp_tool,
                            session,
                            policy=config.tool_policies.get(mcp_tool.name),
                            trust_annotations=config.trust_tool_annotations,
                        )
                    )
                _logger.info("MCP server %r connected", name, tools=len(session.tools))
            except asyncio.CancelledError:
                try:
                    await session.close()
                except BaseException:
                    pass
                raise
            except BaseException as e:
                detail = describe_mcp_error(e)
                _logger.warning("Failed to connect MCP server %r: %s", name, detail)
                self._errors[name] = detail
                try:
                    await session.close()
                except BaseException:
                    pass

    def list_providers(self) -> list[ToolProviderStatus]:
        out: list[ToolProviderStatus] = []
        for name, session in self._sessions.items():
            out.append(
                ToolProviderStatus(
                    id=f"mcp:{name}",
                    label=name,
                    kind="mcp",
                    health=IntegrationHealth(status="connected"),
                    tool_count=len(session.tools),
                )
            )
        for name, error in self._errors.items():
            if name in self._sessions:
                continue
            out.append(
                ToolProviderStatus(
                    id=f"mcp:{name}",
                    label=name,
                    kind="mcp",
                    health=IntegrationHealth(status="error", detail=error),
                    tool_count=0,
                )
            )
        return out

    async def close(self) -> None:
        for name, session in self._sessions.items():
            try:
                await session.close()
            except BaseException:
                _logger.warning("Error closing MCP server %r", name, exc_info=True)
        self._sessions.clear()
        self._tools.clear()
        self._errors.clear()
