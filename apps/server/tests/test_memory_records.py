"""RecordStore — the atomic memory unit, FLAT pool (ntrp/memory/records.py).

Hermetic: a tmp `memory.db` (never ~/.ntrp/memory.db) plus EITHER a fake
SearchIndex (scripted vector hits, no real embeddings / search.db) OR no index at
all (`search_index=None` -> FTS-only). The fake mirrors the exact surface
RecordStore.search touches — `index.embedder.embed_one`, `index.store.vector_search`,
`index.store.get_by_id` — and captures `upsert`/`delete` so we can assert the
record->vector bridge happens. NO scope partition: search/list span ALL records.
Covers add/get, hybrid search (with the fake index AND None), supersede (excluded
from active search, shown with include_superseded), confirm, update, delete,
list(pinned_only), kinds filtering, provenance round-trip, and the labels
substrate (set/add/labels_of/labels_for, records_for_label active-only,
list_labels active counts, rename_label union, merge-unions, supersede_with
inheritance, delete cascade).
"""

import asyncio
from pathlib import Path

import numpy as np
import pytest

from ntrp.memory.models import Kind, SourceRef
from ntrp.memory.records import RecordStore

pytestmark = pytest.mark.asyncio


# --- Fake SearchIndex: the minimal surface RecordStore.search touches. --------


class _FakeItem:
    def __init__(self, metadata: dict):
        self.metadata = metadata


class _FakeStore:
    """Captures upserted records and serves scripted vector hits. `vector_search`
    returns (item_id, score) pairs over the currently-indexed records, ordered by
    cosine to the query embedding, mapping back to record_id via get_by_id."""

    def __init__(self, embedder):
        self._embedder = embedder
        self._items: dict[str, dict] = {}  # source_id -> {metadata, embedding}
        self._next_id = 1
        self._by_int: dict[int, str] = {}  # int item_id -> source_id
        self.deleted: list[tuple[str, str]] = []

    async def upsert_record(self, source_id: str, content: str, metadata: dict):
        emb = await self._embedder.embed_one(content)
        if source_id not in self._items:
            self._by_int[self._next_id] = source_id
            self._items[source_id] = {"int_id": self._next_id, "embedding": emb, "metadata": metadata}
            self._next_id += 1
        else:
            self._items[source_id]["embedding"] = emb
            self._items[source_id]["metadata"] = metadata

    async def vector_search(self, embedding, *, sources, limit):
        assert sources == ["record"]
        q = np.frombuffer(embedding, dtype=np.float32)
        scored: list[tuple[int, float]] = []
        for rec in self._items.values():
            e = rec["embedding"].astype(np.float32)
            denom = (np.linalg.norm(q) * np.linalg.norm(e)) or 1.0
            scored.append((rec["int_id"], float(np.dot(q, e) / denom)))
        scored.sort(key=lambda t: t[1], reverse=True)
        return scored[:limit]

    async def get_by_id(self, item_id: int):
        sid = self._by_int.get(item_id)
        if sid is None or sid not in self._items:
            return None
        return _FakeItem(self._items[sid]["metadata"])


class _FakeEmbedder:
    """Token-overlap pseudo-embeddings (monotone in lexical overlap), as float32
    so serialize_embedding round-trips through np.frombuffer."""

    def __init__(self, dim: int = 64):
        self.dim = dim

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        for tok in text.lower().split():
            v[hash(tok) % self.dim] += 1.0
        n = np.linalg.norm(v)
        return (v / n if n else v).astype(np.float32)

    async def embed_one(self, text: str) -> np.ndarray:
        return self._vec(text)


class _FakeSearchIndex:
    """Mirrors SearchIndex's surface used by RecordStore: `embedder`, `store`,
    `upsert(source, source_id, title, content, metadata)`, `delete(source, id)`."""

    def __init__(self):
        self.embedder = _FakeEmbedder()
        self.store = _FakeStore(self.embedder)
        self.upserts: list[dict] = []

    async def upsert(self, *, source, source_id, title, content, metadata=None):
        assert source == "record"
        self.upserts.append({"source_id": source_id, "content": content, "metadata": metadata})
        await self.store.upsert_record(source_id, content, metadata or {})
        return True

    async def delete(self, source, source_id):
        self.store.deleted.append((source, source_id))
        self.store._items.pop(source_id, None)
        return True


