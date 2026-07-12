import asyncio
import base64
import hashlib
import html
import json
import platform
import secrets
import threading
import time
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from ntrp.settings import NTRP_DIR

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
ISSUER = "https://auth.openai.com"
CODEX_BASE_URL = "https://chatgpt.com/backend-api/codex"
CODEX_CLIENT_VERSION = "0.144.0"
OAUTH_PORT = 1455
OAUTH_CALLBACK_PATH = "/auth/callback"
TOKEN_PATH = NTRP_DIR / "openai-codex-auth.json"
_TOKEN_REFRESH_MARGIN_MS = 30_000


@dataclass(frozen=True)
class OpenAICodexTokens:
    access: str
    refresh: str
    expires: int
    account_id: str | None = None

    @property
    def expired(self) -> bool:
        return self.expires <= _now_ms() + _TOKEN_REFRESH_MARGIN_MS

    def to_json(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "type": "oauth",
            "access": self.access,
            "refresh": self.refresh,
            "expires": self.expires,
        }
        if self.account_id:
            data["accountId"] = self.account_id
        return data


def codex_request_headers(tokens: OpenAICodexTokens) -> dict[str, str]:
    headers = {
        "originator": "codex_cli_rs",
        "User-Agent": (
            f"codex_cli_rs/{CODEX_CLIENT_VERSION} "
            f"({platform.system().lower()} {platform.release()}; {platform.machine()})"
        ),
    }
    if tokens.account_id:
        headers["ChatGPT-Account-Id"] = tokens.account_id
    return headers


@dataclass
class _PendingLogin:
    state: str
    verifier: str
    redirect_uri: str
    url: str
    started_at: int
    opened: bool
    status: str = "pending"
    error: str | None = None


_lock = threading.Lock()
_server: ThreadingHTTPServer | None = None
_server_thread: threading.Thread | None = None
_pending: _PendingLogin | None = None


def _now_ms() -> int:
    return int(time.time() * 1000)


def _base64_url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _generate_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:86]
    challenge = _base64_url(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


def _decode_jwt_claims(token: str) -> dict[str, Any] | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload.encode())
        claims = json.loads(raw)
    except (ValueError, OSError):
        return None
    return claims if isinstance(claims, dict) else None


def _extract_account_id_from_claims(claims: dict[str, Any]) -> str | None:
    if value := claims.get("chatgpt_account_id"):
        return str(value)
    nested = claims.get("https://api.openai.com/auth")
    if isinstance(nested, dict) and (value := nested.get("chatgpt_account_id")):
        return str(value)
    orgs = claims.get("organizations")
    if isinstance(orgs, list) and orgs:
        first = orgs[0]
        if isinstance(first, dict) and first.get("id"):
            return str(first["id"])
    return None


def extract_account_id(token_response: dict[str, Any]) -> str | None:
    for key in ("id_token", "access_token"):
        token = token_response.get(key)
        if isinstance(token, str) and (claims := _decode_jwt_claims(token)):
            if account_id := _extract_account_id_from_claims(claims):
                return account_id
    return None


def _tokens_from_response(data: dict[str, Any], *, fallback_refresh: str | None = None) -> OpenAICodexTokens:
    access = data.get("access_token")
    refresh = data.get("refresh_token") or fallback_refresh
    if not isinstance(access, str) or not access:
        raise RuntimeError("OpenAI auth did not return an access token")
    if not isinstance(refresh, str) or not refresh:
        raise RuntimeError("OpenAI auth did not return a refresh token")
    expires_in = data.get("expires_in")
    if not isinstance(expires_in, int):
        expires_in = 3600
    return OpenAICodexTokens(
        access=access,
        refresh=refresh,
        expires=_now_ms() + expires_in * 1000,
        account_id=extract_account_id(data),
    )


