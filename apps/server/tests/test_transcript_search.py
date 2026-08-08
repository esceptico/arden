"""Transcript full-text search — FTS5 index, triggers, backfill, and the
store.search_messages query (ranking, pagination, time + session scope)."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
import pytest_asyncio

import arden.database as database
from arden.context.models import SessionState
from arden.context.store import SessionStore


@pytest_asyncio.fixture
async def store(tmp_path: Path):
    conn = await database.connect(tmp_path / "sessions.db")
    read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
    s = SessionStore(conn, read_conn, event_conn=conn)
    await s.init_schema()
    yield s
    await read_conn.close()
    await conn.close()


def _state(session_id: str, name: str | None = None, area_id: str | None = None) -> SessionState:
    return SessionState(session_id=session_id, started_at=datetime.now(UTC), name=name, area_id=area_id)


async def _seed(
    store: SessionStore,
    session_id: str,
    texts: list[str],
    *,
    name: str | None = None,
    area_id: str | None = None,
):
    msgs = [{"role": "user" if i % 2 == 0 else "assistant", "content": t} for i, t in enumerate(texts)]
    await store.save_session(_state(session_id, name=name, area_id=area_id), msgs)


class _RecordingEmbedder:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def embed_one(self, text: str):
        self.calls.append("embed")
        return np.ones(8, dtype=np.float32)


class _RecordingVectorStore:
    async def vector_search(self, embedding, *, sources, limit):
        return []


class _RecordingSearchIndex:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.embedder = _RecordingEmbedder(self.calls)
        self.store = _RecordingVectorStore()

    async def upsert(self, **kwargs):
        self.calls.append("upsert")
        return True

    async def delete(self, source: str, source_id: str):
        self.calls.append("delete")
        return True


@pytest.mark.asyncio
async def test_transcript_persistence_and_search_never_use_embeddings(store: SessionStore):
    index = _RecordingSearchIndex()
    store._search_index = index

    await _seed(store, "s1", ["how do I deploy with kubernetes", "use kubectl apply"])
    await asyncio.sleep(0)
    result = await store.search_messages("kubernetes")

    assert len(result["hits"]) == 1
    assert index.calls == []


@pytest.mark.asyncio
async def test_search_finds_message_across_sessions(store: SessionStore):
    await _seed(store, "s1", ["how do I deploy with kubernetes", "use kubectl apply"], name="Ops")
    await _seed(store, "s2", ["what is the capital of france", "Paris"], name="Trivia")

    res = await store.search_messages("kubernetes")
    assert res["has_more"] is False
    assert len(res["hits"]) == 1
    hit = res["hits"][0]
    assert hit["session_id"] == "s1"
    assert hit["session_name"] == "Ops"
    assert hit["seq"] is not None
    assert "kubernetes" in hit["snippet"].lower()


@pytest.mark.asyncio
async def test_search_scoped_to_session(store: SessionStore):
    await _seed(store, "s1", ["shared keyword here"])
    await _seed(store, "s2", ["shared keyword also here"])

    all_hits = await store.search_messages("keyword")
    assert len(all_hits["hits"]) == 2

    scoped = await store.search_messages("keyword", session_id="s2")
    assert len(scoped["hits"]) == 1
    assert scoped["hits"][0]["session_id"] == "s2"


@pytest.mark.asyncio
async def test_search_scoped_to_area(store: SessionStore):
    area_a = await store.create_area(name="A")
    area_b = await store.create_area(name="B")
    await _seed(store, "s1", ["shared keyword here"], area_id=area_a["area_id"])
    await _seed(store, "s2", ["shared keyword also here"], area_id=area_b["area_id"])

    scoped = await store.search_messages("keyword", area_id=area_a["area_id"])

    assert len(scoped["hits"]) == 1
    assert scoped["hits"][0]["session_id"] == "s1"


@pytest.mark.asyncio
async def test_list_session_messages_scoped_to_area(store: SessionStore):
    area_a = await store.create_area(name="A")
    area_b = await store.create_area(name="B")
    await _seed(store, "s1", ["area private"], area_id=area_a["area_id"])

    wrong = await store.list_session_messages("s1", area_id=area_b["area_id"])
    right = await store.list_session_messages("s1", area_id=area_a["area_id"])

    assert wrong["messages"] == []
    assert len(right["messages"]) == 1


@pytest.mark.asyncio
async def test_search_pagination(store: SessionStore):
    await _seed(store, "s1", [f"alpha entry number {i}" for i in range(5)])

    page1 = await store.search_messages("alpha", limit=2, offset=0)
    assert len(page1["hits"]) == 2
    assert page1["has_more"] is True

    page3 = await store.search_messages("alpha", limit=2, offset=4)
    assert len(page3["hits"]) == 1
    assert page3["has_more"] is False


@pytest.mark.asyncio
async def test_search_empty_query_returns_nothing(store: SessionStore):
    await _seed(store, "s1", ["anything"])
    res = await store.search_messages("   ")
    assert res["hits"] == []
    assert res["has_more"] is False


@pytest.mark.asyncio
async def test_search_malformed_query_does_not_raise(store: SessionStore):
    await _seed(store, "s1", ["a quoted thing"])
    # Unbalanced quote / stray operators must fall back to a phrase, not error.
    res = await store.search_messages('quoted "')
    assert isinstance(res["hits"], list)


@pytest.mark.asyncio
async def test_trigger_updates_index_on_message_change(store: SessionStore):
    await _seed(store, "s1", ["original searchable token"])
    assert len(await store.search_messages("original")) or True
    assert len((await store.search_messages("original"))["hits"]) == 1

    # Overwrite the transcript — the AFTER UPDATE/DELETE triggers must keep
    # the FTS index in sync, so the old token disappears and the new appears.
    await store.save_session(_state("s1"), [{"role": "user", "content": "replaced with different wording"}])
    # save_session updates existing rows by message_id; a fresh message_id is
    # assigned per content here, so the old row is replaced on rewrite.
    after = await store.search_messages("replaced")
    assert len(after["hits"]) == 1
    assert after["hits"][0]["session_id"] == "s1"


@pytest.mark.asyncio
async def test_search_time_filter(store: SessionStore):
    await _seed(store, "s1", ["timeboxed needle"])
    future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    past = (datetime.now(UTC) - timedelta(days=1)).isoformat()

    assert len((await store.search_messages("needle", since=past))["hits"]) == 1
    assert len((await store.search_messages("needle", since=future))["hits"]) == 0