async def _drain():
    """Let the fire-and-forget index tasks (add/update/delete) run to completion."""
    await asyncio.sleep(0)
    await asyncio.sleep(0)


def _store(tmp_path: Path, *, index=None) -> RecordStore:
    return RecordStore(tmp_path / "memory.db", search_index=index)


# --- add / get ----------------------------------------------------------------


async def test_add_then_get_round_trips(tmp_path: Path):
    store = _store(tmp_path)
    rec = await store.add("the user prefers tea", kind=Kind.FACT)

    assert rec.id
    assert rec.kind == "fact"
    assert rec.superseded_by is None
    assert rec.pinned is False

    got = await store.get(rec.id)
    assert got is not None
    assert got.text == "the user prefers tea"
    await store.close()


async def test_get_missing_returns_none(tmp_path: Path):
    store = _store(tmp_path)
    assert await store.get("does-not-exist") is None
    await store.close()


async def test_add_defaults_to_fact_kind(tmp_path: Path):
    store = _store(tmp_path)
    rec = await store.add("a loose fact")
    assert rec.kind == "fact"
    await store.close()


async def test_provenance_round_trips_via_source_ref(tmp_path: Path):
    store = _store(tmp_path)
    source = SourceRef(
        kind="curator",
        ref="sess-1",
        scope_kind="area",
        scope_key="proj-1",
        occurred_at="2026-07-12T14:23:41.582+04:00",
        time_precision="millisecond",
        role="user",
        excerpt_hash="sha256:abc",
    )
    rec = await store.add("auth uses JWT", source_ref=source)

    got = await store.get(rec.id)
    assert got.source_ref is not None
    assert got.sources == (got.source_ref,)
    assert got.source_ref.kind == "curator"
    assert got.source_ref.scope_kind == "area"  # inert provenance, not a partition
    assert got.source_ref.scope_key == "proj-1"
    assert got.source_ref.occurred_at == "2026-07-12T14:23:41.582+04:00"
    assert got.source_ref.time_precision == "millisecond"
    assert got.source_ref.role == "user"
    assert got.source_ref.excerpt_hash == "sha256:abc"
    await store.close()


# --- hybrid search: FTS-only (search_index=None) ------------------------------


async def test_search_fts_only_when_no_index(tmp_path: Path):
    store = _store(tmp_path)  # search_index=None -> pure FTS leg
    await store.add("the user deploys with kubernetes")
    await store.add("unrelated note about gardening")

    hits = await store.search("kubernetes")
    assert len(hits) == 1
    assert "kubernetes" in hits[0].text
    await store.close()


async def test_search_returns_empty_when_nothing_matches(tmp_path: Path):
    store = _store(tmp_path)
    await store.add("the user likes tea")
    assert await store.search("xylophone") == []
    await store.close()


async def test_search_spans_whole_flat_pool(tmp_path: Path):
    """No scope partition: records added with any provenance are all searchable."""
    store = _store(tmp_path)
    a = await store.add("the cat sleeps", source_ref=SourceRef("c", "1", scope_kind="user"))
    b = await store.add("the cat sleeps", source_ref=SourceRef("c", "2", scope_kind="area", scope_key="p"))

    hits = await store.search("cat")
    assert {h.id for h in hits} == {a.id, b.id}
    await store.close()


# --- hybrid search: with the fake vector index --------------------------------


async def test_search_surfaces_vector_only_hit(tmp_path: Path):
    """A record the FTS query alone would miss still surfaces because the fake
    vector leg ranks it (RRF fuses both legs)."""
    index = _FakeSearchIndex()
    store = _store(tmp_path, index=index)
    await store.add("kubernetes deployment guide")
    await store.add("kubectl apply manifests to a cluster")
    await _drain()

    hits = await store.search("kubernetes cluster")
    texts = {h.text for h in hits}
    assert any("kubernetes deployment" in t for t in texts)
    assert any("kubectl apply" in t for t in texts)
    await store.close()


