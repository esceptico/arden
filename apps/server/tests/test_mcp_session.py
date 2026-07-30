import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from arden.mcp import session as session_module
from arden.mcp.models import HttpTransport, MCPServerConfig, StdioTransport
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


@pytest.mark.asyncio
async def test_http_transport_uses_mcp_v2_two_stream_contract(monkeypatch):
    class FakeHttpClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class FakeClientSession:
        def __init__(self, read, write):
            assert (read, write) == ("read", "write")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def initialize(self):
            return None

        async def list_tools(self):
            return SimpleNamespace(tools=[])

    @asynccontextmanager
    async def fake_streamable_http_client(url, *, http_client):
        assert url == "https://mcp.example.test"
        assert isinstance(http_client, FakeHttpClient)
        yield "read", "write"

    monkeypatch.setattr(session_module, "create_mcp_http_client", lambda **_kwargs: FakeHttpClient())
    monkeypatch.setattr(session_module, "streamable_http_client", fake_streamable_http_client)
    monkeypatch.setattr(session_module, "ClientSession", FakeClientSession)

    session = MCPServerSession(MCPServerConfig(name="remote", transport=HttpTransport(url="https://mcp.example.test")))
    session._shutdown.set()

    await session._run_http()

    assert session._ready.is_set()
    assert session.tools == []
