import time

import pytest

from arden.mcp import oauth
from arden.mcp.models import HttpTransport, parse_server_config
from arden.server.routers import mcp as mcp_router
from arden.server.routers.mcp import prepare_mcp_server_config
from arden.tools.core.types import ToolAction, ToolScope


def test_http_url_without_scheme_defaults_to_http():
    config = parse_server_config("obsidian", {"transport": "http", "url": "localhost:8008/mcp"})

    assert isinstance(config.transport, HttpTransport)
    assert config.transport.url == "http://localhost:8008/mcp"


def test_http_url_preserves_authorization_headers():
    config = parse_server_config(
        "obsidian",
        {
            "transport": "http",
            "url": "127.0.0.1:8008/mcp",
            "headers": {"Authorization": "Bearer <token>"},
        },
    )

    assert isinstance(config.transport, HttpTransport)
    assert config.transport.headers == {"Authorization": "Bearer <token>"}


def test_http_oauth_config_parses_public_options():
    config = parse_server_config(
        "figma",
        {
            "transport": "http",
            "url": "https://mcp.example.com/mcp",
            "auth": "oauth",
            "client_id": "client-123",
            "client_secret": "secret-123",
            "redirect_port": 8765,
            "scope": "mcp:connect",
            "client_name": "arden-test",
        },
    )

    assert isinstance(config.transport, HttpTransport)
    assert config.transport.auth == "oauth"
    assert config.transport.client_id == "client-123"
    assert config.transport.client_secret == "secret-123"
    assert config.transport.redirect_port == 8765
    assert config.transport.scope == "mcp:connect"
    assert config.transport.client_name == "arden-test"


def test_http_auth_rejects_unknown_mode():
    with pytest.raises(ValueError, match="http auth must be 'oauth'"):
        parse_server_config("figma", {"transport": "http", "url": "https://mcp.example.com/mcp", "auth": "basic"})


def test_prepare_mcp_server_config_preserves_hidden_http_secrets():
    config = prepare_mcp_server_config(
        "figma",
        {
            "transport": "http",
            "url": "https://mcp.example.com/mcp",
            "auth": "oauth",
            "client_id": "updated-client",
        },
        existing={
            "transport": "http",
            "url": "https://mcp.example.com/mcp",
            "auth": "oauth",
            "headers": {"X-Api-Key": "hidden"},
            "client_secret": "hidden-secret",
        },
    )

    assert config["client_id"] == "updated-client"
    assert config["client_secret"] == "hidden-secret"
    assert "headers" not in config


def test_prepare_mcp_server_config_merges_masked_headers():
    # The UI sends header keys with empty (masked) values for untouched headers.
    # An empty value keeps the existing secret; a non-empty value replaces it;
    # an omitted key is dropped.
    config = prepare_mcp_server_config(
        "figma",
        {
            "transport": "http",
            "url": "https://mcp.example.com/mcp",
            "headers": {"Authorization": "", "X-New": "fresh"},
        },
        existing={
            "transport": "http",
            "url": "https://mcp.example.com/mcp",
            "headers": {"Authorization": "Bearer secret", "X-Old": "drop"},
        },
    )

    assert config["headers"] == {"Authorization": "Bearer secret", "X-New": "fresh"}


def test_prepare_mcp_server_config_preserves_hidden_headers_for_header_auth():
    config = prepare_mcp_server_config(
        "figma",
        {
            "transport": "http",
            "url": "https://mcp.example.com/mcp",
        },
        existing={
            "transport": "http",
            "url": "https://mcp.example.com/mcp",
            "headers": {"X-Api-Key": "hidden"},
        },
    )

    assert config["headers"] == {"X-Api-Key": "hidden"}


def test_oauth_discovery_paths_include_resource_path():
    assert mcp_router._oauth_discovery_paths("/mcp") == [
        "/.well-known/oauth-authorization-server/mcp",
        "/mcp/.well-known/oauth-authorization-server",
        "/.well-known/oauth-authorization-server",
    ]


def test_oauth_provider_uses_full_url_as_resource_indicator(monkeypatch, tmp_path):
    # RFC 8707: the resource indicator must be the canonical MCP server URL
    # *including its path*. Stripping to the origin makes servers that bind
    # dynamic clients to the exact MCP resource (e.g. LangSmith) reject the
    # token request with `invalid_target`.
    monkeypatch.setattr(oauth, "OAUTH_DIR", tmp_path)
    provider = oauth.create_oauth_provider(
        "langsmith",
        "https://api.smith.langchain.com/mcp",
        oauth.OAuthOptions(),
    )

    assert provider.context.server_url == "https://api.smith.langchain.com/mcp"
    assert provider.context.get_resource_url() == "https://api.smith.langchain.com/mcp"


