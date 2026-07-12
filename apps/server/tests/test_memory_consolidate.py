"""Consolidate/Lint — the records consolidation pass (ntrp/memory/consolidate.py).

THIS is the memory step: the background O(delta) pass that merges duplicates,
supersedes stale/contradicted records, and drops orphans over the flat record
pool. Hermetic: a STUB LLM (scripted LintOps JSON per neighborhood) + a real tmp
RecordStore (search_index=None -> FTS-only neighborhoods). The watermark lives in
the same tmp memory.db.

Covers:
  (a) merge folds duplicate records onto one survivor (the others superseded);
  (b) a pinned record is never merged/invalidated/dropped;
  (c) invalidate-with-successor supersedes the stale record into the newer one;
  (d) invalidate-without-successor deletes the stale record;
  (e) drop_orphan deletes a source-less record, but keeps a record with provenance;
  (f) watermark durability: a clean second sweep does no LLM work;
  (g) no LLM / no model -> no-op;
  (h) the RecordStore primitives: merge atomicity + pin-guard, updated_since order.
"""

from pathlib import Path

import pytest

from ntrp.memory.consolidate import Consolidate, ConsolidateReport
from ntrp.memory.models import Record, SourceRef
from ntrp.memory.prompts_consolidate import LabelOps, LintOps, MergeOp, RetypeOp
from ntrp.memory.records import RecordStore
from tests.conftest import completion_response

pytestmark = pytest.mark.asyncio


class StubLLM:
    """Scripted completion client: returns queued LintOps payloads (FIFO) and
    records every call so a test can assert the per-neighborhood cost / no-op."""

    def __init__(self, *responses: str):
        self._queue = list(responses)
        self.calls: list[dict] = []

    async def completion(self, *, messages, model, reasoning_effort=None, response_format=None, **kwargs):
        self.calls.append({"messages": messages, "model": model, "response_format": response_format})
        body = self._queue.pop(0) if self._queue else LintOps().model_dump_json()
        return completion_response(body)


def _consolidate(tmp_path: Path, records: RecordStore, llm) -> Consolidate:
    return Consolidate(records, llm, model="memory-model", db_path=tmp_path / "memory.db")


def _merge_ops(member_ids: list[str], merged_text: str | None = None) -> str:
    return LintOps.model_validate(
        {"merges": [{"member_ids": member_ids, "merged_text": merged_text}]}
    ).model_dump_json()


def _invalidate_ops(record_id: str, contradicted_by: str | None = None) -> str:
    return LintOps.model_validate(
        {"invalidations": [{"record_id": record_id, "contradicted_by": contradicted_by}]}
    ).model_dump_json()


def _orphan_ops(record_id: str) -> str:
    return LintOps.model_validate({"orphans": [{"record_id": record_id}]}).model_dump_json()


# --- file-canonical store (the revived nightly path) --------------------------


async def test_consolidate_runs_on_file_page_store_and_skips_observations(tmp_path: Path):
    """The nightly consolidation now runs on the canonical FilePageStore: it merges
    duplicate DURABLE records but never pulls a low-trust observation/lesson into a
    merge (that would launder trust)."""
    from ntrp.memory.file_store import FilePageStore
    from ntrp.memory.models import SourceRef

    store = FilePageStore(tmp_path / "memory")
    await store.open()
    a = await store.add("The user rides a Trek Marlin 5 gravel bike.", kind="fact", source_ref=SourceRef("user", ""))
    b = await store.add("The user rides a Trek Marlin gravel bicycle.", kind="fact", source_ref=SourceRef("user", ""))
    obs = await store.add("Email from Kevin about the bike order.", kind="observation", source_ref=SourceRef("gmail", "g1"))

    llm = StubLLM(_merge_ops([a.id, b.id], merged_text="The user rides a Trek Marlin 5 gravel bike."))
    consolidate = Consolidate(store, llm, model="memory-model", db_path=tmp_path / "meta.db")

    report = await consolidate.run_once()

    assert report.merged == 1
    active = await store.list(limit=None, scopes=None)
    durable = [r for r in active if r.kind == "fact"]
    assert len(durable) == 1 and "Trek Marlin 5" in durable[0].text, durable
    assert any(r.id == obs.id and r.kind == "observation" for r in active), "observation untouched"
    # the observation never entered a judged neighborhood
    for call in llm.calls:
        assert '"kind": "observation"' not in call["messages"][-1]["content"]
    await consolidate.close()
    await store.close()


