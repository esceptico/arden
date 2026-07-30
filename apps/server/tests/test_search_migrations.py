from datetime import UTC, datetime, timedelta

import pytest

import arden.database as database
from arden.database import serialize_embedding
from arden.search.index import SearchIndex
from arden.search.migrations import CURRENT_SCHEMA_VERSION
from arden.search.store import SearchStore
from arden.search.types import RawItem


@pytest.mark.asyncio
async def test_search_migration_removes_legacy_notes_source(tmp_path):
    conn = await database.connect(tmp_path / "search.db", vec=True)
    store = SearchStore(conn, embedding_dim=3)
    await store.init_schema()

    await store.upsert("notes", "note-1", "Legacy note", "obsolete", serialize_embedding([1, 0, 0]))
    await store.upsert("memory", "fact-1", "Memory", "keep", serialize_embedding([0, 1, 0]))
    await conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', '0')",
    )
    await conn.commit()

    await store.init_schema()

    assert await store.get_stats() == {"memory": 1}
    rows = await conn.execute_fetchall("SELECT value FROM meta WHERE key = 'schema_version'")
    assert rows[0]["value"] == str(CURRENT_SCHEMA_VERSION)
    await conn.close()


@pytest.mark.asyncio
async def test_embedding_model_change_keeps_text_and_stays_pending_until_rebuild(tmp_path):
    conn = await database.connect(tmp_path / "search.db", vec=True)
    first = SearchStore(conn, embedding_dim=3, embedding_model="first")
    assert await first.init_schema() is False
    await first.upsert("fact", "one", "Fact", "Persistent text", serialize_embedding([1, 0, 0]))
    await first.mark_embedding_ready()
    await first.set_embedding_retry_at(datetime.now(UTC) + timedelta(minutes=1))

    changed = SearchStore(conn, embedding_dim=3, embedding_model="second")
    assert await changed.init_schema() is True
    assert await changed.get_embedding_retry_at() is None
    assert await changed.get_stats() == {"fact": 1}
    rows = await conn.execute_fetchall("SELECT value FROM meta WHERE key = 'embedding_state'")
    assert rows[0]["value"] == "pending"

    await conn.close()
    restarted = await database.connect(tmp_path / "search.db", vec=True)
    assert await SearchStore(restarted, embedding_dim=3, embedding_model="second").init_schema() is True
    await restarted.close()


@pytest.mark.asyncio
async def test_embedding_dimension_change_keeps_text_and_requires_rebuild(tmp_path):
    conn = await database.connect(tmp_path / "search.db", vec=True)
    first = SearchStore(conn, embedding_dim=3, embedding_model="model")
    await first.init_schema()
    await first.upsert("fact", "one", "Fact", "Persistent text", serialize_embedding([1, 0, 0]))
    await first.mark_embedding_ready()

    changed = SearchStore(conn, embedding_dim=4, embedding_model="model")
    assert await changed.init_schema() is True
    assert await changed.get_stats() == {"fact": 1}
    await conn.close()


@pytest.mark.asyncio
async def test_empty_legacy_vector_table_is_rebuilt_before_first_insert(tmp_path):
    conn = await database.connect(tmp_path / "search.db", vec=True)
    store = SearchStore(conn, embedding_dim=3, embedding_model="model")
    await store.init_schema()
    await conn.execute("DROP TABLE items_vec")
    await conn.execute(
        """
        CREATE VIRTUAL TABLE items_vec USING vec0(
            item_id INTEGER PRIMARY KEY,
            embedding float[3] distance_metric=cosine
        )
        """
    )
    await conn.execute("UPDATE meta SET value = '1' WHERE key = 'vec_schema_version'")
    await conn.commit()

    migrated = SearchStore(conn, embedding_dim=3, embedding_model="model")
    assert await migrated.init_schema() is True
    assert await migrated.upsert(
        "fact",
        "one",
        "Fact",
        "Value",
        serialize_embedding([1, 0, 0]),
    )
    await conn.close()


