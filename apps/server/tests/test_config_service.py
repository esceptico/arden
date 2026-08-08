import asyncio
import json
import os
from copy import deepcopy

import pytest

from arden.config import Config
from arden.llm.models import EmbeddingModel, Model, Provider
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
    monkeypatch.setattr(config_module, "restore_user_settings", save_settings)

    service = ConfigService(on_config_change=reload_config)

    with pytest.raises(RuntimeError, match="reload failed"):
        await service.connect_provider("openai", "new-key")

    assert persisted == {"provider_keys": {"openai": "old-key"}}
    assert reload_seen == [
        {"provider_keys": {"openai": "new-key"}},
        {"provider_keys": {"openai": "old-key"}},
    ]


@pytest.mark.asyncio
async def test_config_service_surfaces_reload_and_rollback_failures(monkeypatch):
    import arden.services.config as config_module

    persisted = {"provider_keys": {"openai": "old-key"}}
    reload_count = 0

    monkeypatch.setattr(config_module, "load_user_settings", lambda: deepcopy(persisted))

    def save_settings(settings: dict) -> None:
        nonlocal persisted
        persisted = deepcopy(settings)

    async def reload_config() -> None:
        nonlocal reload_count
        reload_count += 1
        if reload_count == 1:
            raise RuntimeError("reload failed")
        raise OSError("rollback reload failed")

    monkeypatch.setattr(config_module, "save_user_settings", save_settings)
    monkeypatch.setattr(config_module, "restore_user_settings", save_settings)
    service = ConfigService(on_config_change=reload_config)

    with pytest.raises(BaseExceptionGroup, match="Config reload and rollback both failed") as raised:
        await service.connect_provider("openai", "new-key")

    assert [str(error) for error in raised.value.exceptions] == ["reload failed", "rollback reload failed"]
    assert persisted == {"provider_keys": {"openai": "old-key"}}


@pytest.mark.asyncio
async def test_config_service_finishes_rollback_before_propagating_cancellation(monkeypatch):
    import arden.services.config as config_module

    persisted = {"provider_keys": {"openai": "old-key"}}
    reload_started = asyncio.Event()
    reload_count = 0

    monkeypatch.setattr(config_module, "load_user_settings", lambda: deepcopy(persisted))

    def save_settings(settings: dict) -> None:
        nonlocal persisted
        persisted = deepcopy(settings)

    async def reload_config() -> None:
        nonlocal reload_count
        reload_count += 1
        if reload_count == 1:
            reload_started.set()
            await asyncio.Future()

    monkeypatch.setattr(config_module, "save_user_settings", save_settings)
    monkeypatch.setattr(config_module, "restore_user_settings", save_settings)
    service = ConfigService(on_config_change=reload_config)
    update = asyncio.create_task(service.connect_provider("openai", "new-key"))
    await reload_started.wait()
    update.cancel()

    with pytest.raises(asyncio.CancelledError):
        await update

    assert reload_count == 2
    assert persisted == {"provider_keys": {"openai": "old-key"}}


@pytest.mark.asyncio
async def test_config_updates_serialize_through_reload_and_rollback(monkeypatch):
    import arden.services.config as config_module

    persisted: dict = {}
    first_reload_started = asyncio.Event()
    release_first_reload = asyncio.Event()
    reload_count = 0

    monkeypatch.setattr(config_module, "load_user_settings", lambda: deepcopy(persisted))

    def save_settings(settings: dict) -> None:
        nonlocal persisted
        persisted = deepcopy(settings)

    async def reload_config() -> None:
        nonlocal reload_count
        reload_count += 1
        if reload_count == 1:
            first_reload_started.set()
            await release_first_reload.wait()
            raise RuntimeError("first reload failed")

    monkeypatch.setattr(config_module, "save_user_settings", save_settings)
    monkeypatch.setattr(config_module, "restore_user_settings", save_settings)
    service = ConfigService(on_config_change=reload_config)
    first = asyncio.create_task(service.update(max_messages=200))
    await first_reload_started.wait()
    second = asyncio.create_task(service.update(summary_max_tokens=2048))
    await asyncio.sleep(0)

    assert persisted == {"max_messages": 200}
    release_first_reload.set()
    with pytest.raises(RuntimeError, match="first reload failed"):
        await first
    await second

    assert reload_count == 3
    assert persisted == {"summary_max_tokens": 2048}