async def test_file_store_set_label_kind_meta_to_entity_is_not_data_loss(tmp_path: Path):
    """meta->entity has no faithful mapping on the page-level meta model — it must leave
    the label in place, never silently delete it."""
    from ntrp.memory.file_store import FilePageStore
    from ntrp.memory.models import SourceRef

    store = FilePageStore(tmp_path / "memory")
    await store.open()
    r = await store.add("The user filed a bug.", kind="fact", source_ref=SourceRef("user", ""))
    await store.set_labels(r.id, ["Bug"])  # a page meta label
    assert any(l["label"] == "Bug" for l in await store.list_labels())

    changed = await store.set_label_kind("Bug", "entity")  # ill-defined direction
    assert changed == 0
    assert any(l["label"] == "Bug" for l in await store.list_labels()), "meta label must survive"
    await store.close()


async def test_retype_tracks_successor_id_and_initiating_evidence(tmp_path: Path):
    target = Record(id="old", text="Always verify", kind="fact", scope_kind="user")

    class Store:
        source_ref = None

        async def supersede_with(self, old_id, **kwargs):
            assert old_id == target.id
            self.source_ref = kwargs["source_ref"]
            return Record(
                id="new",
                text=kwargs["text"],
                kind=kwargs["kind"],
                scope_kind=kwargs["scope_kind"],
                scope_key=kwargs["scope_key"],
                source_ref=kwargs["source_ref"],
            )

    store = Store()
    consolidate = Consolidate(store, None, model="memory-model", db_path=tmp_path / "meta.db")
    live = {target.id: target}
    report = ConsolidateReport()

    await consolidate._apply_retype(RetypeOp(record_id=target.id, kind="directive"), live, report)

    assert set(live) == {"new"}
    assert live["new"].kind == "directive"
    assert store.source_ref.kind == "consolidate"
    assert store.source_ref.ref == "retype:old"
    assert report.retyped == 1
    await consolidate.close()


async def test_merge_live_map_uses_returned_successor_for_following_op(tmp_path: Path):
    first = Record(id="first", text="Fact", kind="fact", scope_kind="user")
    second = Record(id="second", text="Fact refined", kind="fact", scope_kind="user")

    class Store:
        async def merge(self, survivor_id, loser_ids, **kwargs):
            return Record(id="merged", text=kwargs["text"], kind="fact", scope_kind="user")

        async def supersede_with(self, old_id, **kwargs):
            assert old_id == "merged"
            return Record(id="retyped", text=kwargs["text"], kind=kwargs["kind"], scope_kind="user")

    consolidate = Consolidate(Store(), None, model="memory-model", db_path=tmp_path / "meta.db")
    live = {first.id: first, second.id: second}
    report = ConsolidateReport()

    await consolidate._apply_merge(
        MergeOp(member_ids=[first.id, second.id], merged_text="Merged fact"),
        live,
        report,
    )
    await consolidate._apply_retype(RetypeOp(record_id="merged", kind="directive"), live, report)

    assert set(live) == {"retyped"}
    assert report.merged == 1
    assert report.retyped == 1
    await consolidate.close()


# --- merge --------------------------------------------------------------------


