from datetime import UTC, datetime

import pytest

from arden.search.index import SearchIndex
from arden.search.types import RawItem

pytestmark = pytest.mark.asyncio


class _TimeoutEmbedder:
    async def embed_one(self, text: str):
        raise TimeoutError("embedding timed out")

    async def embed(self, texts: list[str]):
        raise TimeoutError("embedding timed out")


class _Embedder:
    def __init__(self):
        self.single_calls = 0
        self.batch_calls = 0

    async def embed_one(self, text: str):
        self.single_calls += 1
        return [1.0]

    async def embed(self, texts: list[str]):
        self.batch_calls += 1
        return [[1.0] for _text in texts]


class _Store:
    def __init__(self):
        self.upserts = []
        self.deleted = []
        self.metadata_updates = []

    async def exists_with_hash(self, source: str, source_id: str, content_hash: str) -> bool:
        return False

    async def upsert(
        self, source: str, source_id: str, title: str, content: str, embedding: bytes, metadata=None, **kwargs
    ):
        self.upserts.append((source, source_id, title, content, embedding, metadata, kwargs))
        return True

    async def update_metadata(self, source: str, source_id: str, metadata=None):
        self.metadata_updates.append((source, source_id, metadata))
        return False

    async def get_indexed_hashes(self, source: str):
        return {"stale": (1, "old")}

    async def delete(self, source: str, source_id: str):
        self.deleted.append((source, source_id))
        return True


class _StateStore(_Store):
    def __init__(self):
        super().__init__()
        self.hashes: dict[str, str] = {}
        self.metadata: dict[str, dict | None] = {}

    async def exists_with_hash(self, source: str, source_id: str, content_hash: str) -> bool:
        return self.hashes.get(source_id) == content_hash

    async def upsert(
        self, source: str, source_id: str, title: str, content: str, embedding: bytes, metadata=None, **kwargs
    ):
        self.hashes[source_id] = kwargs["content_hash"]
        self.metadata[source_id] = metadata
        return await super().upsert(source, source_id, title, content, embedding, metadata, **kwargs)

    async def update_metadata(self, source: str, source_id: str, metadata=None):
        changed = self.metadata.get(source_id) != metadata
        self.metadata[source_id] = metadata
        return changed

    async def get_indexed_hashes(self, source: str):
        return {source_id: (index, value) for index, (source_id, value) in enumerate(self.hashes.items())}


def _item(source_id: str) -> RawItem:
    now = datetime.now(UTC)
    return RawItem(
        source="memory",
        source_id=source_id,
        title="title",
        content="content",
        created_at=now,
        updated_at=now,
    )


async def test_upsert_skips_semantic_update_when_embedding_times_out():
    store = _Store()
    index = SearchIndex(store=store, embedder=_TimeoutEmbedder())

    assert await index.upsert("memory", "one", "title", "content") is False
    assert store.upserts == []


async def test_sync_deletes_stale_rows_but_skips_updates_when_embedding_times_out():
    store = _Store()
    index = SearchIndex(store=store, embedder=_TimeoutEmbedder())

    result = await index.sync("memory", [_item("one")])

    assert result.updated == 0
    assert result.deleted == 1
    assert store.deleted == [("memory", "stale")]
    assert store.upserts == []


async def test_direct_and_batch_indexing_share_hash_and_metadata_does_not_reembed():
    store = _StateStore()
    embedder = _Embedder()
    index = SearchIndex(store=store, embedder=embedder)

    assert await index.upsert("memory", "one", "title", "content", {"freshness": "stale"}) is True
    item = _item("one")
    item.metadata["freshness"] = "current"
    result = await index.sync("memory", [item])

    assert result.updated == 0
    assert result.deleted == 0
    assert embedder.single_calls == 1
    assert embedder.batch_calls == 0
    assert store.metadata["one"] == {"freshness": "current"}
