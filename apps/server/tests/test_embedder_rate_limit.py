import asyncio
from datetime import UTC, datetime
from time import monotonic

import pytest
from google.genai.errors import ClientError

from arden.config import Config
from arden.embedder import EmbeddingConfig
from arden.llm.base import EmbeddingClient
from arden.llm.models import EmbeddingModel, Provider
from arden.search.types import RawItem
from arden.server.indexer import Indexer
from arden.server.runtime.core import Runtime


class _RateLimitedClient(EmbeddingClient):
    def __init__(self, retry_delay: str):
        self.retry_delay = retry_delay
        self.calls = 0

    async def _embedding(self, texts: list[str], model: str) -> list[list[float]]:
        del texts, model
        self.calls += 1
        raise ClientError(
            429,
            {
                "error": {
                    "code": 429,
                    "status": "RESOURCE_EXHAUSTED",
                    "details": [
                        {
                            "@type": "type.googleapis.com/google.rpc.RetryInfo",
                            "retryDelay": self.retry_delay,
                        }
                    ],
                }
            },
        )

    async def close(self) -> None:
        return


class _SuccessClient(EmbeddingClient):
    def __init__(self):
        self.calls = 0
        self.active = 0
        self.max_active = 0

    async def _embedding(self, texts: list[str], model: str) -> list[list[float]]:
        del model
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        return [[1.0] for _text in texts]

    async def close(self) -> None:
        return


class _RateLimitedOnceClient(_SuccessClient):
    async def _embedding(self, texts: list[str], model: str) -> list[list[float]]:
        self.calls += 1
        if self.calls == 1:
            raise ClientError(
                429,
                {
                    "error": {
                        "code": 429,
                        "status": "RESOURCE_EXHAUSTED",
                        "details": [
                            {
                                "@type": "type.googleapis.com/google.rpc.RetryInfo",
                                "retryDelay": "0.05s",
                            }
                        ],
                    }
                },
            )
        return [[1.0] for _text in texts]


async def test_rate_limit_cooldown_survives_restart_and_delays_the_next_call(tmp_path, monkeypatch):
    limited = _RateLimitedClient("0.2s")
    monkeypatch.setattr("arden.embedder.get_embedding_client", lambda _model: limited)
    first = Indexer(tmp_path / "search.db", EmbeddingConfig("test", 1))
    await first.connect()
    assert first.index is not None
    assert first.index.embedder is not None

    task = asyncio.create_task(first.index.embedder.embed(["first"]))
    for _attempt in range(100):
        if first.rate_limit_retry_at is not None:
            break
        await asyncio.sleep(0.001)
    assert first.rate_limit_retry_at is not None
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await first.close()

    recovered_client = _SuccessClient()
    monkeypatch.setattr("arden.embedder.get_embedding_client", lambda _model: recovered_client)
    restarted = Indexer(tmp_path / "search.db", EmbeddingConfig("test", 1))
    await restarted.connect()
    assert restarted.index is not None
    assert restarted.index.embedder is not None
    assert restarted.rate_limit_retry_at is not None

    started = monotonic()
    await restarted.index.embedder.embed(["second"])
    elapsed = monotonic() - started

    assert elapsed >= 0.1
    assert recovered_client.calls == 1
    assert restarted.rate_limit_retry_at is None
    assert await restarted.index.store.get_embedding_retry_at() is None
    await restarted.close()


async def test_all_embedding_traffic_shares_one_provider_gate(tmp_path, monkeypatch):
    client = _SuccessClient()
    monkeypatch.setattr("arden.embedder.get_embedding_client", lambda _model: client)
    indexer = Indexer(tmp_path / "search.db", EmbeddingConfig("test", 1))
    await indexer.connect()
    assert indexer.index is not None
    assert indexer.index.embedder is not None

    await asyncio.gather(
        indexer.index.embedder.embed(["index batch"]),
        indexer.index.embedder.embed(["semantic query"]),
    )

    assert client.calls == 2
    assert client.max_active == 1
    await indexer.close()


async def test_runtime_rebuild_waits_for_rate_limit_and_finishes_ready(tmp_path, monkeypatch):
    import arden.llm.models as llm_models

    monkeypatch.setitem(
        llm_models._registry._embedding_models,
        "test-embedding",
        EmbeddingModel("test-embedding", Provider.CUSTOM, 1, base_url="http://localhost"),
    )
    client = _RateLimitedOnceClient()
    monkeypatch.setattr("arden.embedder.get_embedding_client", lambda _model: client)
    config = Config(arden_dir=tmp_path, memory=False, embedding_model="test-embedding")
    config.db_dir.mkdir(parents=True, exist_ok=True)
    runtime = Runtime(config)
    await runtime.knowledge.connect()
    now = datetime.now(UTC)
    item = RawItem("fact", "one", "Fact", "Value", now, now)

    class Projection:
        async def sync(self, *, progress_callback, raise_on_error):
            await runtime.search_index.sync(
                "fact",
                [item],
                progress_callback=progress_callback,
                raise_on_error=raise_on_error,
            )
            return self

    runtime.fact_index_projection = Projection()
    assert await runtime.start_indexing() is True
    for _attempt in range(100):
        status = await runtime.get_index_status()
        if status["retry_at"] is not None:
            break
        await asyncio.sleep(0.001)
    assert status["state"] == "reindexing"
    assert status["retry_at"] is not None

    assert runtime._index_task is not None
    await runtime._index_task
    assert (await runtime.get_index_status())["state"] == "ready"
    assert client.calls == 2
    assert await runtime.search_index.store.get_embedding_retry_at() is None
    await runtime.knowledge.close()