async def test_merge_folds_duplicates_onto_one_survivor(tmp_path: Path):
    records = RecordStore(tmp_path / "memory.db", search_index=None)
    a = await records.add("the user lives in Yerevan")
    b = await records.add("the user lives in Yerevan, Armenia")
    # One neighborhood is judged; queue a merge of both onto a unified survivor.
    llm = StubLLM(_merge_ops([a.id, b.id], merged_text="the user lives in Yerevan, Armenia"))
    consolidate = _consolidate(tmp_path, records, llm)

    report = await consolidate.run_once()

    assert report.merged == 1
    active = await records.list()
    assert len(active) == 1
    survivor = active[0]
    assert survivor.text == "the user lives in Yerevan, Armenia"
    # The loser is folded into the survivor, then the LINT pass hard-deletes the
    # tombstone (records carry no archived status).
    loser = a if survivor.id == b.id else b
    assert await records.get(loser.id) is None
    assert report.pruned == 1
    await consolidate.close()
    await records.close()


async def test_merge_keeps_survivor_text_without_merged_text(tmp_path: Path):
    records = RecordStore(tmp_path / "memory.db", search_index=None)
    a = await records.add("uses Linux daily")
    b = await records.add("uses Linux daily")
    llm = StubLLM(_merge_ops([a.id, b.id]))  # no unified wording
    consolidate = _consolidate(tmp_path, records, llm)

    report = await consolidate.run_once()

    assert report.merged == 1
    active = await records.list()
    assert len(active) == 1
    assert active[0].text == "uses Linux daily"
    await consolidate.close()
    await records.close()


# --- pin inviolability --------------------------------------------------------


async def test_pinned_record_is_never_merged(tmp_path: Path):
    records = RecordStore(tmp_path / "memory.db", search_index=None)
    a = await records.add("the user lives in Yerevan", pinned=True)
    b = await records.add("the user lives in Yerevan")
    llm = StubLLM(_merge_ops([a.id, b.id]))
    consolidate = _consolidate(tmp_path, records, llm)

    report = await consolidate.run_once()

    assert report.merged == 0
    assert len(await records.list()) == 2  # both survive untouched
    await consolidate.close()
    await records.close()


async def test_pinned_record_is_never_invalidated(tmp_path: Path):
    records = RecordStore(tmp_path / "memory.db", search_index=None)
    a = await records.add("the user works at Acme", pinned=True)
    llm = StubLLM(_invalidate_ops(a.id))
    consolidate = _consolidate(tmp_path, records, llm)

    await consolidate.run_once()

    assert await records.get(a.id) is not None  # not deleted
    await consolidate.close()
    await records.close()


# --- invalidate ---------------------------------------------------------------


async def test_invalidate_with_successor_supersedes_into_it(tmp_path: Path):
    records = RecordStore(tmp_path / "memory.db", search_index=None)
    old = await records.add("the user works at Acme")
    new = await records.add("the user works at Globex")
    # Both share the "the user works at" neighborhood; mark old as superseded by new.
    llm = StubLLM(_invalidate_ops(old.id, contradicted_by=new.id))
    consolidate = _consolidate(tmp_path, records, llm)

    report = await consolidate.run_once()

    assert report.superseded == 1
    assert report.pruned == 1
    assert await records.get(old.id) is None  # superseded into new, then pruned
    assert (await records.get(new.id)).superseded_by is None  # the newer record lives
    await consolidate.close()
    await records.close()


async def test_invalidate_without_successor_deletes_stale(tmp_path: Path):
    records = RecordStore(tmp_path / "memory.db", search_index=None)
    stale = await records.add("the meeting is tomorrow at noon")
    llm = StubLLM(_invalidate_ops(stale.id))
    consolidate = _consolidate(tmp_path, records, llm)

    report = await consolidate.run_once()

    assert report.superseded == 1
    assert await records.get(stale.id) is None  # no successor -> deleted
    await consolidate.close()
    await records.close()


# --- drop orphan --------------------------------------------------------------


async def test_drop_orphan_removes_sourceless_record(tmp_path: Path):
    records = RecordStore(tmp_path / "memory.db", search_index=None)
    orphan = await records.add("a stray fragment")  # no source_ref
    llm = StubLLM(_orphan_ops(orphan.id))
    consolidate = _consolidate(tmp_path, records, llm)

    report = await consolidate.run_once()

    assert report.dropped == 1
    assert await records.get(orphan.id) is None
    await consolidate.close()
    await records.close()