async def test_add_bridges_record_into_the_vector_index(tmp_path: Path):
    index = _FakeSearchIndex()
    store = _store(tmp_path, index=index)
    rec = await store.add("indexed record")
    await _drain()

    assert len(index.upserts) == 1
    up = index.upserts[0]
    assert up["source_id"] == rec.id
    assert up["content"] == "indexed record"
    assert up["metadata"]["record_id"] == rec.id
    assert up["metadata"]["kind"] == "fact"
    assert "scope_kind" not in up["metadata"]  # raw store writes can remain unscoped; tool/API writes apply scope
    await store.close()


# --- supersede ----------------------------------------------------------------


async def test_supersede_excludes_from_active_search(tmp_path: Path):
    store = _store(tmp_path)
    old = await store.add("the user lives in Berlin")
    new = await store.add("the user lives in Munich")
    await store.supersede(old.id, new.id)

    assert (await store.get(old.id)).superseded_by == new.id

    active = await store.search("the user lives")
    active_ids = {h.id for h in active}
    assert old.id not in active_ids
    assert new.id in active_ids

    with_old = await store.search("the user lives", include_superseded=True)
    assert old.id in {h.id for h in with_old}
    await store.close()


# --- confirm / update ---------------------------------------------------------


async def test_confirm_bumps_last_confirmed_at(tmp_path: Path):
    store = _store(tmp_path)
    rec = await store.add("a fact")
    before = (await store.get(rec.id)).last_confirmed_at

    await asyncio.sleep(0.01)
    await store.confirm(rec.id)

    after = (await store.get(rec.id)).last_confirmed_at
    assert after > before
    await store.close()


async def test_update_retexts_and_confirms(tmp_path: Path):
    index = _FakeSearchIndex()
    store = _store(tmp_path, index=index)
    rec = await store.add("old text")
    await _drain()
    before = (await store.get(rec.id)).last_confirmed_at

    await asyncio.sleep(0.01)
    await store.update(rec.id, "new text")
    await _drain()

    got = await store.get(rec.id)
    assert got.text == "new text"
    assert got.last_confirmed_at > before  # update confirms
    assert index.upserts[-1]["content"] == "new text"
    await store.close()


# --- delete -------------------------------------------------------------------


async def test_delete_removes_row_and_vector(tmp_path: Path):
    index = _FakeSearchIndex()
    store = _store(tmp_path, index=index)
    rec = await store.add("disposable")
    await _drain()

    await store.delete(rec.id)
    await _drain()

    assert await store.get(rec.id) is None
    assert ("record", rec.id) in index.store.deleted
    await store.close()


# --- prune (LINT structural hygiene) ------------------------------------------


async def test_prune_hard_deletes_tombstones_and_orphan_labels(tmp_path: Path):
    store = _store(tmp_path)  # FTS-only; vector reconcile is a no-op without an index
    survivor = await store.add("the user lives in Munich")
    stale = await store.add("the user lives in Berlin")
    await store.set_labels(stale.id, ["location"])
    await store.supersede(stale.id, survivor.id)

    report = await store.prune()

    assert report["records"] == 1  # the one tombstone
    assert report["labels"] == 1  # its orphaned label
    assert await store.get(stale.id) is None  # tombstone gone
    assert await store.get(survivor.id) is not None  # active survivor untouched
    assert await store.labels_of(stale.id) == []
    assert await store.count_active() == 1

    # Idempotent: a clean store prunes nothing.
    assert (await store.prune())["records"] == 0
    await store.close()


# --- list(pinned_only) --------------------------------------------------------


