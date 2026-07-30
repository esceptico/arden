from types import SimpleNamespace

import pytest

from arden.llm.models import EmbeddingModel, Model, Provider, provider_label
from arden.server.routers import settings as settings_router


def test_provider_labels_are_canonical_for_model_surfaces():
    assert provider_label(Provider.ANTHROPIC) == "Anthropic"
    assert provider_label(Provider.OPENAI) == "OpenAI"
    assert provider_label(Provider.OPENAI_CODEX) == "OpenAI Codex"
    assert provider_label(Provider.GOOGLE) == "Google"
    assert provider_label(Provider.OPENROUTER) == "OpenRouter"
    assert provider_label(Provider.CUSTOM) == "Custom"


@pytest.mark.asyncio
async def test_models_endpoint_includes_canonical_group_labels(monkeypatch):
    models = {
        "openai-codex/test": Model(
            id="openai-codex/test",
            provider=Provider.OPENAI_CODEX,
            max_context_tokens=128_000,
        )
    }
    monkeypatch.setattr(settings_router, "get_models_fn", lambda: models)
    monkeypatch.setattr(settings_router, "connected_providers", lambda config: {Provider.OPENAI_CODEX})
    runtime = SimpleNamespace(
        config=SimpleNamespace(
            chat_model="openai-codex/test",
            research_model="openai-codex/test",
            workflow_model="openai-codex/test",
            memory_model="openai-codex/test",
            auxiliary_model="openai-codex/test",
        )
    )

    payload = await settings_router.get_models(runtime)

    assert payload["groups"] == [
        {
            "provider": "openai-codex",
            "label": "OpenAI Codex",
            "models": ["openai-codex/test"],
        }
    ]


@pytest.mark.asyncio
async def test_models_endpoint_lists_connected_providers_only(monkeypatch):
    models = {
        "openai-codex/test": Model(
            id="openai-codex/test",
            provider=Provider.OPENAI_CODEX,
            max_context_tokens=128_000,
        ),
        "claude-sonnet-4-6": Model(
            id="claude-sonnet-4-6",
            provider=Provider.ANTHROPIC,
            max_context_tokens=200_000,
        ),
        "gemini-3-flash-preview": Model(
            id="gemini-3-flash-preview",
            provider=Provider.GOOGLE,
            max_context_tokens=1_000_000,
        ),
    }
    monkeypatch.setattr(settings_router, "get_models_fn", lambda: models)
    monkeypatch.setattr(settings_router, "connected_providers", lambda config: {Provider.OPENAI_CODEX})
    runtime = SimpleNamespace(
        config=SimpleNamespace(
            chat_model="openai-codex/test",
            research_model="openai-codex/test",
            workflow_model="openai-codex/test",
            # An active selection survives its provider's disconnect so the
            # picker can still label it.
            memory_model="gemini-3-flash-preview",
            auxiliary_model="openai-codex/test",
        )
    )

    payload = await settings_router.get_models(runtime)

    assert payload["models"] == ["openai-codex/test", "gemini-3-flash-preview"]
    assert {group["provider"] for group in payload["groups"]} == {"openai-codex", "google"}
    assert "claude-sonnet-4-6" not in payload["models"]


@pytest.mark.asyncio
async def test_embedding_catalog_lists_reachable_models_and_retains_current(monkeypatch):
    models = {
        "openai": EmbeddingModel("openai", Provider.OPENAI, 3),
        "google": EmbeddingModel("google", Provider.GOOGLE, 3),
    }
    monkeypatch.setattr(settings_router, "get_embedding_models_fn", lambda: models)
    monkeypatch.setattr(settings_router, "list_embedding_models", lambda: list(models))
    runtime = SimpleNamespace(
        config=SimpleNamespace(embedding_model="google"),
        embedding_model_available=lambda model_id: model_id == "openai",
    )

    payload = await settings_router.get_embedding_models(runtime)

    assert payload["models"] == ["openai", "google"]
    assert payload["groups"] == [
        {"provider": "openai", "label": "OpenAI", "models": ["openai"]},
        {"provider": "google", "label": "Google", "models": ["google"]},
    ]