async def test_drop_orphan_keeps_record_with_provenance(tmp_path: Path):
    records = RecordStore(tmp_path / "memory.db", search_index=None)
    sourced = await records.add("a sourced fact", source_ref=SourceRef("curator", "s1"))
    llm = StubLLM(_orphan_ops(sourced.id))
    consolidate = _consolidate(tmp_path, records, llm)

    report = await consolidate.run_once()

    assert report.dropped == 0
    assert await records.get(sourced.id) is not None  # provenance -> not an orphan
    await consolidate.close()
    await records.close()


# --- label hygiene (one bounded call per sweep) --------------------------------


def _label_ops(renames: list[tuple[str, str]]) -> str:
    return LabelOps.model_validate({"renames": [{"old": old, "new": new} for old, new in renames]}).model_dump_json()


async def test_label_hygiene_folds_near_duplicate_labels(tmp_path: Path):
    records = RecordStore(tmp_path / "memory.db", search_index=None)
    a = await records.add("dex sleeps through the night now")
    b = await records.add("dex sleeps in his crib")
    await records.set_labels(a.id, ["dex"])
    await records.set_labels(b.id, ["Dex memory"])
    # One neighborhood judgment (no ops), then the label-hygiene call: both
    # spellings fold into "Dex"; the hallucinated "ghost" rename is dropped.
    llm = StubLLM(
        LintOps().model_dump_json(),
        _label_ops([("dex", "Dex"), ("Dex memory", "Dex"), ("ghost", "Dex")]),
    )
    consolidate = _consolidate(tmp_path, records, llm)

    report = await consolidate.run_once()

    assert report.relabeled == 2
    assert await records.list_labels() == [{"label": "Dex", "count": 2, "kind": "meta"}]
    assert await records.labels_of(a.id) == ["Dex"]
    # ONE hygiene call after the neighborhood judgments; no derivation phase runs.
    assert [c["response_format"] for c in llm.calls] == [LintOps, LabelOps]

    # No new records -> the next sweep spends nothing, label hygiene included.
    await consolidate.run_once()
    assert len(llm.calls) == 2
    await consolidate.close()
    await records.close()


async def test_label_hygiene_reclassifies_entity_vs_meta(tmp_path: Path):
    """The hygiene call retypes a subject label to 'entity' while leaving a
    category label as 'meta'. Only the explicit reclass op moves a label."""
    records = RecordStore(tmp_path / "memory.db", search_index=None)
    d1 = await records.add("Dex slept through the night")
    d2 = await records.add("Dex started crawling")
    await records.set_labels(d1.id, ["Dex"])
    await records.set_labels(d2.id, ["Dex"])
    b1 = await records.add("server returned a 500 on /memory")
    b2 = await records.add("intermittent 500 on the memory router")
    await records.set_labels(b1.id, ["Bug"])
    await records.set_labels(b2.id, ["Bug"])

    reclass_ops = LabelOps.model_validate(
        {"reclass": [{"label": "Dex", "kind": "entity"}]}
    ).model_dump_json()
    consolidate = _consolidate(tmp_path, records, StubLLM())

    report = ConsolidateReport()
    # Drive _lint_labels directly with a stubbed label judgment.
    consolidate._judge_labels = _scripted_judge_labels(reclass_ops)  # type: ignore[method-assign]
    await consolidate._lint_labels(report)

    assert report.reclassified == 1
    by_label = {e["label"]: e["kind"] for e in await records.list_labels()}
    assert by_label["Dex"] == "entity"
    assert by_label["Bug"] == "meta"
    await consolidate.close()
    await records.close()


