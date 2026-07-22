from __future__ import annotations

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


class _Store:
    def __init__(self):
        self.upserts = []
        self.deleted = []

    async def exists_with_hash(self, source: str, source_id: str, content_hash: str) -> bool:
        return False

    async def upsert(self, source: str, source_id: str, title: str, content: str, embedding: bytes, metadata=None):
        self.upserts.append((source, source_id, title, content, embedding, metadata))
        return True

    async def get_indexed_hashes(self, source: str):
        return {"stale": (1, "old")}

    async def delete(self, source: str, source_id: str):
        self.deleted.append((source, source_id))
        return True


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
