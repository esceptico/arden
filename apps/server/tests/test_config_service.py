from copy import deepcopy

import pytest

from arden.config import Config
from arden.llm.models import Model, Provider
from arden.services.config import ConfigService


def test_integration_enabled_uses_explicit_service_state():
    config = Config(
        _env_file=None,
        memory=False,
        integration_states={"gmail": False, "calendar": True},
    )

    assert config.integration_enabled("gmail") is False
    assert config.integration_enabled("calendar") is True
    assert config.integration_enabled("google_drive") is False


@pytest.mark.asyncio
async def test_config_service_rolls_back_nested_settings_and_reloads_runtime(monkeypatch):
    import arden.services.config as config_module

    persisted = {"provider_keys": {"openai": "old-key"}}
    reload_seen: list[dict] = []

    def load_settings() -> dict:
        return deepcopy(persisted)

    def save_settings(settings: dict) -> None:
        nonlocal persisted
        persisted = deepcopy(settings)

    async def reload_config() -> None:
        reload_seen.append(deepcopy(persisted))
        if len(reload_seen) == 1:
            raise RuntimeError("reload failed")

    monkeypatch.setattr(config_module, "load_user_settings", load_settings)
    monkeypatch.setattr(config_module, "save_user_settings", save_settings)

    service = ConfigService(on_config_change=reload_config)

    with pytest.raises(RuntimeError, match="reload failed"):
        await service.connect_provider("openai", "new-key")

    assert persisted == {"provider_keys": {"openai": "old-key"}}
    assert reload_seen == [
        {"provider_keys": {"openai": "new-key"}},
        {"provider_keys": {"openai": "old-key"}},
    ]


@pytest.mark.asyncio
async def test_config_service_persists_explicit_no_embeddings(monkeypatch):
    import arden.services.config as config_module

    persisted = {"embedding_model": "text-embedding-3-small"}

    monkeypatch.setattr(config_module, "load_user_settings", lambda: deepcopy(persisted))

    def save_settings(settings: dict) -> None:
        nonlocal persisted
        persisted = deepcopy(settings)

    monkeypatch.setattr(config_module, "save_user_settings", save_settings)

    service = ConfigService(on_config_change=lambda: _noop())
    await service.update(embedding_model=None)

    assert persisted == {"embedding_model": None}
    assert Config(openai_api_key="key", **persisted).embedding is None


async def _noop() -> None:
    return


@pytest.mark.asyncio
async def test_config_service_creates_custom_model_and_stores_api_key(monkeypatch):
    import arden.services.config as config_module

    persisted = {}
    added: list[dict] = []
    reload_seen: list[dict] = []

    def load_settings() -> dict:
        return deepcopy(persisted)

    def save_settings(settings: dict) -> None:
        nonlocal persisted
        persisted = deepcopy(settings)

    def add_model(**kwargs) -> Model:
        added.append(kwargs)
        return Model(
            id=kwargs["model_id"],
            provider=Provider.CUSTOM,
            max_context_tokens=kwargs["context_window"],
            max_output_tokens=kwargs["max_output_tokens"],
            base_url=kwargs["base_url"],
        )

    async def reload_config() -> None:
        reload_seen.append(deepcopy(persisted))

    monkeypatch.setattr(config_module, "load_user_settings", load_settings)
    monkeypatch.setattr(config_module, "save_user_settings", save_settings)
    monkeypatch.setattr(config_module, "add_custom_model", add_model)
    monkeypatch.setattr(config_module, "get_models_by_provider", lambda _provider: {})

    service = ConfigService(on_config_change=reload_config)

    model = await service.create_custom_model(
        model_id="local/test",
        base_url="http://localhost:11434/v1",
        context_window=8192,
        max_output_tokens=2048,
        api_key="secret",
    )

    assert model.id == "local/test"
    assert added == [
        {
            "model_id": "local/test",
            "base_url": "http://localhost:11434/v1",
            "context_window": 8192,
            "max_output_tokens": 2048,
        }
    ]
    assert persisted == {"custom_model_keys": {"local/test": "secret"}}
    assert reload_seen == [{"custom_model_keys": {"local/test": "secret"}}]


@pytest.mark.asyncio
async def test_config_service_deletes_custom_model_and_clears_active_fields(monkeypatch):
    import arden.services.config as config_module

    model = Model(
        id="local/test",
        provider=Provider.CUSTOM,
        max_context_tokens=8192,
        max_output_tokens=2048,
        base_url="http://localhost:11434/v1",
    )
    persisted = {
        "custom_model_keys": {"local/test": "secret", "other": "keep"},
        "chat_model": "local/test",
        "model_roles": {
            "research": {"model": "local/test", "reasoning_effort": "high"},
            "auxiliary": {"model": "local/test", "reasoning_effort": None},
            "memory": {"model": "other", "reasoning_effort": None},
        },
        "model_reasoning_efforts": {"local/test": "high", "other": "low"},
    }
    removed: list[str] = []
    reload_seen: list[dict] = []

    def load_settings() -> dict:
        return deepcopy(persisted)

    def save_settings(settings: dict) -> None:
        nonlocal persisted
        persisted = deepcopy(settings)

    async def reload_config() -> None:
        reload_seen.append(deepcopy(persisted))

    monkeypatch.setattr(config_module, "load_user_settings", load_settings)
    monkeypatch.setattr(config_module, "save_user_settings", save_settings)
    monkeypatch.setattr(config_module, "get_models_by_provider", lambda _provider: {"local/test": model})
    monkeypatch.setattr(config_module, "remove_custom_model", lambda model_id: removed.append(model_id))

    service = ConfigService(on_config_change=reload_config)

    await service.delete_custom_model("local/test")

    assert removed == ["local/test"]
    assert persisted == {
        "custom_model_keys": {"other": "keep"},
        "model_roles": {
            "memory": {"model": "other", "reasoning_effort": None},
        },
        "model_reasoning_efforts": {"other": "low"},
    }
    assert reload_seen == [persisted]


@pytest.mark.asyncio
async def test_writing_role_setups_retires_the_flat_legacy_keys(monkeypatch):
    """Config READS the old scalars so an existing install keeps its models, but
    once the role objects are written the file should have one source of truth."""
    import arden.services.config as config_module

    persisted = {
        "chat_model": "gpt-5.2",
        "research_model": "gpt-5.2",
        "research_reasoning_effort": "high",
        "memory_model": "gpt-5.2",
    }

    def load_settings() -> dict:
        return deepcopy(persisted)

    def save_settings(settings: dict) -> None:
        nonlocal persisted
        persisted = deepcopy(settings)

    async def reload_config() -> None:
        return None

    monkeypatch.setattr(config_module, "load_user_settings", load_settings)
    monkeypatch.setattr(config_module, "save_user_settings", save_settings)

    service = ConfigService(on_config_change=reload_config)
    await service.update(model_roles={"research": {"model": "gpt-5.2", "reasoning_effort": "low"}})

    assert persisted["model_roles"] == {"research": {"model": "gpt-5.2", "reasoning_effort": "low"}}
    for stale in ("research_model", "research_reasoning_effort", "memory_model"):
        assert stale not in persisted
    assert persisted["chat_model"] == "gpt-5.2"
