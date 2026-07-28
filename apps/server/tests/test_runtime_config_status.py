import pytest
from pydantic import ValidationError

from arden.config import Config
from arden.llm.models import EmbeddingModel, Provider
from arden.server.runtime.config import RuntimeConfig
from arden.server.runtime.core import Runtime
from arden.server.runtime.knowledge import KnowledgeRuntime
from arden.server.schemas import AddCustomModelRequest, UpdateConfigRequest


class _Integrations:
    def __init__(self):
        self.synced = []

    def sync(self, config):
        self.synced.append(config)


class _Knowledge:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.reloaded = []

    async def reload_config(self, config, stores):
        self.reloaded.append((config, stores))
        if self.fail:
            raise RuntimeError("knowledge reload failed")


async def _noop_reset():
    return None


async def _noop_sync_mcp(_config=None):
    return None


def test_config_rejects_non_positive_max_depth():
    with pytest.raises(ValidationError):
        Config(memory=False, max_depth=0)


def test_update_config_request_rejects_non_positive_max_depth():
    with pytest.raises(ValidationError):
        UpdateConfigRequest(max_depth=0)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_depth", 17),
        ("compression_threshold", 0.09),
        ("max_messages", 1001),
        ("compression_keep_ratio", 1.01),
        ("summary_max_tokens", 8001),
        ("consolidation_interval", 501),
    ],
)
def test_config_and_patch_share_the_settings_slider_bounds(field, value):
    with pytest.raises(ValidationError):
        Config(memory=False, **{field: value})
    with pytest.raises(ValidationError):
        UpdateConfigRequest(**{field: value})


def test_custom_model_request_rejects_output_larger_than_context():
    with pytest.raises(ValidationError, match="must not exceed"):
        AddCustomModelRequest(
            model_id="local/qwen",
            base_url="http://localhost:11434/v1",
            context_window=4096,
            max_output_tokens=8192,
        )

    request = AddCustomModelRequest(
        model_id="local/qwen",
        base_url="http://localhost:11434/v1",
        context_window=8192,
        max_output_tokens=4096,
    )
    assert request.max_output_tokens == 4096


@pytest.mark.asyncio
async def test_runtime_reload_advances_config_version_after_success(monkeypatch):
    import arden.server.runtime.config as config_module

    original = Config(memory=False)
    runtime = Runtime(config=original)
    integrations = _Integrations()
    knowledge = _Knowledge()
    updated = Config(memory=False, max_depth=12)

    runtime.integrations = integrations
    runtime.knowledge = knowledge
    runtime.sync_mcp = _noop_sync_mcp

    monkeypatch.setattr(config_module, "get_config", lambda: updated)
    monkeypatch.setattr(config_module, "llm_reset", _noop_reset)
    monkeypatch.setattr(config_module, "llm_init", lambda _config: None)

    before = runtime.config_status()["config_version"]

    await runtime.reload_config()

    assert runtime.config is updated
    assert integrations.synced == [updated]
    assert knowledge.reloaded == [(updated, None)]
    assert runtime.config_status()["config_version"] == before + 1


@pytest.mark.asyncio
async def test_runtime_reload_does_not_advance_config_version_after_failure(monkeypatch):
    import arden.server.runtime.config as config_module

    original = Config(memory=False)
    runtime = Runtime(config=original)
    runtime.integrations = _Integrations()
    runtime.knowledge = _Knowledge(fail=True)
    runtime.sync_mcp = _noop_sync_mcp

    monkeypatch.setattr(config_module, "get_config", lambda: Config(memory=False, max_depth=12))
    monkeypatch.setattr(config_module, "llm_reset", _noop_reset)
    monkeypatch.setattr(config_module, "llm_init", lambda _config: None)

    before = runtime.config_status()["config_version"]

    with pytest.raises(RuntimeError, match="knowledge reload failed"):
        await runtime.reload_config()

    assert runtime.config is original
    assert runtime.config_status()["config_version"] == before


@pytest.mark.asyncio
async def test_runtime_reload_refreshes_models_before_reading_config(monkeypatch):
    import arden.server.runtime.config as config_module

    events: list[str] = []
    updated = Config(memory=False, max_depth=12)

    async def refresh_models() -> bool:
        events.append("models")
        return True

    def read_config() -> Config:
        events.append("config")
        return updated

    monkeypatch.setattr(config_module, "get_config", read_config)
    monkeypatch.setattr(config_module, "llm_reset", _noop_reset)
    monkeypatch.setattr(config_module, "llm_init", lambda _config: None)

    runtime = RuntimeConfig(
        Config(memory=False),
        get_integrations=_Integrations,
        get_knowledge=_Knowledge,
        get_stores=lambda: None,
        sync_mcp=_noop_sync_mcp,
        is_closing=lambda: False,
        refresh_models=refresh_models,
    )

    await runtime.reload()

    assert events == ["models", "config"]
    assert runtime.config is updated


@pytest.mark.asyncio
async def test_knowledge_runtime_syncs_indexer_with_embedding_config(tmp_path, monkeypatch):
    import arden.llm.models as llm_models

    monkeypatch.setitem(
        llm_models._registry._embedding_models,
        "test-embedding",
        EmbeddingModel("test-embedding", Provider.OPENAI, 3),
    )

    initial = Config(arden_dir=tmp_path, memory=False, embedding_model=None)
    initial.db_dir.mkdir(parents=True, exist_ok=True)
    knowledge = KnowledgeRuntime(initial)

    assert knowledge.tool_services() == {}

    assert knowledge.indexer is None
    assert knowledge.search_index is None

    enabled = Config(arden_dir=tmp_path, memory=False, embedding_model="test-embedding")
    await knowledge.reload_config(enabled, stores=None)

    assert knowledge.indexer is not None
    assert knowledge.search_index is not None

    disabled = Config(arden_dir=tmp_path, memory=False, embedding_model=None)
    await knowledge.reload_config(disabled, stores=None)

    assert knowledge.indexer is None
    assert knowledge.search_index is None