async def test_empty_delta_reruns_label_hygiene_when_fingerprint_changes(tmp_path: Path):
    """An idle sweep re-runs label hygiene when the durable vocabulary
    fingerprint changed outside the record delta path."""
    records = RecordStore(tmp_path / "memory.db", search_index=None)
    a = await records.add("Dex is the user's son")
    b = await records.add("the user works on ntrp")
    await records.set_labels(a.id, ["Dex"])
    await records.set_labels(b.id, ["ntrp"])
    consolidate = _consolidate(tmp_path, records, StubLLM())

    # First sweep advances the watermark past both records and stores the label
    # vocabulary fingerprint.
    await consolidate.run_once()
    await records.set_label_kind("Dex", "entity")

    called = {"n": 0}
    orig = consolidate._lint_labels

    async def _spy(report, labels=None):
        called["n"] += 1
        await orig(report, labels=labels)

    consolidate._lint_labels = _spy  # type: ignore[method-assign]

    # Second sweep: delta is empty, but the label vocabulary changed, so
    # _lint_labels must still be invoked.
    await consolidate.run_once()
    assert called["n"] == 1
    await consolidate.close()
    await records.close()


def _scripted_judge_labels(body: str):
    async def _judge(labels):
        return LabelOps.model_validate_json(body)

    return _judge


async def test_label_hygiene_skipped_below_two_labels(tmp_path: Path):
    records = RecordStore(tmp_path / "memory.db", search_index=None)
    a = await records.add("a lone labeled fact")
    await records.set_labels(a.id, ["solo"])
    llm = StubLLM()  # default no-op LintOps for the one neighborhood
    consolidate = _consolidate(tmp_path, records, llm)

    report = await consolidate.run_once()

    assert report.relabeled == 0
    assert [c["response_format"] for c in llm.calls] == [LintOps]
    await consolidate.close()
    await records.close()


async def test_merge_unions_labels_onto_survivor(tmp_path: Path):
    """Verify the store-level union semantics through a consolidation merge."""
    records = RecordStore(tmp_path / "memory.db", search_index=None)
    a = await records.add("the user lives in Yerevan")
    b = await records.add("the user lives in Yerevan, Armenia")
    await records.set_labels(a.id, ["Timur"])
    await records.set_labels(b.id, ["places"])
    llm = StubLLM(
        _merge_ops([a.id, b.id], merged_text="the user lives in Yerevan, Armenia"),
        _label_ops([]),
    )
    consolidate = _consolidate(tmp_path, records, llm)

    await consolidate.run_once()

    survivor = (await records.list())[0]
    assert await records.labels_of(survivor.id) == ["Timur", "places"]
    await consolidate.close()
    await records.close()


# --- watermark durability / anti-heartbeat -----------------------------------


async def test_second_sweep_is_a_noop_without_changes(tmp_path: Path):
    records = RecordStore(tmp_path / "memory.db", search_index=None)
    await records.add("a settled fact")
    llm = StubLLM()  # default LintOps() (no ops) for the first sweep's one neighborhood
    consolidate = _consolidate(tmp_path, records, llm)

    await consolidate.run_once()
    first_calls = len(llm.calls)
    assert first_calls >= 1

    # No record was confirmed after the first sweep's watermark -> the delta is
    # empty, so the second sweep spends no LLM call.
    await consolidate.run_once()
    assert len(llm.calls) == first_calls
    await consolidate.close()
    await records.close()


async def test_idle_sweep_makes_zero_llm_calls(tmp_path: Path):
    """The fingerprint cache: once a clean night has judged every neighborhood, a
    subsequent sweep with nothing changed re-judges nothing and re-runs no label
    hygiene — zero LLM calls (the waste-elimination over a full re-scan)."""
    records = RecordStore(tmp_path / "memory.db", search_index=None)
    a = await records.add("Dex is sleeping through the night")
    b = await records.add("ntrp has a memory system")
    await records.set_labels(a.id, ["Dex"])
    await records.set_labels(b.id, ["ntrp"])
    llm = StubLLM()  # default no-op LintOps/LabelOps for every call
    consolidate = _consolidate(tmp_path, records, llm)

    await consolidate.run_once()
    calls_after_first = len(llm.calls)
    assert calls_after_first > 0  # judged the two new records + one label-hygiene call

    await consolidate.run_once()  # nothing changed
    assert len(llm.calls) == calls_after_first, "an idle sweep must make zero LLM calls"

    # a changed record re-enters: its hood fingerprint differs, so it IS re-judged
    await records.update(a.id, "Dex now sleeps in his own room")
    await consolidate.run_once()
    assert len(llm.calls) > calls_after_first, "a changed record is re-judged"
    await consolidate.close()
    await records.close()


