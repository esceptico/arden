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
from dataclasses import replace
from pathlib import Path
from shutil import move

import numpy as np
import pytest

from ntrp.memory.file_store import FilePageStore
from ntrp.memory.ledger import LedgerEntry, LedgerMeta, render_ledger_entry
from ntrp.memory.models import Kind, SourceRef
from ntrp.memory.pages import Page
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


# --- schema-v2 file ledger lifecycle ---------------------------------------


def _ledger_entry(
    record_id: str,
    text: str,
    *,
    sequence: int,
    scope_kind: str = "user",
    scope_key: str | None = None,
    sources: tuple[SourceRef, ...] = (),
    supersedes: tuple[str, ...] = (),
    operation: str = "record",
) -> LedgerEntry:
    return LedgerEntry(
        id=record_id,
        text=text,
        kind=Kind.FACT,
        occurred_at="2026-07-12T10:00:00Z",
        meta=LedgerMeta(
            recorded_at=f"2026-07-12T10:00:{sequence:02d}Z",
            sequence=sequence,
            time_precision="second",
            scope_kind=scope_kind,
            scope_key=scope_key,
            sources=sources,
            supersedes=supersedes,
            operation=operation,
        ),
    )


def _write_ledger_vault(vault: Path, entries: list[LedgerEntry], page: str = "topics/a.md") -> None:
    visible = vault / page
    raw = vault / "raw" / page
    visible.parent.mkdir(parents=True, exist_ok=True)
    raw.parent.mkdir(parents=True, exist_ok=True)
    visible.write_text("# A\n", encoding="utf-8")
    raw.write_text(
        "\n".join((f"<!-- ntrp:records schema=2 page={page} -->", *(render_ledger_entry(e) for e in entries))) + "\n",
        encoding="utf-8",
    )


async def _file_store(vault: Path, entries: list[LedgerEntry]) -> FilePageStore:
    _write_ledger_vault(vault, entries)
    store = FilePageStore(vault)
    await store.open()
    return store


async def _file_store_pages(vault: Path, pages: dict[str, list[LedgerEntry]], *, index=None) -> FilePageStore:
    for page, entries in pages.items():
        _write_ledger_vault(vault, entries, page)
    store = FilePageStore(vault, search_index=index)
    await store.open()
    return store


class _LedgerIndexStore:
    def __init__(self):
        self.ids: set[str] = set()

    async def get_indexed_hashes(self, source):
        assert source == "memory_line"
        return dict.fromkeys(self.ids, (0, ""))


class _LedgerIndex:
    def __init__(self):
        self.store = _LedgerIndexStore()

    async def upsert(self, *, source, source_id, title, content, metadata=None):
        assert source == "memory_line"
        self.store.ids.add(source_id)
        return True

    async def delete(self, source, source_id):
        assert source == "memory_line"
        self.store.ids.discard(source_id)
        return True


async def test_page_active_entries_uses_relationship_graph_and_rejects_invalid_targets():
    first = _ledger_entry("first", "First", sequence=1)
    second = _ledger_entry("second", "Second", sequence=2, supersedes=("first",))
    assert Page(lines=[first, second]).active_entries() == (second,)

    duplicate = _ledger_entry("first", "Duplicate", sequence=3)
    with pytest.raises(ValueError, match="duplicate ledger entry id"):
        Page(lines=[first, duplicate]).active_entries()
    external = _ledger_entry("third", "External", sequence=4, supersedes=("other-page",))
    assert Page(lines=[external]).active_entries() == (external,)


async def test_moving_page_does_not_change_record_scope(tmp_path: Path):
    vault = tmp_path / "memory"
    entry = _ledger_entry("area-fact", "Area fact", sequence=1, scope_kind="area", scope_key="a1")
    store = await _file_store(vault, [entry])

    (vault / "notes").mkdir()
    (vault / "raw" / "notes").mkdir()
    move(vault / "topics" / "a.md", vault / "notes" / "a.md")
    move(vault / "raw" / "topics" / "a.md", vault / "raw" / "notes" / "a.md")
    await store.refresh_from_disk()

    record = await store.get(entry.id)
    assert record is not None
    assert (record.scope_kind, record.scope_key) == ("area", "a1")
    await store.close()


