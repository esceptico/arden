import asyncio

import pytest

from arden.mcp import session as session_module
from arden.mcp.models import MCPServerConfig, StdioTransport
from arden.mcp.session import MCPServerSession


class HangingMCPServerSession(MCPServerSession):
    async def _run(self) -> None:
        await asyncio.Event().wait()


@pytest.mark.asyncio
async def test_initial_connect_times_out(monkeypatch):
    monkeypatch.setattr(session_module, "_INITIAL_CONNECT_TIMEOUT", 0.01)
    session = HangingMCPServerSession(MCPServerConfig(name="stuck", transport=StdioTransport(command="noop")))

    with pytest.raises(RuntimeError, match="MCP server 'stuck' did not initialize within 0s"):
        await session.connect()

    assert session.connected is False
    assert session._task is None
