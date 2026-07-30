import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta

import aiosqlite
import pytest
from fastapi import HTTPException
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

    assert knowledge.indexer is not None
    assert knowledge.search_index is None

    enabled = Config(arden_dir=tmp_path, memory=False, embedding_model="test-embedding")
    await knowledge.reload_config(enabled, stores=None)

    assert knowledge.indexer is not None
    assert knowledge.search_index is not None
    assert knowledge.search_index.embedder is not None

    disabled = Config(arden_dir=tmp_path, memory=False, embedding_model=None)
    await knowledge.reload_config(disabled, stores=None)

    assert knowledge.indexer is not None
    assert knowledge.search_index is not None
    await knowledge.close()


@pytest.mark.asyncio
async def test_embedding_change_requires_confirmation_before_settings_mutation(tmp_path, monkeypatch):
    import arden.llm.models as llm_models

    monkeypatch.setitem(
        llm_models._registry._embedding_models,
        "test-embedding",
        EmbeddingModel("test-embedding", Provider.CUSTOM, 3, base_url="http://localhost"),
    )
    runtime = Runtime(Config(arden_dir=tmp_path, memory=False, embedding_model="test-embedding"))
    calls: list[dict] = []

    async def update(**fields) -> None:
        calls.append(fields)

    monkeypatch.setattr(runtime.config_service, "update", update)

    with pytest.raises(HTTPException, match="confirmed=true"):
        await runtime.update_embedding_model(None, confirmed=False)

    assert calls == []
    await runtime.update_embedding_model("test-embedding", confirmed=False)
    assert calls == []
    with pytest.raises(HTTPException, match="not available"):
        await runtime.update_embedding_model("text-embedding-3-small", confirmed=True)


@pytest.mark.asyncio
async def test_runtime_index_status_reports_progress_error_and_concurrent_rebuild(tmp_path, monkeypatch):
    import arden.llm.models as llm_models

    monkeypatch.setitem(
        llm_models._registry._embedding_models,
        "test-embedding",
        EmbeddingModel("test-embedding", Provider.CUSTOM, 3, base_url="http://localhost"),
    )
    runtime = Runtime(Config(arden_dir=tmp_path, memory=False, embedding_model="test-embedding"))

    class Indexer:
        vector_enabled = True
        rate_limit_retry_at = None

    runtime.knowledge.indexer = Indexer()
    started = asyncio.Event()
    release = asyncio.Event()

    class Projection:
        async def sync(self, *, progress_callback, **_kwargs):
            assert "force" not in _kwargs
            progress_callback(0, 2)
            started.set()
            await release.wait()
            progress_callback(2, 2)
            return self

    runtime.fact_index_projection = Projection()
    runtime.wiki_page_projection = Projection()

    assert await runtime.start_indexing() is True
    await started.wait()
    runtime.knowledge.indexer.rate_limit_retry_at = datetime.now(UTC) + timedelta(seconds=46)
    active_status = await runtime.get_index_status()
    assert active_status["state"] == "reindexing"
    assert datetime.fromisoformat(active_status["retry_at"]) > datetime.now(UTC)
    runtime.knowledge.indexer.rate_limit_retry_at = None
    assert (await runtime.get_index_status())["retry_at"] is None
    with pytest.raises(HTTPException, match="already running"):
        await runtime.start_indexing()
    with pytest.raises(HTTPException, match="already running"):
        await runtime.update_embedding_model(None, confirmed=True)

    release.set()
    assert runtime._index_task is not None
    await runtime._index_task
    assert await runtime.get_index_status() == {
        "state": "ready",
        "model": "test-embedding",
        "phase": None,
        "done": 2,
        "total": 2,
        "retry_at": None,
        "error": None,
    }


@pytest.mark.asyncio
async def test_runtime_reload_stops_active_rebuild_before_reloading_knowledge(tmp_path, monkeypatch):
    import arden.llm.models as llm_models

    monkeypatch.setitem(
        llm_models._registry._embedding_models,
        "test-embedding",
        EmbeddingModel("test-embedding", Provider.CUSTOM, 3, base_url="http://localhost"),
    )
    runtime = Runtime(Config(arden_dir=tmp_path, memory=False, embedding_model="test-embedding"))

    class Indexer:
        vector_enabled = True
        needs_rebuild = False
        rate_limit_retry_at = None

    runtime.knowledge.indexer = Indexer()
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def active_rebuild():
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            stopped.set()

    old_task = asyncio.create_task(active_rebuild())
    runtime._index_task = old_task
    await started.wait()

    async def reload_config():
        assert stopped.is_set()

    monkeypatch.setattr(runtime.config_runtime, "reload", reload_config)

    await runtime.reload_config()

    assert old_task.cancelled()
    assert runtime._index_task is not old_task
    await runtime._index_task