@pytest.mark.asyncio
async def test_failed_rebuild_keeps_model_identity_pending_for_retry_after_restart(tmp_path):
    class FailingEmbedder:
        async def embed(self, _texts):
            raise TimeoutError("provider unavailable")

    conn = await database.connect(tmp_path / "search.db", vec=True)
    first = SearchStore(conn, embedding_dim=3, embedding_model="first")
    assert await first.init_schema() is False
    await first.upsert("fact", "one", "Fact", "Needs retry", serialize_embedding([1, 0, 0]))
    await first.mark_embedding_ready()
    store = SearchStore(conn, embedding_dim=3, embedding_model="second")
    assert await store.init_schema() is True
    now = datetime.now(UTC)
    item = RawItem("fact", "one", "Fact", "Needs retry", now, now)

    with pytest.raises(TimeoutError, match="provider unavailable"):
        await SearchIndex(store, FailingEmbedder()).sync("fact", [item], force=True, raise_on_error=True)

    rows = await conn.execute_fetchall("SELECT value FROM meta WHERE key = 'embedding_state'")
    assert rows[0]["value"] == "pending"
    await conn.close()

    restarted = await database.connect(tmp_path / "search.db", vec=True)
    assert await SearchStore(restarted, embedding_dim=3, embedding_model="second").init_schema() is True
    await restarted.close()


@pytest.mark.asyncio
async def test_pending_rebuild_preserves_completed_vectors_after_restart(tmp_path):
    class Embedder:
        def __init__(self, fail_on_call: int | None = None):
            self.calls = 0
            self.batch_sizes: list[int] = []
            self.fail_on_call = fail_on_call

        async def embed(self, texts):
            self.calls += 1
            self.batch_sizes.append(len(texts))
            if self.calls == self.fail_on_call:
                raise TimeoutError("rate limited")
            return [[1.0, 0.0, 0.0] for _text in texts]

    now = datetime.now(UTC)
    items = [RawItem("fact", str(number), "Fact", f"Value {number}", now, now) for number in range(3)]
    conn = await database.connect(tmp_path / "search.db", vec=True)
    first = SearchStore(conn, embedding_dim=3, embedding_model="first")
    await first.init_schema()
    await SearchIndex(first, Embedder()).sync("fact", items)
    await first.mark_embedding_ready()

    changed = SearchStore(conn, embedding_dim=3, embedding_model="second")
    assert await changed.init_schema() is True
    with pytest.raises(TimeoutError, match="rate limited"):
        await SearchIndex(changed, Embedder(fail_on_call=2)).sync(
            "fact",
            items,
            batch_size=1,
            raise_on_error=True,
        )
    rows = await conn.execute_fetchall("SELECT COUNT(*) AS count FROM items_vec")
    assert rows[0]["count"] == 1
    await conn.close()

    restarted = await database.connect(tmp_path / "search.db", vec=True)
    resumed_store = SearchStore(restarted, embedding_dim=3, embedding_model="second")
    assert await resumed_store.init_schema() is True
    rows = await restarted.execute_fetchall("SELECT COUNT(*) AS count FROM items_vec")
    assert rows[0]["count"] == 1

    resumed = Embedder()
    await SearchIndex(resumed_store, resumed).sync("fact", items)
    assert resumed.batch_sizes == [2]
    rows = await restarted.execute_fetchall("SELECT COUNT(*) AS count FROM items_vec")
    assert rows[0]["count"] == 3
    await restarted.close()


@pytest.mark.asyncio
async def test_legacy_index_without_model_identity_is_not_dropped(tmp_path):
    conn = await database.connect(tmp_path / "search.db", vec=True)
    store = SearchStore(conn, embedding_dim=3, embedding_model="first")
    await store.init_schema()
    await store.upsert("fact", "one", "Fact", "Legacy text", serialize_embedding([1, 0, 0]))
    await store.mark_embedding_ready()
    await conn.execute("DELETE FROM meta WHERE key IN ('embedding_model', 'embedding_state')")
    await conn.commit()

    restored = SearchStore(conn, embedding_dim=3, embedding_model="first")
    assert await restored.init_schema() is False
    assert await restored.get_stats() == {"fact": 1}
    await conn.close()


@pytest.mark.asyncio
async def test_fresh_index_does_not_request_a_second_forced_rebuild(tmp_path):
    conn = await database.connect(tmp_path / "search.db", vec=True)
    store = SearchStore(conn, embedding_dim=3, embedding_model="first")

    assert await store.init_schema() is False
    await conn.close()
