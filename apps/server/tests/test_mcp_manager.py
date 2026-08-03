import asyncio
from types import SimpleNamespace

import pytest

from arden.mcp import manager as manager_module
from arden.mcp.manager import MCPManager


async def test_connect_propagates_cancellation_and_closes_in_flight_session(monkeypatch) -> None:
    closed = False

    class _Session:
        def __init__(self, _config) -> None:
            pass

        async def connect(self) -> None:
            raise asyncio.CancelledError

        async def close(self) -> None:
            nonlocal closed
            closed = True

    monkeypatch.setattr(manager_module, "parse_server_config", lambda _name, _raw: SimpleNamespace())
    monkeypatch.setattr(manager_module, "MCPServerSession", _Session)

    with pytest.raises(asyncio.CancelledError):
        await MCPManager().connect({"slow": {}})

    assert closed is True
