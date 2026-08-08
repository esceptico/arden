from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from arden.llm.codex_catalog import CodexCatalogError
from arden.llm.models import Provider
from arden.llm.openai_codex_auth import OpenAICodexTokens
from arden.llm.openai_codex_catalog import parse_codex_catalog, refresh_codex_models


def _entry(
    slug: str,
    *,
    visibility: str = "list",
    supported_in_api: bool = True,
) -> dict:
    return {
        "slug": slug,
        "visibility": visibility,
        "supported_in_api": supported_in_api,
        "context_window": 372_000,
        "supported_reasoning_levels": [
            {"effort": "low"},
            {"effort": "medium"},
            {"effort": "high"},
        ],
        "supports_search_tool": True,
    }


def test_parse_codex_catalog_filters_visibility_and_api_support():
    models = parse_codex_catalog(
        {
            "models": [
                _entry("gpt-live"),
                _entry("gpt-hidden", visibility="hide"),
                _entry("gpt-no-api", supported_in_api=False),
            ]
        }
    )

    assert [model.id for model in models] == ["openai-codex/gpt-live"]
    assert models[0].provider == Provider.OPENAI_CODEX
    assert models[0].max_context_tokens == 372_000
    assert models[0].reasoning_efforts == ("low", "medium", "high")
    assert models[0].native_deferred_tools is True


def test_parse_codex_catalog_rejects_any_malformed_eligible_entry():
    with pytest.raises(CodexCatalogError, match="model 1 is invalid"):
        parse_codex_catalog(
            {
                "models": [
                    _entry("gpt-live"),
                    {"slug": "missing-context", "visibility": "list", "supported_in_api": True},
                ]
            }
        )


def test_parse_codex_catalog_rejects_duplicate_eligible_slugs():
    with pytest.raises(CodexCatalogError, match="duplicate slug"):
        parse_codex_catalog({"models": [_entry("gpt-live"), _entry("gpt-live")]})


class _FailingClient:
    async def get(self, *args, **kwargs):
        request = httpx.Request("GET", "https://chatgpt.com/backend-api/codex/models")
        raise httpx.ConnectError("down", request=request)


@pytest.mark.asyncio
async def test_refresh_failure_retains_existing_models(monkeypatch):
    replace = Mock()
    monkeypatch.setattr(
        "arden.llm.openai_codex_catalog.get_valid_tokens",
        AsyncMock(return_value=OpenAICodexTokens("access", "refresh", 123, "account")),
    )
    monkeypatch.setattr("arden.llm.openai_codex_catalog.replace_provider_models", replace)

    assert await refresh_codex_models(client=_FailingClient()) is False
    replace.assert_not_called()


class _CatalogResponse:
    def __init__(self, payload: dict | None = None):
        self.payload = payload or {"models": [_entry("gpt-live")]}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload


class _CatalogClient:
    def __init__(self, payload: dict | None = None):
        self.calls: list[tuple] = []
        self.payload = payload

    async def get(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return _CatalogResponse(self.payload)


@pytest.mark.asyncio
async def test_refresh_replaces_codex_provider(monkeypatch):
    client = _CatalogClient()
    replace = Mock(return_value=True)
    monkeypatch.setattr(
        "arden.llm.openai_codex_catalog.get_valid_tokens",
        AsyncMock(return_value=OpenAICodexTokens("access", "refresh", 123, "account")),
    )
    monkeypatch.setattr("arden.llm.openai_codex_catalog.replace_provider_models", replace)

    assert await refresh_codex_models(client=client) is True
    models = replace.call_args.args[1]
    assert replace.call_args.args[0] == Provider.OPENAI_CODEX
    assert [model.id for model in models] == ["openai-codex/gpt-live"]
    assert client.calls[0][1]["params"] == {"client_version": "0.147.0"}
    assert client.calls[0][1]["headers"]["Authorization"] == "Bearer access"


@pytest.mark.asyncio
async def test_refresh_rejects_partial_catalog_and_retains_packaged_models(monkeypatch):
    client = _CatalogClient(
        {
            "models": [
                _entry("gpt-live"),
                {"slug": "broken", "visibility": "list", "supported_in_api": True},
            ]
        }
    )
    replace = Mock()
    monkeypatch.setattr(
        "arden.llm.openai_codex_catalog.get_valid_tokens",
        AsyncMock(return_value=OpenAICodexTokens("access", "refresh", 123, "account")),
    )
    monkeypatch.setattr("arden.llm.openai_codex_catalog.replace_provider_models", replace)

    assert await refresh_codex_models(client=client) is False
    replace.assert_not_called()