async def test_list_pinned_only(tmp_path: Path):
    store = _store(tmp_path)
    await store.add("loose note", pinned=False)
    pinned = await store.add("pinned fact", pinned=True)

    everything = await store.list()
    assert len(everything) == 2

    only_pinned = await store.list(pinned_only=True)
    assert [r.id for r in only_pinned] == [pinned.id]
    await store.close()


async def test_list_excludes_superseded(tmp_path: Path):
    store = _store(tmp_path)
    old = await store.add("old")
    new = await store.add("new")
    await store.supersede(old.id, new.id)

    ids = {r.id for r in await store.list()}
    assert old.id not in ids
    assert new.id in ids
    await store.close()


async def test_list_limit_none_returns_all_active_records(tmp_path: Path):
    store = _store(tmp_path, index=None)
    rows = [await store.add(f"fact {i}", kind=Kind.FACT) for i in range(56)]
    await store.supersede(rows[0].id, rows[-1].id)

    assert len(await store.list()) == 50
    assert len(await store.list(limit=None)) == 55
    assert rows[0].id not in {r.id for r in await store.list(limit=None)}
    await store.close()


async def test_list_spans_whole_flat_pool(tmp_path: Path):
    """No scope: list returns every active record regardless of provenance."""
    store = _store(tmp_path)
    u = await store.add("user-prov", source_ref=SourceRef("c", "1", scope_kind="user"))
    p = await store.add("proj-prov", source_ref=SourceRef("c", "2", scope_kind="area", scope_key="x"))

    ids = {r.id for r in await store.list()}
    assert ids == {u.id, p.id}
    await store.close()


# --- kinds filtering ----------------------------------------------------------


async def test_list_filters_by_kinds(tmp_path: Path):
    store = _store(tmp_path)
    fact = await store.add("the sky is blue", kind=Kind.FACT)
    await store.add("daily receipt", kind=Kind.SOURCE)

    rows = await store.list(kinds=["fact"])

    assert [r.id for r in rows] == [fact.id]
    await store.close()


async def test_search_filters_by_kinds(tmp_path: Path):
    store = _store(tmp_path)
    fact = await store.add("the sky is blue", kind=Kind.FACT)
    await store.add("the sky is blue", kind=Kind.SOURCE)

    hits = await store.search("sky", kinds=["fact"])
    assert {h.id for h in hits} == {fact.id}
    await store.close()


# --- labels ---------------------------------------------------------------------


async def test_set_labels_replaces_all(tmp_path: Path):
    store = _store(tmp_path)
    rec = await store.add("Dex sleeps eighteen hours a day")

    await store.set_labels(rec.id, ["Dex", "traits"])
    assert await store.labels_of(rec.id) == ["Dex", "traits"]

    await store.set_labels(rec.id, ["health"])  # replace, not union
    assert await store.labels_of(rec.id) == ["health"]
    await store.close()


async def test_add_labels_unions(tmp_path: Path):
    store = _store(tmp_path)
    rec = await store.add("Dex hates the vacuum cleaner")
    await store.set_labels(rec.id, ["Dex"])

    await store.add_labels(rec.id, ["Dex", "traits"])  # duplicate ignored, new added
    assert await store.labels_of(rec.id) == ["Dex", "traits"]
    await store.close()


async def test_labels_of_missing_record_is_empty(tmp_path: Path):
    store = _store(tmp_path)
    assert await store.labels_of("does-not-exist") == []
    await store.close()


async def test_labels_for_batch_hydrates_every_id(tmp_path: Path):
    store = _store(tmp_path)
    a = await store.add("a")
    b = await store.add("b")
    c = await store.add("c")
    await store.set_labels(a.id, ["x"])
    await store.set_labels(b.id, ["y", "x"])

    got = await store.labels_for([a.id, b.id, c.id])
    assert got == {a.id: ["x"], b.id: ["x", "y"], c.id: []}  # unlabeled -> []
    assert await store.labels_for([]) == {}
    await store.close()


