from types import SimpleNamespace

from arden.llm import router
from arden.llm.models import Model, Provider


def test_reinitializing_router_drops_removed_custom_model_keys(monkeypatch):
    custom = Model(
        id="local/test",
        provider=Provider.CUSTOM,
        max_context_tokens=8192,
        base_url="http://localhost/v1",
    )
    settings = iter([{"custom_model_keys": {custom.id: "old-secret"}}, {}])
    captured_keys: list[str | None] = []

    class Client:
        def __init__(self, *, api_key=None, **_kwargs):
            captured_keys.append(api_key)

    config = SimpleNamespace(
        anthropic_api_key=None,
        openai_api_key=None,
        gemini_api_key=None,
        openrouter_api_key=None,
        model_extra={},
    )
    monkeypatch.setattr(router, "_api_keys", {})
    monkeypatch.setattr(router, "load_user_settings", lambda: next(settings))
    monkeypatch.setattr(router, "get_models", lambda: {custom.id: custom})
    monkeypatch.setattr(router, "get_embedding_models", lambda: {})
    monkeypatch.setattr(router, "get_model", lambda _model_id: custom)
    monkeypatch.setattr(router, "OpenAIClient", Client)

    router.init(config)
    router.init(config)
    router.create_completion_client(custom.id)

    assert captured_keys == ["unused"]