def load_tokens() -> OpenAICodexTokens | None:
    if not TOKEN_PATH.exists():
        return None
    try:
        raw = json.loads(TOKEN_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    access = raw.get("access")
    refresh = raw.get("refresh")
    expires = raw.get("expires")
    if not isinstance(access, str) or not isinstance(refresh, str) or not isinstance(expires, int):
        return None
    account_id = raw.get("accountId")
    return OpenAICodexTokens(
        access=access,
        refresh=refresh,
        expires=expires,
        account_id=str(account_id) if account_id else None,
    )


def save_tokens(tokens: OpenAICodexTokens) -> None:
    NTRP_DIR.mkdir(exist_ok=True)
    TOKEN_PATH.write_text(json.dumps(tokens.to_json(), indent=2))
    TOKEN_PATH.chmod(0o600)


def clear_tokens() -> None:
    try:
        TOKEN_PATH.unlink()
    except FileNotFoundError:
        pass


def is_authenticated() -> bool:
    return load_tokens() is not None


async def _post_token_form(form: dict[str, str]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{ISSUER}/oauth/token",
            data=form,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if response.status_code >= 400:
        body = response.text[:500]
        raise RuntimeError(f"OpenAI token request failed ({response.status_code}): {body}")
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("OpenAI token request returned a non-object response")
    return data


async def exchange_code_for_tokens(code: str, redirect_uri: str, verifier: str) -> OpenAICodexTokens:
    data = await _post_token_form(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": CLIENT_ID,
            "code_verifier": verifier,
        }
    )
    return _tokens_from_response(data)


async def refresh_tokens(tokens: OpenAICodexTokens) -> OpenAICodexTokens:
    data = await _post_token_form(
        {
            "grant_type": "refresh_token",
            "refresh_token": tokens.refresh,
            "client_id": CLIENT_ID,
        }
    )
    refreshed = _tokens_from_response(data, fallback_refresh=tokens.refresh)
    if refreshed.account_id is None and tokens.account_id:
        refreshed = OpenAICodexTokens(
            access=refreshed.access,
            refresh=refreshed.refresh,
            expires=refreshed.expires,
            account_id=tokens.account_id,
        )
    save_tokens(refreshed)
    return refreshed


async def get_valid_tokens() -> OpenAICodexTokens:
    tokens = load_tokens()
    if tokens is None:
        raise RuntimeError("OpenAI Codex provider is not connected")
    if tokens.expired:
        return await refresh_tokens(tokens)
    return tokens


def _authorize_url(redirect_uri: str, verifier: str, challenge: str, state: str) -> str:
    query = urlencode(
        {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": redirect_uri,
            "scope": "openid profile email offline_access",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "id_token_add_organizations": "true",
            "codex_cli_simplified_flow": "true",
            "state": state,
            "originator": "ntrp",
        }
    )
    return f"{ISSUER}/oauth/authorize?{query}"


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        return

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/cancel":
            _finish_pending("cancelled", "Login cancelled")
            self._html(200, "Authorization Cancelled", "You can close this window and return to ntrp.", error=True)
            _stop_server_async()
            return
        if parsed.path != OAUTH_CALLBACK_PATH:
            self.send_error(404)
            return

        params = parse_qs(parsed.query)
        if error := _first(params, "error"):
            detail = _first(params, "error_description") or error
            _finish_pending("failed", detail)
            self._html(200, "Authorization Failed", detail, error=True)
            _stop_server_async()
            return

        code = _first(params, "code")
        state = _first(params, "state")
        if not code:
            _finish_pending("failed", "Missing authorization code")
            self._html(400, "Authorization Failed", "Missing authorization code", error=True)
            _stop_server_async()
            return

        with _lock:
            pending = _pending
        if pending is None or state != pending.state:
            _finish_pending("failed", "Invalid OAuth state")
            self._html(400, "Authorization Failed", "Invalid OAuth state", error=True)
            _stop_server_async()
            return

        try:
            tokens = asyncio.run(exchange_code_for_tokens(code, pending.redirect_uri, pending.verifier))
            save_tokens(tokens)
            _finish_pending("connected", None)
            self._html(200, "Authorization Successful", "You can close this window and return to ntrp.")
        except Exception as exc:
            message = str(exc)
            _finish_pending("failed", message)
            self._html(500, "Authorization Failed", message, error=True)
        finally:
            _stop_server_async()

    def _html(self, status: int, title: str, message: str, *, error: bool = False) -> None:
        fg = "#fc533a" if error else "#f1ecec"
        body = f"""<!doctype html>
<html>
  <head>
    <title>ntrp - OpenAI Authorization</title>
    <style>
      body {{
        font-family: system-ui, -apple-system, sans-serif;
        display: flex;
        justify-content: center;
        align-items: center;
        height: 100vh;
        margin: 0;
        background: #131010;
        color: #f1ecec;
      }}
      .container {{ text-align: center; padding: 2rem; }}
      h1 {{ color: {fg}; margin-bottom: 1rem; }}
      p {{ color: #b7b1b1; max-width: 44rem; }}
    </style>
  </head>
  <body>
    <div class="container">
      <h1>{html.escape(title)}</h1>
      <p>{html.escape(message)}</p>
    </div>
    <script>setTimeout(() => window.close(), 2000)</script>
  </body>
</html>"""
        raw = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def _first(params: dict[str, list[str]], key: str) -> str | None:
    values = params.get(key)
    return values[0] if values else None


def _finish_pending(status: str, error: str | None) -> None:
    with _lock:
        if _pending:
            _pending.status = status
            _pending.error = error


def _stop_server_async() -> None:
    if _server is not None:
        threading.Thread(target=_shutdown_server, daemon=True).start()


def _shutdown_server() -> None:
    global _server, _server_thread

    server = _server
    if server is None:
        return
    try:
        server.shutdown()
        server.server_close()
    finally:
        with _lock:
            if _server is server:
                _server = None
                _server_thread = None


def _start_server() -> None:
    global _server, _server_thread
    if _server is not None:
        return
    server = ThreadingHTTPServer(("127.0.0.1", OAUTH_PORT), _OAuthCallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _server = server
    _server_thread = thread


def start_browser_login() -> dict[str, Any]:
    global _pending

    verifier, challenge = _generate_pkce()
    state = _base64_url(secrets.token_bytes(32))
    redirect_uri = f"http://localhost:{OAUTH_PORT}{OAUTH_CALLBACK_PATH}"
    url = _authorize_url(redirect_uri, verifier, challenge, state)

    with _lock:
        pending = _pending
        if pending and pending.status == "pending":
            return {
                "status": pending.status,
                "url": pending.url,
                "opened": pending.opened,
                "expires_at": pending.started_at + 5 * 60 * 1000,
            }

    _start_server()
    with _lock:
        _pending = _PendingLogin(
            state=state,
            verifier=verifier,
            redirect_uri=redirect_uri,
            url=url,
            started_at=_now_ms(),
            opened=False,
        )
    opened = bool(webbrowser.open(url, new=1, autoraise=True))
    with _lock:
        if _pending and _pending.state == state:
            _pending.opened = opened

    return {
        "status": "pending",
        "url": url,
        "opened": opened,
        "expires_at": _now_ms() + 5 * 60 * 1000,
        "instructions": "Complete the OpenAI sign-in in your browser.",
    }


def login_status() -> dict[str, Any]:
    if tokens := load_tokens():
        return {
            "connected": True,
            "status": "connected",
            "account_id": tokens.account_id,
            "expires": tokens.expires,
        }
    with _lock:
        pending = _pending
        if pending is None:
            return {"connected": False, "status": "idle"}
        return {
            "connected": False,
            "status": pending.status,
            "error": pending.error,
            "url": pending.url,
            "opened": pending.opened,
            "expires_at": pending.started_at + 5 * 60 * 1000,
        }
