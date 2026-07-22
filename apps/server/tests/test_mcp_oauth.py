import json
import stat
import time

import pytest

from arden.mcp import oauth as oauth_module
from arden.mcp.oauth import MCPTokenStorage, OAuthOptions, create_oauth_provider, ensure_oauth_metadata

SERVER_URL = "https://api.smith.langchain.com/mcp"
TOKEN_ENDPOINT = "https://api.smith.langchain.com/oauth/token"


@pytest.fixture
def oauth_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(oauth_module, "OAUTH_DIR", tmp_path)
    return tmp_path


def _write_state(path, *, metadata: bool):
    state = {
        "client_info": {
            "client_id": "dcr_test",
            "redirect_uris": ["http://127.0.0.1:62927/callback"],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
        },
        "tokens": {"access_token": "stale", "refresh_token": "r3fr3sh", "expires_in": 300},
        "expires_at": time.time() - 60,  # already expired -> forces refresh path
    }
    if metadata:
        state["oauth_metadata"] = {
            "issuer": "https://api.smith.langchain.com",
            "authorization_endpoint": "https://api.smith.langchain.com/oauth/authorize",
            "token_endpoint": TOKEN_ENDPOINT,
        }
    path.write_text(json.dumps(state))


def test_metadata_roundtrip(oauth_dir):
    storage = MCPTokenStorage("langsmith")
    assert storage.get_metadata() is None
    _write_state(oauth_dir / "langsmith.json", metadata=True)
    meta = storage.get_metadata()
    assert meta is not None
    assert str(meta.token_endpoint) == TOKEN_ENDPOINT


def test_token_storage_rejects_path_traversal_server_name(oauth_dir):
    with pytest.raises(ValueError):
        MCPTokenStorage("../outside")


async def test_token_storage_uses_private_permissions(oauth_dir):
    from mcp.shared.auth import OAuthToken

    storage = MCPTokenStorage("langsmith")
    await storage.set_tokens(OAuthToken(access_token="tok", token_type="Bearer"))

    assert stat.S_IMODE(oauth_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE((oauth_dir / "langsmith.json").stat().st_mode) == 0o600


def test_provider_preloads_token_endpoint(oauth_dir):
    """Regression: refresh must target the discovered /oauth/token, not the
    guessed {host}/token that returns 404 after a reload."""
    _write_state(oauth_dir / "langsmith.json", metadata=True)
    provider = create_oauth_provider("langsmith", SERVER_URL, OAuthOptions())

    assert provider.context.oauth_metadata is not None
    assert provider._get_token_endpoint() == TOKEN_ENDPOINT


def test_provider_without_metadata_guesses_wrong_endpoint(oauth_dir):
    """Without preloaded metadata the SDK falls back to the 404-prone guess —
    this is the bug the preload fixes."""
    _write_state(oauth_dir / "langsmith.json", metadata=False)
    provider = create_oauth_provider("langsmith", SERVER_URL, OAuthOptions())

    assert provider.context.oauth_metadata is None
    assert provider._get_token_endpoint() == "https://api.smith.langchain.com/token"


async def test_ensure_metadata_noop_without_tokens(oauth_dir, monkeypatch):
    called = False

    async def _fail(_url):
        nonlocal called
        called = True
        return None

    monkeypatch.setattr(oauth_module, "_discover_oauth_metadata", _fail)
    await ensure_oauth_metadata("langsmith", SERVER_URL)  # no state file at all
    assert called is False


async def test_ensure_metadata_skips_when_already_present(oauth_dir, monkeypatch):
    _write_state(oauth_dir / "langsmith.json", metadata=True)
    called = False

    async def _fail(_url):
        nonlocal called
        called = True
        return None

    monkeypatch.setattr(oauth_module, "_discover_oauth_metadata", _fail)
    await ensure_oauth_metadata("langsmith", SERVER_URL)
    assert called is False


async def test_ensure_metadata_discovers_and_persists(oauth_dir, monkeypatch):
    from mcp.shared.auth import OAuthMetadata

    _write_state(oauth_dir / "langsmith.json", metadata=False)

    async def _discover(_url):
        return OAuthMetadata.model_validate(
            {
                "issuer": "https://api.smith.langchain.com",
                "authorization_endpoint": "https://api.smith.langchain.com/oauth/authorize",
                "token_endpoint": TOKEN_ENDPOINT,
            }
        )

    monkeypatch.setattr(oauth_module, "_discover_oauth_metadata", _discover)
    await ensure_oauth_metadata("langsmith", SERVER_URL)

    stored = MCPTokenStorage("langsmith").get_metadata()
    assert stored is not None
    assert str(stored.token_endpoint) == TOKEN_ENDPOINT