async def test_forget_appends_retract_and_keeps_history(tmp_path: Path):
    vault = tmp_path / "memory"
    source = SourceRef("chat_message", "s:m1", captured_at="2026-07-12T10:00:00Z")
    store = await _file_store(vault, [_ledger_entry("temporary", "Temporary", sequence=1, sources=(source,))])

    await store.delete("temporary", source_ref=SourceRef("tool_call", "forget:1", captured_at="2026-07-12T10:01:00Z"))

    assert await store.get("temporary") is None
    history = store.history("temporary")
    assert [entry.meta.operation for entry in history] == ["record", "retract"]
    assert history[-1].meta.sources[-1].ref == "forget:1"
    assert "Temporary" in (vault / "raw" / "topics" / "a.md").read_text(encoding="utf-8")
    await store.close()


async def test_ledger_write_recovers_visible_and_raw_files_as_one_commit(tmp_path: Path, monkeypatch):
    vault = tmp_path / "memory"
    original = _ledger_entry("original", "Original", sequence=1)
    store = await _file_store(vault, [original])
    visible = vault / "topics" / "a.md"
    raw = vault / "raw" / "topics" / "a.md"
    before = visible.read_bytes(), raw.read_bytes()

    def inject(point: str) -> None:
        if point == "after_replace:0":
            raise RuntimeError("injected journal failure")

    monkeypatch.setattr(store._journal, "_checkpoint", inject)
    successor = _ledger_entry("successor", "Successor", sequence=2, supersedes=(original.id,))
    with pytest.raises(RuntimeError, match="injected journal failure"):
        store.append_entries((successor,))

    store._journal = type(store._journal)(vault)
    store._journal.recover()
    after = visible.read_bytes(), raw.read_bytes()

    assert after == before or (b"updated:" in after[0] and b"^successor" in after[1])
    assert store.canonical_revision
    await store.close()


async def test_append_entries_stages_every_touched_page_and_caller_file_in_one_commit(tmp_path: Path, monkeypatch):
    vault = tmp_path / "memory"
    first = _ledger_entry("first", "First", sequence=1)
    second = _ledger_entry("second", "Second", sequence=2)
    store = await _file_store_pages(vault, {"topics/a.md": [first], "notes/b.md": [second]})
    commits = []
    commit = store._journal.commit

    def capture(files):
        commits.append(dict(files))
        return commit(files)

    monkeypatch.setattr(store._journal, "commit", capture)
    store.append_entries(
        (
            _ledger_entry("first-new", "First new", sequence=3, supersedes=(first.id,)),
            _ledger_entry("second-new", "Second new", sequence=4, supersedes=(second.id,)),
        ),
        files={Path("topics/a.md"): b"# User-edited A\n", Path("notes/user.md"): b"# User note\n"},
    )

    assert len(commits) == 1
    assert set(commits[0]) == {
        Path("topics/a.md"),
        Path("raw/topics/a.md"),
        Path("notes/b.md"),
        Path("raw/notes/b.md"),
        Path("notes/user.md"),
    }
    assert commits[0][Path("topics/a.md")] == b"# User-edited A\n"
    assert (vault / "topics" / "a.md").read_bytes() == b"# User-edited A\n"
    assert len(store.canonical_revision) == 64
    assert FilePageStore(vault).canonical_revision == store.canonical_revision
    await store.close()


async def test_generated_prose_only_write_is_excluded_from_canonical_revision(tmp_path: Path, monkeypatch):
    vault = tmp_path / "memory"
    store = await _file_store(vault, [_ledger_entry("record", "Fact", sequence=1)])
    page_path = vault / "topics" / "a.md"
    revision = store.canonical_revision

    def reject_commit(files):
        raise AssertionError(f"projection entered canonical commit: {files}")

    monkeypatch.setattr(store._journal, "commit", reject_commit)
    store._pages[page_path].prose = "Generated briefing."
    store._persist(page_path)

    assert "Generated briefing." in page_path.read_text(encoding="utf-8")
    assert store.canonical_revision == revision
    await store.close()


