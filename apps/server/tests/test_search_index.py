from datetime import UTC, datetime

import aiosqlite
import pytest

import arden.database as database
from arden.search.index import SearchIndex
from arden.search.store import SearchStore
from arden.search.types import RawItem
from arden.server.indexer import Indexer

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
        self.batch_sizes: list[int] = []

    async def embed_one(self, text: str):
        self.single_calls += 1
        return [1.0]

    async def embed(self, texts: list[str]):
        self.batch_calls += 1
        self.batch_sizes.append(len(texts))
        return [[1.0] for _text in texts]


class _FailSecondBatchEmbedder(_Embedder):
    async def embed(self, texts: list[str]):
        self.batch_calls += 1
        self.batch_sizes.append(len(texts))
        if self.batch_calls == 2:
            raise TimeoutError("rate limited")
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
        return {"stale": (1, "old", True)}

    async def delete(self, source: str, source_id: str):
        self.deleted.append((source, source_id))
        return True


class _StateStore(_Store):
    def __init__(self):
        super().__init__()
        self.hashes: dict[str, str] = {}
        self.metadata: dict[str, dict | None] = {}
        self.embedded: set[str] = set()

    async def exists_with_hash(self, source: str, source_id: str, content_hash: str) -> bool:
        return self.hashes.get(source_id) == content_hash

    async def upsert(
        self, source: str, source_id: str, title: str, content: str, embedding: bytes, metadata=None, **kwargs
    ):
        self.hashes[source_id] = kwargs["content_hash"]
        self.metadata[source_id] = metadata
        if embedding is None:
            self.embedded.discard(source_id)
        else:
            self.embedded.add(source_id)
        return await super().upsert(source, source_id, title, content, embedding, metadata, **kwargs)

    async def update_metadata(self, source: str, source_id: str, metadata=None):
        changed = self.metadata.get(source_id) != metadata
        self.metadata[source_id] = metadata
        return changed

    async def get_indexed_hashes(self, source: str):
        return {
            source_id: (index, value, source_id in self.embedded)
            for index, (source_id, value) in enumerate(self.hashes.items())
        }


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


async def test_sync_keeps_full_text_current_when_embedding_times_out():
    store = _Store()
    index = SearchIndex(store=store, embedder=_TimeoutEmbedder())

    result = await index.sync("memory", [_item("one")])

    assert result.updated == 1
    assert result.deleted == 1
    assert result.degraded is True
    assert store.deleted == [("memory", "stale")]
    assert len(store.upserts) == 1
    assert store.upserts[0][4] is None


async def test_sync_retries_same_hash_after_an_embedding_failure(tmp_path):
    conn = await database.connect(tmp_path / "search.db", vec=True)
    store = SearchStore(conn, embedding_dim=1, embedding_model="test")
    await store.init_schema()
    item = _item("one")

    await SearchIndex(store, _TimeoutEmbedder()).sync("memory", [item])

    recovered = _Embedder()
    await SearchIndex(store, recovered).sync("memory", [item])

    assert recovered.batch_calls == 1
    rows = await conn.execute_fetchall("SELECT COUNT(*) AS count FROM items_vec")
    assert rows[0]["count"] == 1
    await conn.close()


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


async def test_forced_sync_reembeds_every_item_in_provider_batches():
    store = _StateStore()
    embedder = _Embedder()
    index = SearchIndex(store=store, embedder=embedder)
    progress: list[tuple[int, int]] = []

    result = await index.sync(
        "fact",
        [_item(str(number)) for number in range(51)],
        force=True,
        progress_callback=lambda done, total: progress.append((done, total)),
    )

    assert result.updated == 51
    assert embedder.batch_sizes == [50, 1]
    assert progress[0] == (0, 51)
    assert progress[-1] == (51, 51)


