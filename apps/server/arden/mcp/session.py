import asyncio
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client
from mcp.types import CallToolResult
from mcp.types import Tool as McpTool

from arden.logging import get_logger
from arden.mcp.errors import describe_mcp_error
from arden.mcp.models import HttpTransport, MCPServerConfig, StdioTransport

_logger = get_logger(__name__)

_MAX_RECONNECT_RETRIES = 5
_MAX_BACKOFF = 60.0
_INITIAL_CONNECT_TIMEOUT = 15.0

_OAUTH_REAUTH_MSG = "OAuth tokens expired — re-authenticate via /mcp/servers/{name}/oauth"


def _is_oauth_error(exc: BaseException) -> bool:
    if isinstance(exc, BaseExceptionGroup):
        return any(_is_oauth_error(e) for e in exc.exceptions)
    name = type(exc).__name__
    msg = str(exc)
    return "OAuthFlowError" in name or "redirect handler" in msg


class MCPServerSession:
    """Manages a single MCP server connection in a long-lived asyncio Task.

    The entire lifecycle (connect → discover → serve → disconnect) runs in
    one task so that anyio cancel-scopes are entered/exited in the same
    context.  Includes automatic reconnection with exponential backoff when
    the connection drops after initial success.
    """

    def __init__(self, config: MCPServerConfig):
        self.config = config
        self._session: ClientSession | None = None
        self._all_tools: list[McpTool] = []
        self._tools: list[McpTool] = []
        self._task: asyncio.Task | None = None
        self._ready = asyncio.Event()
        self._shutdown = asyncio.Event()
        self._error: BaseException | None = None

    @property
    def name(self) -> str:
        return self.config.name

    @property
    def connected(self) -> bool:
        return self._session is not None

    @property
    def tools(self) -> list[McpTool]:
        return self._tools

    @property
    def all_tools(self) -> list[McpTool]:
        return self._all_tools

    # -- transport runners (run inside the long-lived task) -------------------

    async def _run_stdio(self) -> None:
        transport = self.config.transport
        assert isinstance(transport, StdioTransport)
        params = StdioServerParameters(
            command=transport.command,
            args=transport.args,
            env=transport.env,
        )
        async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
            await session.initialize()
            self._session = session
            await self._discover_tools()
            self._ready.set()
            await self._shutdown.wait()

    async def _run_http(self) -> None:
        transport = self.config.transport
        assert isinstance(transport, HttpTransport)
        if transport.auth == "oauth":
            from arden.mcp.oauth import OAuthOptions, create_oauth_provider, ensure_oauth_metadata

            opts = OAuthOptions(
                client_id=transport.client_id,
                client_secret=transport.client_secret,
                redirect_port=transport.redirect_port,
                scope=transport.scope,
                client_name=transport.client_name or "ARDEN",
            )
            await ensure_oauth_metadata(self.name, transport.url)
            oauth = create_oauth_provider(self.name, transport.url, opts)
            http_client = create_mcp_http_client(auth=oauth)
        else:
            http_client = create_mcp_http_client(headers=transport.headers or None)

        async with (
            http_client,
            streamable_http_client(transport.url, http_client=http_client) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            self._session = session
            await self._discover_tools()
            self._ready.set()
            await self._shutdown.wait()

    async def _discover_tools(self) -> None:
        if not self._session:
            return
        response = await self._session.list_tools()
        self._all_tools = response.tools
        whitelist = self.config.tools
        if whitelist is not None:
            allowed = set(whitelist)
            self._tools = [t for t in response.tools if t.name in allowed]
        else:
            self._tools = response.tools

    # -- long-lived task with reconnection -----------------------------------

    async def _run(self) -> None:
        retries = 0
        backoff = 1.0
        is_http = isinstance(self.config.transport, HttpTransport)

        while True:
            try:
                if is_http:
                    await self._run_http()
                else:
                    await self._run_stdio()
                break  # clean exit (shutdown requested)
            except asyncio.CancelledError:
                raise
            except BaseException as exc:
                self._session = None

                # First connection attempt failed — report and stop.
                if not self._ready.is_set():
                    if _is_oauth_error(exc):
                        self._error = RuntimeError(_OAUTH_REAUTH_MSG.format(name=self.name))
                    else:
                        self._error = RuntimeError(describe_mcp_error(exc))
                    self._ready.set()
                    return

                if self._shutdown.is_set():
                    return

                # OAuth errors can't be fixed by retrying.
                if _is_oauth_error(exc):
                    _logger.warning(
                        "MCP server %r: %s",
                        self.name,
                        _OAUTH_REAUTH_MSG.format(name=self.name),
                    )
                    return

                retries += 1
                if retries > _MAX_RECONNECT_RETRIES:
                    _logger.warning(
                        "MCP server %r failed after %d reconnect attempts: %s",
                        self.name,
                        _MAX_RECONNECT_RETRIES,
                        describe_mcp_error(exc),
                    )
                    return

                _logger.warning(
                    "MCP server %r connection lost (%d/%d), reconnecting in %.0fs: %s",
                    self.name,
                    retries,
                    _MAX_RECONNECT_RETRIES,
                    backoff,
                    describe_mcp_error(exc),
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF)

                if self._shutdown.is_set():
                    return

                # Reset ready so callers can await reconnect.
                self._ready.clear()
            finally:
                self._session = None

    # -- public API ----------------------------------------------------------

    async def connect(self) -> None:
        """Start the background task and wait until connected (or failed)."""
        self._task = asyncio.create_task(self._run())
        try:
            await asyncio.wait_for(self._ready.wait(), timeout=_INITIAL_CONNECT_TIMEOUT)
        except TimeoutError as exc:
            detail = f"MCP server {self.name!r} did not initialize within {_INITIAL_CONNECT_TIMEOUT:.0f}s"
            self._error = RuntimeError(detail)
            self._shutdown.set()
            if self._task and not self._task.done():
                self._task.cancel()
                try:
                    await self._task
                except BaseException:
                    pass
            self._task = None
            raise self._error from exc
        if self._error:
            raise self._error

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> CallToolResult:
        if not self._session:
            # Connection might be re-establishing — wait briefly.
            if self._task and not self._task.done():
                try:
                    await asyncio.wait_for(self._ready.wait(), timeout=30)
                except TimeoutError:
                    pass
            if not self._session:
                raise RuntimeError(f"MCP server {self.name!r} is not connected")
        return await self._session.call_tool(tool_name, arguments)

    async def close(self) -> None:
        self._shutdown.set()
        if self._task and not self._task.done():
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except TimeoutError:
                _logger.warning("MCP server %r shutdown timed out, cancelling", self.name)
                self._task.cancel()
                try:
                    await self._task
                except (asyncio.CancelledError, BaseException):
                    pass
        self._session = None
        self._all_tools = []
        self._tools = []
        self._task = None
