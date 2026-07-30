import asyncio
import html
import json
import re
import time
import urllib.parse
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from threading import Event
from typing import Any

from mcp.client.auth import OAuthClientProvider
from mcp.client.auth.utils import (
    build_oauth_authorization_server_metadata_discovery_urls,
    build_protected_resource_metadata_discovery_urls,
    create_oauth_metadata_request,
    handle_auth_metadata_response,
    handle_protected_resource_response,
)
from mcp.client.streamable_http import create_mcp_http_client
from mcp.shared.auth import (
    AuthorizationCodeResult,
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthMetadata,
    OAuthToken,
)

from arden.logging import get_logger
from arden.settings import ARDEN_DIR

_logger = get_logger(__name__)

# Respect ARDEN_DIR like all other persistent state (was hardcoded to ~/.arden,
# which breaks when ARDEN_DIR is redirected, e.g. a remote/containerized server).
OAUTH_DIR = ARDEN_DIR / "mcp_oauth"
LOGIN_TIMEOUT = 120
_SERVER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _token_path(server_name: str) -> Path:
    if not _SERVER_NAME_RE.fullmatch(server_name):
        raise ValueError("Invalid MCP server name for OAuth token storage")
    return OAUTH_DIR / f"{server_name}.json"


class MCPTokenStorage:
    def __init__(self, server_name: str):
        self._path = _token_path(server_name)

    def _read(self) -> dict:
        if self._path.exists():
            try:
                return json.loads(self._path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _write(self, data: dict) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._path.parent.chmod(0o700)
        self._path.write_text(json.dumps(data, indent=2))
        self._path.chmod(0o600)

    async def get_tokens(self) -> OAuthToken | None:
        data = self._read()
        if not (tokens := data.get("tokens")):
            return None
        # If access token has expired, make it falsy so the SDK falls into
        # the refresh path instead of sending a stale token and hitting 401.
        expires_at = data.get("expires_at")
        if expires_at and time.time() > expires_at:
            if not tokens.get("refresh_token"):
                return None
            tokens = {**tokens, "access_token": ""}
        return OAuthToken(**tokens)

    async def set_tokens(self, tokens: OAuthToken) -> None:
        data = self._read()
        dumped = tokens.model_dump(mode="json", exclude_none=True)
        data["tokens"] = dumped
        if tokens.expires_in:
            data["expires_at"] = time.time() + tokens.expires_in
        self._write(data)

    def get_metadata(self) -> OAuthMetadata | None:
        if meta := self._read().get("oauth_metadata"):
            return OAuthMetadata.model_validate(meta)
        return None

    def set_metadata(self, metadata: OAuthMetadata) -> None:
        data = self._read()
        data["oauth_metadata"] = metadata.model_dump(mode="json", exclude_none=True)
        self._write(data)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        data = self._read()
        if client := data.get("client_info"):
            return OAuthClientInformationFull(**client)
        return None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        data = self._read()
        data["client_info"] = client_info.model_dump(mode="json", exclude_none=True)
        self._write(data)

    def clear(self) -> None:
        if self._path.exists():
            self._path.unlink()


@dataclass(frozen=True)
class OAuthOptions:
    client_id: str | None = None
    client_secret: str | None = None
    redirect_port: int | None = None
    scope: str | None = None
    client_name: str = "ARDEN"


def _build_client_metadata(redirect_uri: str, opts: OAuthOptions) -> OAuthClientMetadata:
    auth_method = "client_secret_post" if opts.client_secret else "none"
    kwargs: dict[str, Any] = {
        "client_name": opts.client_name,
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": auth_method,
    }
    if opts.scope:
        kwargs["scope"] = opts.scope
    return OAuthClientMetadata.model_validate(kwargs)


def _seed_client_info(storage: "MCPTokenStorage", redirect_uri: str, opts: OAuthOptions) -> None:
    """For providers without dynamic client registration (e.g. Slack), seed
    the storage with pre-registered credentials so the SDK skips DCR."""
    if not opts.client_id:
        return
    info_dict: dict[str, Any] = {
        "client_id": opts.client_id,
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "client_secret_post" if opts.client_secret else "none",
    }
    if opts.client_secret:
        info_dict["client_secret"] = opts.client_secret
    if opts.scope:
        info_dict["scope"] = opts.scope
    info = OAuthClientInformationFull.model_validate(info_dict)
    data = storage._read()
    data["client_info"] = info.model_dump(mode="json", exclude_none=True)
    storage._write(data)


def create_oauth_provider(server_name: str, server_url: str, opts: OAuthOptions) -> OAuthClientProvider:
    """Create a provider that reuses stored tokens but cannot start a new OAuth flow.

    Used during session connect — if tokens are valid, they're attached
    automatically. If re-auth is needed, the connection fails and the user
    must re-authenticate via the /mcp/servers/{name}/oauth endpoint.
    """
    storage = MCPTokenStorage(server_name)
    redirect_uri = f"http://127.0.0.1:{opts.redirect_port or 0}/callback"
    _seed_client_info(storage, redirect_uri, opts)
    provider = OAuthClientProvider(
        server_url=server_url,
        client_metadata=_build_client_metadata(redirect_uri, opts),
        storage=storage,
        redirect_handler=None,
        callback_handler=None,
    )
    # The SDK only persists tokens + client_info; the discovered authorization
    # server metadata (with the real token_endpoint) lives in memory and is lost
    # on reload. Without it, token refresh guesses {host}/token and 404s. Seed it
    # so refresh targets the correct endpoint. _initialize() leaves it untouched.
    if metadata := storage.get_metadata():
        provider.context.oauth_metadata = metadata
    return provider


async def _discover_oauth_metadata(server_url: str) -> OAuthMetadata | None:
    """Replicate the SDK's discovery (steps 1-2 of its 401 flow) over the public
    well-known endpoints so we can persist the token endpoint ahead of time."""
    async with create_mcp_http_client() as client:
        auth_server_url: str | None = None
        for url in build_protected_resource_metadata_discovery_urls(None, server_url):
            prm = await handle_protected_resource_response(await client.send(create_oauth_metadata_request(url)))
            if prm:
                auth_server_url = str(prm.authorization_servers[0])
                break

        for url in build_oauth_authorization_server_metadata_discovery_urls(auth_server_url, server_url):
            ok, asm = await handle_auth_metadata_response(await client.send(create_oauth_metadata_request(url)))
            if not ok:
                break
            if asm:
                return asm
    return None


async def ensure_oauth_metadata(server_name: str, server_url: str) -> None:
    """Self-heal connections authenticated before metadata was persisted: if we
    hold tokens but no metadata, discover and store it so the next refresh works."""
    storage = MCPTokenStorage(server_name)
    data = storage._read()
    if not data.get("tokens") or data.get("oauth_metadata"):
        return
    if metadata := await _discover_oauth_metadata(server_url):
        storage.set_metadata(metadata)


def run_mcp_oauth(server_name: str, server_url: str, opts: OAuthOptions) -> None:
    """Run a full interactive OAuth flow: clear old tokens, open browser, wait for callback."""
    code_result: dict[str, Any] = {}
    done = Event()

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return

            params = urllib.parse.parse_qs(parsed.query)
            if "code" in params:
                code_result["code"] = params["code"][0]
                code_result["state"] = params.get("state", [None])[0]
                self.send_response(200)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(b"<html><body><h2>Connected! You can close this tab.</h2></body></html>")
            else:
                error = params.get("error", ["unknown"])[0]
                code_result["error"] = error
                self.send_response(400)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(f"<html><body><h2>Error: {html.escape(error)}</h2></body></html>".encode())
            done.set()

        def log_message(self, format, *args):
            pass

    server = HTTPServer(("127.0.0.1", opts.redirect_port or 0), CallbackHandler)
    port = server.server_address[1]
    redirect_uri = f"http://127.0.0.1:{port}/callback"

    async def redirect_handler(url: str) -> None:
        _logger.info("Opening browser for MCP OAuth (server=%r, port=%d)", server_name, port)
        webbrowser.open(url)

    async def callback_handler() -> AuthorizationCodeResult:
        server.timeout = 5
        deadline = time.time() + LOGIN_TIMEOUT
        while not done.is_set():
            if time.time() > deadline:
                server.server_close()
                raise RuntimeError("OAuth timed out — no callback received within 120s")
            server.handle_request()
        server.server_close()

        if "error" in code_result:
            raise RuntimeError(f"OAuth failed: {code_result['error']}")
        if "code" not in code_result:
            raise RuntimeError("OAuth timed out — no authorization code received")
        return AuthorizationCodeResult(code=code_result["code"], state=code_result.get("state"))

    storage = MCPTokenStorage(server_name)
    storage.clear()
    _seed_client_info(storage, redirect_uri, opts)

    provider = OAuthClientProvider(
        server_url=server_url,
        client_metadata=_build_client_metadata(redirect_uri, opts),
        storage=storage,
        redirect_handler=redirect_handler,
        callback_handler=callback_handler,
    )

    loop = asyncio.new_event_loop()
    try:

        async def _run():
            async with create_mcp_http_client(auth=provider) as client:
                resp = await client.get(server_url)
                _logger.info("MCP OAuth completed for %r (status=%d)", server_name, resp.status_code)

        loop.run_until_complete(_run())
        if provider.context.oauth_metadata:
            storage.set_metadata(provider.context.oauth_metadata)
    finally:
        loop.close()


def clear_tokens(server_name: str) -> None:
    path = _token_path(server_name)
    if path.exists():
        path.unlink()
