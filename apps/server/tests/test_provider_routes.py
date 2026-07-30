from types import SimpleNamespace

from fastapi.testclient import TestClient

from arden.config import Config
from arden.llm.models import EmbeddingModel, Provider
from arden.server.app import app
from arden.server.routers import providers as provider_router


def test_provider_routes_are_registered_once():
    paths = TestClient(app).get("/openapi.json").json()["paths"]

    assert "/providers" in paths
    assert "/providers/{provider_id}/connect" in paths
    assert "/services" in paths
    assert "/services/{service_id}/connect" in paths
    assert "/tool-providers" in paths
    assert "/setup/status" in paths
    assert "/setup/google/credentials" in paths
    assert "/setup/google/preflight" in paths
    assert "/setup/slack/verify" in paths
    assert "/gmail/add" in paths


async def test_custom_embedding_only_provider_is_connected(monkeypatch):
    monkeypatch.setattr(provider_router, "get_models_by_provider", lambda _provider: {})
    monkeypatch.setattr(
        provider_router,
        "get_embedding_models_by_provider",
        lambda provider: (
            {"local/embed": EmbeddingModel("local/embed", provider, 3, base_url="http://localhost")}
            if provider is Provider.CUSTOM
            else {}
        ),
    )
    monkeypatch.setattr(provider_router, "load_tokens", lambda: None)

    payload = await provider_router.get_providers(SimpleNamespace(config=Config(memory=False)))

    custom = next(provider for provider in payload["providers"] if provider["id"] == "custom")
    assert custom["connected"] is True
    assert custom["embedding_models"] == ["local/embed"]