async def test_failed_label_hygiene_does_not_persist_fingerprint_and_idle_sweep_retries(tmp_path: Path):
    records = RecordStore(tmp_path / "memory.db", search_index=None)
    a = await records.add("Dex is the user's son")
    b = await records.add("ntrp has a memory system")
    await records.set_labels(a.id, ["Dex"])
    await records.set_labels(b.id, ["ntrp"])
    consolidate = _consolidate(tmp_path, records, StubLLM())

    calls = {"n": 0}

    async def _fail_judge(labels):
        calls["n"] += 1
        return None

    consolidate._judge_labels = _fail_judge  # type: ignore[method-assign]

    await consolidate.run_once()
    assert calls["n"] == 1
    assert await consolidate._read_label_fingerprint() is None

    await consolidate.run_once()
    assert calls["n"] == 2
    assert await consolidate._read_label_fingerprint() is None
    await consolidate.close()
    await records.close()


async def test_failed_neighborhood_judgment_does_not_persist_fingerprint(tmp_path: Path):
    records = RecordStore(tmp_path / "memory.db", search_index=None)
    await records.add("the user prefers tea")
    consolidate = _consolidate(tmp_path, records, StubLLM())

    async def _fail_judge(hood):
        return None

    consolidate._judge = _fail_judge  # type: ignore[method-assign]

    await consolidate.run_once()

    assert await consolidate._read_fingerprints() == {}
    await consolidate.close()
    await records.close()


async def test_failed_neighborhood_apply_does_not_persist_fingerprint(tmp_path: Path):
    records = RecordStore(tmp_path / "memory.db", search_index=None)
    await records.add("the user prefers tea")
    consolidate = _consolidate(tmp_path, records, StubLLM())

    async def _fail_apply(ops, hood, report):
        raise RuntimeError("injected apply failure")

    consolidate._apply = _fail_apply  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="injected apply failure"):
        await consolidate.run_once()

    assert await consolidate._read_fingerprints() == {}
    await consolidate.close()
    await records.close()


async def test_consolidate_report_changed_memory_tracks_all_mutations():
    assert ConsolidateReport().changed_memory is False
    assert ConsolidateReport(reclassified=1).changed_memory is True


async def test_noop_without_llm(tmp_path: Path):
    records = RecordStore(tmp_path / "memory.db", search_index=None)
    await records.add("x")
    await records.add("x")
    consolidate = Consolidate(records, None, model="memory-model", db_path=tmp_path / "memory.db")

    report = await consolidate.run_once()

    assert report.merged == 0
    assert len(await records.list()) == 2
    await consolidate.close()
    await records.close()


# --- RecordStore primitives ---------------------------------------------------


async def test_record_merge_is_atomic_and_pin_guarded(tmp_path: Path):
    records = RecordStore(tmp_path / "memory.db", search_index=None)
    s = await records.add("survivor")
    l1 = await records.add("loser one")
    l2 = await records.add("loser two")

    merged = await records.merge(s.id, [l1.id, l2.id], text="unified survivor")
    assert merged is not None
    assert merged.text == "unified survivor"
    assert (await records.get(l1.id)).superseded_by == s.id
    assert (await records.get(l2.id)).superseded_by == s.id
    assert {r.id for r in await records.list()} == {s.id}

    # A pinned member aborts the whole merge.
    p = await records.add("pinned", pinned=True)
    q = await records.add("other")
    assert await records.merge(q.id, [p.id]) is None
    assert (await records.get(p.id)).superseded_by is None
    await records.close()