async def test_records_for_label_active_only_newest_confirmed_first(tmp_path: Path):
    store = _store(tmp_path)
    old = await store.add("Dex was adopted in 2021")
    await asyncio.sleep(0.01)
    new = await store.add("Dex eats grain-free food")
    await store.set_labels(old.id, ["Dex"])
    await store.set_labels(new.id, ["Dex"])
    await store.add("unlabeled noise")

    hits = await store.records_for_label("Dex")
    assert [r.id for r in hits] == [new.id, old.id]  # newest-confirmed first

    successor = await store.add("Dex was adopted in 2022")
    await store.supersede(old.id, successor.id)
    assert [r.id for r in await store.records_for_label("Dex")] == [new.id]
    await store.close()


async def test_list_labels_counts_active_records_only(tmp_path: Path):
    store = _store(tmp_path)
    a = await store.add("a")
    b = await store.add("b")
    c = await store.add("c")
    await store.set_labels(a.id, ["Dex", "health"])
    await store.set_labels(b.id, ["Dex"])
    await store.set_labels(c.id, ["Dex"])

    successor = await store.add("c2")
    await store.supersede(c.id, successor.id)  # c's labels no longer counted

    assert await store.list_labels() == [
        {"label": "Dex", "count": 2, "kind": "meta"},
        {"label": "health", "count": 1, "kind": "meta"},
    ]
    await store.close()


async def test_set_label_kind_retypes_all_rows_and_is_idempotent(tmp_path: Path):
    store = _store(tmp_path)
    a = await store.add("Dex slept through the night")
    b = await store.add("Dex started crawling")
    await store.set_labels(a.id, ["Dex", "health"])
    await store.set_labels(b.id, ["Dex"])

    n = await store.set_label_kind("Dex", "entity")
    assert n == 2  # both record rows carrying the label retyped

    by_label = {e["label"]: e["kind"] for e in await store.list_labels()}
    assert by_label["Dex"] == "entity"
    assert by_label["health"] == "meta"  # untouched

    # Idempotent: re-applying the same kind still touches the rows but changes nothing.
    await store.set_label_kind("Dex", "entity")
    by_label = {e["label"]: e["kind"] for e in await store.list_labels()}
    assert by_label["Dex"] == "entity"
    await store.close()


async def test_rename_label_unions_into_existing(tmp_path: Path):
    store = _store(tmp_path)
    a = await store.add("a")
    b = await store.add("b")
    await store.set_labels(a.id, ["dex"])
    await store.set_labels(b.id, ["Dex", "dex"])  # carries both spellings

    await store.rename_label("dex", "Dex")

    assert await store.labels_of(a.id) == ["Dex"]
    assert await store.labels_of(b.id) == ["Dex"]  # union: no duplicate row
    assert await store.list_labels() == [{"label": "Dex", "count": 2, "kind": "meta"}]
    await store.close()


async def test_merge_unions_labels_onto_survivor(tmp_path: Path):
    store = _store(tmp_path)
    s = await store.add("survivor")
    l1 = await store.add("loser one")
    l2 = await store.add("loser two")
    await store.set_labels(s.id, ["Dex"])
    await store.set_labels(l1.id, ["health"])
    await store.set_labels(l2.id, ["Dex", "traits"])

    merged = await store.merge(s.id, [l1.id, l2.id])

    assert merged is not None
    assert await store.labels_of(s.id) == ["Dex", "health", "traits"]
    await store.close()


async def test_supersede_with_passes_labels_to_successor(tmp_path: Path):
    store = _store(tmp_path)
    old = await store.add("Dex weighs 12kg")
    await store.set_labels(old.id, ["Dex", "health"])

    new = await store.supersede_with(old.id, text="Dex weighs 14kg")

    assert await store.labels_of(new.id) == ["Dex", "health"]
    assert await store.labels_of(old.id) == ["Dex", "health"]  # history keeps its labels
    await store.close()


async def test_delete_cascades_labels(tmp_path: Path):
    store = _store(tmp_path)
    rec = await store.add("disposable")
    await store.set_labels(rec.id, ["Dex"])

    await store.delete(rec.id)

    assert await store.labels_of(rec.id) == []
    assert await store.list_labels() == []
    await store.close()