async def test_update_appends_successor_and_preserves_evidence(tmp_path: Path):
    vault = tmp_path / "memory"
    original_source = SourceRef("chat_message", "s:m1", captured_at="2026-07-12T10:00:00Z")
    update_source = SourceRef("tool_call", "remember:2", captured_at="2026-07-12T10:01:00Z")
    store = await _file_store(
        vault,
        [_ledger_entry("original", "Original", sequence=1, scope_kind="area", scope_key="a1", sources=(original_source,))],
    )

    assert await store.update("original", "Updated", source_ref=update_source) is True

    history = store.history("original")
    assert [entry.text for entry in history] == ["Original", "Updated"]
    assert history[-1].meta.supersedes == ("original",)
    assert history[-1].meta.sources == (original_source, update_source)
    current = await store.get(history[-1].id)
    assert current is not None
    assert (current.scope_kind, current.scope_key) == ("area", "a1")
    await store.close()


async def test_merge_appends_one_successor_for_all_predecessors_and_unions_evidence(tmp_path: Path):
    vault = tmp_path / "memory"
    sources = tuple(
        SourceRef("chat_message", f"s:m{i}", captured_at=f"2026-07-12T10:0{i}:00Z") for i in range(1, 4)
    )
    store = await _file_store(
        vault,
        [_ledger_entry(f"r{i}", f"Fact {i}", sequence=i, sources=(sources[i - 1],)) for i in range(1, 4)],
    )

    merged = await store.merge("r1", ["r2", "r3"], text="Merged fact")

    assert merged is not None
    successor = store.history("r1")[-1]
    assert successor.id == merged.id
    assert successor.meta.supersedes == ("r1", "r2", "r3")
    assert successor.meta.sources == sources
    assert {record.id for record in await store.list()} == {successor.id}
    await store.close()


async def test_v2_non_lifecycle_mutations_replace_immutable_entry_in_place(tmp_path: Path):
    vault = tmp_path / "memory"
    entry = _ledger_entry("record", "Fact", sequence=1)
    store = await _file_store(vault, [entry])

    assert await store.confirm(entry.id) is True
    assert await store.set_pinned(entry.id, True) is True
    await store.set_labels(entry.id, ["work"], entity_labels=["Dex"])

    current = await store.get(entry.id)
    assert current is not None
    assert current.id == entry.id
    assert current.pinned is True
    assert len(store.history(entry.id)) == 1
    assert await store.labels_of(entry.id) == ["Dex", "work"]

    await store.rename_label("Dex", "Ntrp")
    assert await store.labels_of(entry.id) == ["Ntrp", "work"]
    assert await store.set_label_kind("Ntrp", "meta") == 1
    assert await store.labels_of(entry.id) == ["Ntrp", "work"]

    await store.set_labels(entry.id, ["work"], entity_labels=["Dex"])
    await store.add_labels(entry.id, ["urgent"])
    assert await store.labels_of(entry.id) == ["Dex", "urgent", "work"]
    await store.add_labels(entry.id, [], entity_labels=["Ntrp"])
    assert await store.labels_of(entry.id) == ["Dex", "Ntrp", "urgent", "work"]
    await store.close()


async def test_v2_set_kind_appends_successor(tmp_path: Path):
    vault = tmp_path / "memory"
    store = await _file_store(vault, [_ledger_entry("record", "Rule", sequence=1)])

    assert await store.set_kind("record", Kind.DIRECTIVE) is True

    history = store.history("record")
    assert [entry.kind for entry in history] == [Kind.FACT, Kind.DIRECTIVE]
    assert history[-1].meta.supersedes == ("record",)
    assert await store.get("record") is None
    assert (await store.get(history[-1].id)).kind == Kind.DIRECTIVE
    await store.close()


async def test_v2_supersede_keeps_existing_successor_active_and_reopens(tmp_path: Path):
    vault = tmp_path / "memory"
    old = _ledger_entry("old", "Old", sequence=1)
    new = _ledger_entry("new", "New", sequence=2)
    store = await _file_store(vault, [old, new])

    assert await store.supersede(old.id, new.id) is True
    assert await store.get(old.id) is None
    assert (await store.get(new.id)).text == "New"
    history = store.history(old.id)
    assert [entry.id for entry in history] == [old.id, new.id, history[-1].id]
    assert history[-1].meta.operation == "retract"
    assert history[-1].meta.successor_id == new.id
    await store.close()

    reopened = FilePageStore(vault)
    await reopened.open()
    assert await reopened.get(old.id) is None
    assert (await reopened.get(new.id)).text == "New"
    await reopened.close()