async def test_updated_since_is_oldest_first_and_inclusive(tmp_path: Path):
    import asyncio

    records = RecordStore(tmp_path / "memory.db", search_index=None)
    a = await records.add("first")
    await asyncio.sleep(0.01)
    b = await records.add("second")

    rows = await records.updated_since(None, limit=10)
    assert [r.id for r in rows] == [a.id, b.id]  # oldest-confirmed first

    # `>=`-inclusive at the boundary.
    rows2 = await records.updated_since(a.last_confirmed_at, limit=10)
    assert a.id in {r.id for r in rows2}
    await records.close()


async def test_neighborhood_excludes_self(tmp_path: Path):
    records = RecordStore(tmp_path / "memory.db", search_index=None)
    a = await records.add("kubernetes deployment guide")
    b = await records.add("kubernetes cluster setup")

    hood = await records.neighborhood(a, limit=8)
    ids = {r.id for r in hood}
    assert a.id not in ids
    assert b.id in ids
    await records.close()


async def test_lessons_merge_with_lessons_but_never_cross_into_durable_hoods(tmp_path: Path):
    """Playbook hygiene: near-duplicate lessons dedup like facts do, but the
    neighborhoods are kind-partitioned so a lesson never merges with a fact."""
    from ntrp.memory.file_store import FilePageStore
    from ntrp.memory.models import SourceRef

    store = FilePageStore(tmp_path / "memory")
    await store.open()
    l1 = await store.add("Verify against the running system before reporting status.", kind="lesson", source_ref=SourceRef("curator", ""))
    l2 = await store.add("Always verify claims against the live system before reporting.", kind="lesson", source_ref=SourceRef("curator", ""))
    fact = await store.add("The user verifies systems at Dex.", kind="fact", source_ref=SourceRef("user", ""))

    class _HoodAwareLLM(StubLLM):
        """Hood order isn't deterministic — answer the merge only for the lesson hood."""

        async def completion(self, *, messages, model, reasoning_effort=None, response_format=None, **kwargs):
            self.calls.append({"messages": messages, "model": model, "response_format": response_format})
            body = messages[-1]["content"]
            if l1.id in body and l2.id in body:
                return completion_response(_merge_ops([l1.id, l2.id], merged_text="Verify against the running system before reporting status."))
            return completion_response(LintOps().model_dump_json())

    llm = _HoodAwareLLM()
    consolidate = Consolidate(store, llm, model="memory-model", db_path=tmp_path / "meta.db")

    report = await consolidate.run_once()

    assert report.merged == 1
    active = await store.list(limit=None, scopes=None)
    lessons = [r for r in active if r.kind == "lesson"]
    assert len(lessons) == 1 and lessons[0].kind == "lesson", lessons
    assert any(r.id == fact.id for r in active), "fact untouched"
    # no judged neighborhood ever mixed a lesson with a durable record
    for call in llm.calls:
        body = call["messages"][-1]["content"]
        if '"kind": "lesson"' in body:
            assert '"kind": "fact"' not in body, "lesson hood contaminated with a durable record"
    await consolidate.close()
    await store.close()


async def test_lesson_is_never_retyped_to_durable(tmp_path: Path):
    """Trust boundary: the judge cannot promote an agent-inferred lesson to a fact."""
    from ntrp.memory.file_store import FilePageStore
    from ntrp.memory.models import SourceRef

    store = FilePageStore(tmp_path / "memory")
    await store.open()
    lesson = await store.add("Prefer editing artifacts in place.", kind="lesson", source_ref=SourceRef("curator", ""))

    retype = LintOps.model_validate({"retypes": [{"record_id": lesson.id, "kind": "fact"}]}).model_dump_json()
    llm = StubLLM(retype)
    consolidate = Consolidate(store, llm, model="memory-model", db_path=tmp_path / "meta.db")

    report = await consolidate.run_once()

    assert report.retyped == 0
    assert (await store.get(lesson.id)).kind == "lesson"
    await consolidate.close()
    await store.close()