@pytest.mark.asyncio
async def test_failed_rollback_preserves_the_last_known_good_backup(tmp_path, monkeypatch):
    import arden.settings as settings_module

    monkeypatch.setattr(settings_module, "ARDEN_DIR", tmp_path)
    settings_module.save_user_settings({"max_messages": 120})
    primary = tmp_path / "settings.json"
    backup = tmp_path / "settings.json.bak"
    replace = os.replace
    primary_replaces = 0

    def fail_rollback_replace(source, destination) -> None:
        nonlocal primary_replaces
        if destination == primary:
            primary_replaces += 1
            if primary_replaces == 2:
                raise OSError("rollback replace failed")
        replace(source, destination)

    async def reload_config() -> None:
        raise RuntimeError("reload failed")

    monkeypatch.setattr(settings_module.os, "replace", fail_rollback_replace)
    service = ConfigService(on_config_change=reload_config)

    with pytest.raises(BaseExceptionGroup, match="Config reload and rollback both failed"):
        await service.update(max_messages=200)

    assert json.loads(primary.read_text()) == {"max_messages": 200}
    assert json.loads(backup.read_text()) == {"max_messages": 120}


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
async def test_config_service_persists_and_clears_storage_budget(monkeypatch):
    import arden.services.config as config_module

    persisted: dict = {}
    monkeypatch.setattr(config_module, "load_user_settings", lambda: deepcopy(persisted))

    def save_settings(settings: dict) -> None:
        nonlocal persisted
        persisted = deepcopy(settings)

    monkeypatch.setattr(config_module, "save_user_settings", save_settings)
    service = ConfigService(on_config_change=_noop)

    await service.update(
        max_space_gb=12.5,
        storage_backup_retention_days=21,
        storage_allow_archived_cleanup=True,
        storage_allow_current_cleanup=True,
        storage_allow_delete_cold_chats=True,
        storage_current_inactive_days=120,
        storage_current_minimum=150,
    )
    assert persisted == {
        "max_space_gb": 12.5,
        "storage_backup_retention_days": 21,
        "storage_allow_archived_cleanup": True,
        "storage_allow_current_cleanup": True,
        "storage_allow_delete_cold_chats": True,
        "storage_current_inactive_days": 120,
        "storage_current_minimum": 150,
    }
    assert Config(_env_file=None, **persisted).storage_allow_delete_cold_chats is True

    await service.update(max_space_gb=None, storage_allow_delete_cold_chats=False)
    assert persisted == {
        "storage_backup_retention_days": 21,
        "storage_allow_archived_cleanup": True,
        "storage_allow_current_cleanup": True,
        "storage_allow_delete_cold_chats": False,
        "storage_current_inactive_days": 120,
        "storage_current_minimum": 150,
    }


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
async def test_config_service_creates_custom_embedding_model_and_stores_api_key(monkeypatch):
    import arden.services.config as config_module

    persisted = {}
    added: list[dict] = []
    reload_seen: list[dict] = []

    def save_settings(settings: dict) -> None:
        nonlocal persisted
        persisted = deepcopy(settings)

    def add_model(**kwargs) -> EmbeddingModel:
        added.append(kwargs)
        return EmbeddingModel(
            id=kwargs["model_id"],
            provider=Provider.CUSTOM,
            dim=kwargs["dim"],
            base_url=kwargs["base_url"],
        )

    async def reload_config() -> None:
        reload_seen.append(deepcopy(persisted))

    monkeypatch.setattr(config_module, "load_user_settings", lambda: deepcopy(persisted))
    monkeypatch.setattr(config_module, "save_user_settings", save_settings)
    monkeypatch.setattr(config_module, "add_custom_embedding_model", add_model)
    monkeypatch.setattr(config_module, "get_embedding_models", lambda: {})
    monkeypatch.setattr(config_module, "get_models", lambda: {})

    service = ConfigService(on_config_change=reload_config)
    model = await service.create_custom_embedding_model(
        model_id="Qwen3-Embedding-0.6B",
        base_url="http://127.0.0.1:8081/v1",
        dim=1024,
        api_key="secret",
    )

    assert model.id == "Qwen3-Embedding-0.6B"
    assert added == [
        {
            "model_id": "Qwen3-Embedding-0.6B",
            "base_url": "http://127.0.0.1:8081/v1",
            "dim": 1024,
        }
    ]
    assert persisted == {"custom_model_keys": {"Qwen3-Embedding-0.6B": "secret"}}
    assert reload_seen == [persisted]


@pytest.mark.asyncio
async def test_deleting_active_custom_embedding_clears_selection_and_key(monkeypatch):
    import arden.services.config as config_module

    model = EmbeddingModel(
        id="local/embed",
        provider=Provider.CUSTOM,
        dim=1024,
        base_url="http://127.0.0.1:8081/v1",
    )
    persisted = {
        "embedding_model": model.id,
        "custom_model_keys": {model.id: "secret"},
    }
    removed: list[str] = []

    def save_settings(settings: dict) -> None:
        nonlocal persisted
        persisted = deepcopy(settings)

    monkeypatch.setattr(config_module, "load_user_settings", lambda: deepcopy(persisted))
    monkeypatch.setattr(config_module, "save_user_settings", save_settings)
    monkeypatch.setattr(
        config_module,
        "get_embedding_models_by_provider",
        lambda _provider: {model.id: model},
    )
    monkeypatch.setattr(
        config_module,
        "remove_custom_embedding_model",
        lambda model_id: removed.append(model_id),
    )

    service = ConfigService(on_config_change=lambda: _noop())
    await service.delete_custom_embedding_model(model.id)

    assert removed == [model.id]
    assert persisted == {"embedding_model": None}


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
async def test_writing_role_setups_persists_only_the_canonical_shape(monkeypatch):
    import arden.services.config as config_module

    persisted = {
        "chat_model": "gpt-5.2",
        "model_roles": {"memory": {"model": "gpt-5.2", "reasoning_effort": None}},
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
    assert persisted["chat_model"] == "gpt-5.2"
