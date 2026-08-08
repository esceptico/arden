from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from arden.config import Config
from arden.llm.models import CustomModelInputError, CustomModelsError, EmbeddingModel, Provider
from arden.server.app import app
from arden.server.routers import providers as provider_router
from arden.server.routers import settings as settings_router
from arden.server.schemas import AddCustomEmbeddingModelRequest, AddCustomModelRequest


def test_provider_routes_are_registered_once():
    paths = TestClient(app).get("/openapi.json").json()["paths"]

    assert "/providers" in paths
    assert "/providers/{provider_id}/connect" in paths
    assert "/models/custom/embedding" in paths
    assert "/models/custom/embedding/{model_id}" in paths
    assert "/services" in paths
    assert "/services/{service_id}/connect" in paths
    assert "/tool-providers" in paths
    assert "/setup/status" in paths
    assert "/setup/google/credentials" in paths
    assert "/setup/google/preflight" in paths
    assert "/setup/slack/verify" in paths
    assert "/google/{service}/connect" in paths


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
    assert custom["custom_embedding_models"] == [{"id": "local/embed", "base_url": "http://localhost", "dimensions": 3}]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route", "request_payload"),
    [
        (
            settings_router.create_custom_model,
            AddCustomModelRequest(model_id="local/test", base_url="http://localhost/v1", context_window=8192),
        ),
        (
            settings_router.create_custom_embedding_model,
            AddCustomEmbeddingModelRequest(
                model_id="local/embed",
                base_url="http://localhost/v1",
                dimensions=1024,
            ),
        ),
    ],
)
async def test_custom_model_routes_do_not_misreport_runtime_failures_as_bad_requests(route, request_payload):
    class FailingConfigService:
        async def create_custom_model(self, **_kwargs):
            raise CustomModelsError("models.json is corrupt")

        async def create_custom_embedding_model(self, **_kwargs):
            raise CustomModelsError("models.json is corrupt")

    with pytest.raises(CustomModelsError, match=r"models\.json is corrupt"):
        await route(request_payload, FailingConfigService())


@pytest.mark.asyncio
async def test_custom_model_route_reports_invalid_id_as_bad_request():
    class InvalidConfigService:
        async def create_custom_model(self, **_kwargs):
            raise CustomModelInputError("reserved model ID")

    request = AddCustomModelRequest(model_id="embedding", base_url="http://localhost/v1", context_window=8192)
    with pytest.raises(HTTPException) as raised:
        await settings_router.create_custom_model(request, InvalidConfigService())

    assert raised.value.status_code == 400
    assert raised.value.detail == "reserved model ID"