async def test_v2_prune_is_idempotent_and_preserves_history(tmp_path: Path):
    vault = tmp_path / "memory"
    store = await _file_store(vault, [_ledger_entry("record", "Old", sequence=1)])
    await store.update("record", "New")
    before = (vault / "raw" / "topics" / "a.md").read_text(encoding="utf-8")

    assert await store.prune() == {"records": 0}
    assert await store.prune() == {"records": 0}
    assert (vault / "raw" / "topics" / "a.md").read_text(encoding="utf-8") == before
    assert len(store.history("record")) == 2
    await store.close()


async def test_v2_wipe_appends_retracts_and_preserves_pinned_history(tmp_path: Path):
    vault = tmp_path / "memory"
    pinned = _ledger_entry("pinned", "Pinned", sequence=1)
    disposable = _ledger_entry("drop", "Drop", sequence=2)
    store = await _file_store(vault, [pinned, disposable])
    await store.set_pinned(pinned.id, True)

    assert await store.wipe_except_pinned() == {"deleted": 1, "kept_pinned": 1}
    assert await store.wipe_except_pinned() == {"deleted": 0, "kept_pinned": 1}
    assert (await store.get(pinned.id)).pinned is True
    assert await store.get(disposable.id) is None
    assert [entry.meta.operation for entry in store.history(disposable.id)] == ["record", "retract"]
    assert "Drop" in (vault / "raw" / "topics" / "a.md").read_text(encoding="utf-8")
    await store.close()


async def test_cross_page_merge_uses_global_graph_for_index_and_labels_after_reopen(tmp_path: Path):
    vault = tmp_path / "memory"
    index = _LedgerIndex()
    first = _ledger_entry("first", "Shared fact one", sequence=1, scope_kind="area", scope_key="a1")
    second = _ledger_entry("second", "Shared fact two", sequence=2, scope_kind="area", scope_key="a1")
    store = await _file_store_pages(
        vault,
        {"topics/a.md": [first], "notes/b.md": [second]},
        index=index,
    )
    await store.set_labels(first.id, ["survivor"], entity_labels=["Alpha"])
    await store.set_labels(second.id, ["loser"], entity_labels=["Beta"])

    merged = await store.merge(first.id, [second.id], text="Shared merged fact")
    await _drain()

    assert merged is not None
    assert index.store.ids == {merged.id}
    assert await store.labels_of(merged.id) == ["Alpha", "loser", "survivor"]
    labels = {row["label"]: row["count"] for row in await store.list_labels()}
    assert labels.get("Beta", 0) == 0
    assert labels["Alpha"] == 1
    await store.close()

    reopened = FilePageStore(vault)
    await reopened.open()
    assert {record.id for record in await reopened.list()} == {merged.id}
    assert len(reopened.history(first.id)) == 3
    assert {row["label"] for row in await reopened.list_labels()} >= {"Alpha", "survivor"}
    await reopened.close()


async def test_cross_scope_merge_is_rejected_without_deactivation(tmp_path: Path):
    vault = tmp_path / "memory"
    first = _ledger_entry("first", "Area one", sequence=1, scope_kind="area", scope_key="a1")
    second = _ledger_entry("second", "Area two", sequence=2, scope_kind="area", scope_key="a2")
    store = await _file_store_pages(vault, {"topics/a.md": [first], "topics/b.md": [second]})

    assert await store.merge(first.id, [second.id], text="Must reject") is None
    assert {record.id for record in await store.list()} == {first.id, second.id}
    assert store.history(first.id) == (first,)
    assert store.history(second.id) == (second,)
    await store.close()


async def test_successor_target_is_validated_by_global_graph(tmp_path: Path):
    vault = tmp_path / "memory"
    old = _ledger_entry("old", "Old", sequence=1)
    link = _ledger_entry("link", "Old", sequence=2, supersedes=(old.id,), operation="retract")
    link = replace(link, meta=replace(link.meta, successor_id="missing"))
    _write_ledger_vault(vault, [old, link])

    with pytest.raises(ValueError, match="missing successor target"):
        await FilePageStore(vault).open()