@pytest.mark.asyncio
async def test_runtime_reports_rebuild_failure_when_a_projection_cannot_finish(tmp_path, monkeypatch):
    import arden.llm.models as llm_models

    monkeypatch.setitem(
        llm_models._registry._embedding_models,
        "test-embedding",
        EmbeddingModel("test-embedding", Provider.CUSTOM, 3, base_url="http://localhost"),
    )
    runtime = Runtime(Config(arden_dir=tmp_path, memory=False, embedding_model="test-embedding"))

    class Indexer:
        vector_enabled = True
        rate_limit_retry_at = None

    runtime.knowledge.indexer = Indexer()

    class Projection:
        async def sync(self, **_kwargs):
            return None

    runtime.fact_index_projection = Projection()

    assert await runtime.start_indexing() is True
    assert runtime._index_task is not None
    await runtime._index_task

    status = await runtime.get_index_status()
    assert status["state"] == "error"
    assert status["error"] == "RuntimeError: fact index rebuild did not complete"


@pytest.mark.asyncio
async def test_runtime_does_not_mark_existing_unavailable_partitions_as_rebuilt(tmp_path, monkeypatch):
    import arden.llm.models as llm_models

    monkeypatch.setitem(
        llm_models._registry._embedding_models,
        "test-embedding",
        EmbeddingModel("test-embedding", Provider.CUSTOM, 3, base_url="http://localhost"),
    )
    runtime = Runtime(Config(arden_dir=tmp_path, memory=False, embedding_model="test-embedding"))

    class Indexer:
        vector_enabled = True
        rate_limit_retry_at = None

    runtime.knowledge.indexer = Indexer()

    class Store:
        marked_ready = False

        async def get_stats(self):
            return {"fact": 1}

        async def mark_embedding_ready(self):
            self.marked_ready = True

    class Index:
        def __init__(self, store):
            self.store = store

    store = Store()
    runtime.knowledge.search_index = Index(store)

    assert await runtime.start_indexing() is True
    assert runtime._index_task is not None
    await runtime._index_task

    status = await runtime.get_index_status()
    assert status["state"] == "error"
    assert status["error"] == "RuntimeError: cannot rebuild unavailable sources: fact"
    assert store.marked_ready is False


@pytest.mark.asyncio
async def test_runtime_reports_vector_index_initialization_failure(tmp_path, monkeypatch):
    import arden.llm.models as llm_models

    monkeypatch.setitem(
        llm_models._registry._embedding_models,
        "test-embedding",
        EmbeddingModel("test-embedding", Provider.CUSTOM, 3, base_url="http://localhost"),
    )

    original_execute = aiosqlite.Connection.execute

    async def reject_vector_table(self, sql, parameters=()):
        if "CREATE VIRTUAL TABLE IF NOT EXISTS items_vec" in sql:
            raise sqlite3.OperationalError("vec extension unavailable")
        return await original_execute(self, sql, parameters)

    monkeypatch.setattr(aiosqlite.Connection, "execute", reject_vector_table)
    runtime = Runtime(Config(arden_dir=tmp_path, memory=False, embedding_model="test-embedding"))

    await runtime.connect()
    try:
        assert await runtime.get_index_status() == {
            "state": "error",
            "model": "test-embedding",
            "phase": None,
            "done": 0,
            "total": 0,
            "retry_at": None,
            "error": "Vector index is unavailable; full-text search remains available",
        }
        assert runtime.search_index is not None
        assert runtime.search_index.embedder is None
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_runtime_connect_reports_initial_embedding_failure_without_stopping_fts(tmp_path, monkeypatch):
    import arden.llm.models as llm_models
    from arden.embedder import Embedder
    from arden.memory.facts.ledger import FactLedger

    monkeypatch.setitem(
        llm_models._registry._embedding_models,
        "test-embedding",
        EmbeddingModel("test-embedding", Provider.CUSTOM, 3, base_url="http://localhost"),
    )

    config = Config(arden_dir=tmp_path, embedding_model="test-embedding")
    ledger = FactLedger(config.memory_artifacts_dir / "facts")
    plan = ledger.plan(
        [
            {
                "op": "create",
                "fact_id": "sunshield",
                "text": "The orbital telescope uses a sunshield.",
                "kind": "fact",
                "subjects": ["Telescope"],
                "scope": {"kind": "user", "key": None},
                "sources": [{"kind": "test", "ref": "sunshield"}],
            }
        ],
        actor="test",
        origin="test",
        reason="seed",
    )
    ledger.commit(plan)

    async def unavailable(self, texts):
        del self, texts
        raise TimeoutError("provider unavailable")

    monkeypatch.setattr(Embedder, "embed", unavailable)
    runtime = Runtime(config)

    await runtime.connect()
    try:
        assert (await runtime.get_index_status())["state"] == "error"
        assert (await runtime.get_index_status())["error"] == "TimeoutError: provider unavailable"
        assert runtime.search_index is not None
        assert runtime.search_index.embedder is not None
        results = await runtime.search_index.search("sunshield", sources=["fact"])
        assert [result.source_id for result in results] == ["sunshield"]
    finally:
        await runtime.close()