async def test_rebuild_resume_embeds_only_missing_vectors(tmp_path):
    conn = await database.connect(tmp_path / "search.db", vec=True)
    store = SearchStore(conn, embedding_dim=1, embedding_model="test")
    await store.init_schema()
    items = [_item(str(number)) for number in range(146)]
    interrupted = _FailSecondBatchEmbedder()

    with pytest.raises(TimeoutError, match="rate limited"):
        await SearchIndex(store, interrupted).sync("fact", items, raise_on_error=True)

    rows = await conn.execute_fetchall("SELECT COUNT(*) AS count FROM items_vec")
    assert interrupted.batch_sizes == [50, 50]
    assert rows[0]["count"] == 50

    resumed = _Embedder()
    result = await SearchIndex(store, resumed).sync("fact", items, raise_on_error=True)

    assert result.updated == 96
    assert resumed.batch_sizes == [50, 46]
    rows = await conn.execute_fetchall("SELECT COUNT(*) AS count FROM items_vec")
    assert rows[0]["count"] == 146
    await conn.close()


async def test_no_embeddings_keeps_fact_and_wiki_keyword_search(tmp_path):
    conn = await database.connect(tmp_path / "search.db")
    store = SearchStore(conn, embedding_dim=None)
    await store.init_schema()
    index = SearchIndex(store=store, embedder=None)

    now = datetime.now(UTC)
    items = [
        RawItem("fact", "fact-1", "Fact", "The orbital telescope uses a sunshield.", now, now),
        RawItem("wiki_page", "wiki-1", "Wiki", "The sunshield keeps the telescope cool.", now, now),
    ]
    await index.sync("fact", [items[0]])
    await index.sync("wiki_page", [items[1]])

    results = await index.search("sunshield", sources=["fact", "wiki_page"])

    assert {result.source_id for result in results} == {"fact-1", "wiki-1"}
    assert await store.get_stats() == {"fact": 1, "wiki_page": 1}
    await conn.close()


async def test_projection_checkpoints_persist_and_clear_with_their_source(tmp_path):
    db_path = tmp_path / "search.db"
    conn = await database.connect(db_path)
    store = SearchStore(conn, embedding_dim=None)
    await store.init_schema()

    await store.set_projection_checkpoint("fact", "fact-index-v1:abc")
    assert await store.get_projection_checkpoint("fact") == "fact-index-v1:abc"
    await conn.close()

    conn = await database.connect(db_path)
    store = SearchStore(conn, embedding_dim=None)
    await store.init_schema()
    assert await store.get_projection_checkpoint("fact") == "fact-index-v1:abc"

    await store.clear_source("fact")
    assert await store.get_projection_checkpoint("fact") is None
    await conn.close()


async def test_disabling_embeddings_does_not_load_vector_extension(tmp_path, monkeypatch):
    db_path = tmp_path / "search.db"
    conn = await database.connect(db_path, vec=True)
    store = SearchStore(conn, embedding_dim=1, embedding_model="test")
    await store.init_schema()
    await store.upsert(
        "fact",
        "fact-1",
        "Fact",
        "The orbital telescope uses a sunshield.",
        database.serialize_embedding([1.0]),
    )
    await store.mark_embedding_ready()
    await store.set_projection_checkpoint("fact", "fact-index-v1:abc")
    await conn.close()

    async def reject_vector_extension(_self, _path):
        raise AssertionError("disabled embeddings loaded sqlite-vec")

    with monkeypatch.context() as disabled:
        disabled.setattr(aiosqlite.Connection, "load_extension", reject_vector_extension)
        indexer = Indexer(db_path, embedding=None)
        await indexer.connect()
        try:
            assert indexer.index is not None
            assert indexer.index.embedder is None
            assert indexer.vector_enabled is False
            results = await indexer.index.search("sunshield", sources=["fact"])
            assert [result.source_id for result in results] == ["fact-1"]
        finally:
            await indexer.close()

    conn = await database.connect(db_path, vec=True)
    reenabled = SearchStore(conn, embedding_dim=1, embedding_model="test")
    assert await reenabled.init_schema() is True
    assert reenabled.has_vector_index is True
    assert await reenabled.get_projection_checkpoint("fact") is None
    await conn.close()