@pytest.mark.asyncio
async def test_expired_oauth_tokens_with_refresh_token_load_for_refresh(monkeypatch, tmp_path):
    monkeypatch.setattr(oauth, "OAUTH_DIR", tmp_path)
    storage = oauth.MCPTokenStorage("linear")
    storage._write(
        {
            "tokens": {
                "access_token": "expired-access",
                "token_type": "Bearer",
                "refresh_token": "refresh-token",
            },
            "expires_at": time.time() - 1,
        }
    )

    tokens = await storage.get_tokens()

    assert tokens is not None
    assert tokens.access_token == ""
    assert tokens.refresh_token == "refresh-token"


@pytest.mark.asyncio
async def test_prepare_mcp_server_config_for_save_auto_marks_discovered_oauth(monkeypatch):
    async def discover(url: str) -> bool:
        assert url == "https://mcp.linear.app/mcp"
        return True

    monkeypatch.setattr(mcp_router, "discover_mcp_oauth", discover)

    config = await mcp_router.prepare_mcp_server_config_for_save(
        "linear",
        {"transport": "http", "url": "https://mcp.linear.app/mcp"},
    )

    assert config["auth"] == "oauth"
    assert "headers" not in config


@pytest.mark.asyncio
async def test_prepare_mcp_server_config_for_save_skips_discovery_for_headers(monkeypatch):
    async def discover(url: str) -> bool:
        raise AssertionError("discovery should not run when explicit headers are configured")

    monkeypatch.setattr(mcp_router, "discover_mcp_oauth", discover)

    config = await mcp_router.prepare_mcp_server_config_for_save(
        "linear",
        {
            "transport": "http",
            "url": "https://mcp.linear.app/mcp",
            "headers": {"Authorization": "Bearer token"},
        },
    )

    assert "auth" not in config
    assert config["headers"] == {"Authorization": "Bearer token"}


def test_http_url_rejects_non_http_scheme():
    with pytest.raises(ValueError, match="http url must use http:// or https://"):
        parse_server_config("obsidian", {"transport": "http", "url": "ftp://localhost:8008/mcp"})


def test_http_url_rejects_missing_host():
    with pytest.raises(ValueError, match="http url must use http:// or https://"):
        parse_server_config("obsidian", {"transport": "http", "url": "http:///mcp"})


def test_parse_tool_policies():
    config = parse_server_config(
        "obsidian",
        {
            "transport": "http",
            "url": "127.0.0.1:8008/mcp",
            "tool_policies": {
                "search": {
                    "action": "read",
                    "scope": "external",
                    "requires_approval": False,
                    "permissions": ["mcp", "notes"],
                    "max_result_chars": 5000,
                }
            },
        },
    )

    policy = config.tool_policies["search"]
    assert policy.action is ToolAction.READ
    assert policy.scope is ToolScope.EXTERNAL
    assert policy.requires_approval is False
    assert policy.permissions == frozenset({"mcp", "notes"})
    assert policy.max_result_chars == 5000


def test_parse_tool_policies_rejects_partial_mutation_risk():
    with pytest.raises(ValueError, match="open_world, idempotent"):
        parse_server_config(
            "obsidian",
            {
                "transport": "http",
                "url": "127.0.0.1:8008/mcp",
                "tool_policies": {
                    "publish": {
                        "action": "write",
                        "scope": "external",
                        "requires_approval": True,
                        "destructive": False,
                    }
                },
            },
        )


def test_parse_tool_policies_accepts_complete_mutation_risk():
    config = parse_server_config(
        "obsidian",
        {
            "transport": "http",
            "url": "127.0.0.1:8008/mcp",
            "tool_policies": {
                "publish": {
                    "action": "write",
                    "scope": "external",
                    "requires_approval": True,
                    "destructive": False,
                    "open_world": False,
                    "idempotent": True,
                }
            },
        },
    )

    policy = config.tool_policies["publish"]
    assert (policy.destructive, policy.open_world, policy.idempotent) == (False, False, True)


def test_trust_tool_annotations_defaults_to_false():
    config = parse_server_config("obsidian", {"transport": "http", "url": "127.0.0.1:8008/mcp"})

    assert config.trust_tool_annotations is False


def test_mcp_server_name_must_fit_source_provider_contract():
    with pytest.raises(ValueError, match="provider exceeds 64"):
        parse_server_config("x" * 65, {"transport": "stdio", "command": "true"})
