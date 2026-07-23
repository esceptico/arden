"""run_memory_init (arden/memory/init.py) — the /init full re-derivation driver.

Phase 1 = TRANSCRIPTS ONLY. Hermetic: a STUB curator LLM (scripted single-call
JSON ops, reusing the test_memory_curator pattern), a STUB consolidate LLM (empty
ops so the sweep no-ops but still advances its watermark), a STUB sessions store,
and a real tmp RecordStore (`search_index=None` -> FTS-only). The whole memory
lives in a tmp sqlite so ~/.Arden is never touched.

Proves:
  (a) the PINNED record survives the wipe;
  (b) the 3 non-pinned records are gone;
  (c) curate_watermark:%/consolidate_watermark meta rows were cleared then re-written;
  (d) >=1 record was re-derived from the seeded chat session;
  (e) the backup file exists at the returned backup_path;
  (f) the report counts are right.
"""

import json
from pathlib import Path

from arden.memory.consolidate import Consolidate
from arden.memory.curator import Curator
from arden.memory.init import run_memory_init
from arden.memory.records import RecordStore
from tests.conftest import completion_response
from tests.test_memory_curator import StubSessions, _make_file_curator, _scope, _turn


class CuratorStubLLM:
    """Curator LLM: returns queued op-JSON payloads (FIFO), then empty ops."""

    def __init__(self, *responses: str):
        self._queue = list(responses)
        self.calls: list[dict] = []

    async def completion(self, *, messages, model, reasoning_effort=None, **kwargs):
        self.calls.append({"messages": messages, "model": model})
        body = self._queue.pop(0) if self._queue else json.dumps({"records": []})
        return completion_response(body)


class ConsolidateStubLLM:
    """Consolidate LLM: always returns empty structured ops ({}) so every
    LintOps/LabelOps judgment validates to a no-op — the sweep advances its
    watermark without mutating the pool."""

    def __init__(self):
        self.calls = 0

    async def completion(self, *, messages, model, reasoning_effort=None, **kwargs):
        self.calls += 1
        return completion_response("{}")


class FakeConfig:
    def __init__(self, db_path: Path, artifacts_dir: Path):
        self.memory_db_path = db_path
        self.memory_artifacts_dir = artifacts_dir


class FakeKnowledge:
    """Minimal KnowledgeRuntime stand-in exposing exactly what run_memory_init
    reaches for: _record_store, memory_curator, _consolidate, config."""

    def __init__(self, record_store, curator, consolidate, config):
        self._record_store = record_store
        self.memory_curator = curator
        self._consolidate = consolidate
        self.config = config

    @property
    def memory_ready(self) -> bool:
        return self._record_store is not None

    def _memory_llm(self):
        # These tests target P1–P3 (wipe/re-derive/budget); keep P4 mechanical so
        # synthesis calls don't pollute the curation call-count assertions.
        return None, ""


async def _read_meta(db_path: Path, key_like: str) -> list[tuple[str, str]]:
    from arden.database import connect as db_connect

    conn = await db_connect(db_path)
    try:
        rows = await conn.execute_fetchall("SELECT key, value FROM meta WHERE key LIKE ?", (key_like,))
        return [(r["key"], r["value"]) for r in rows]
    finally:
        await conn.close()


async def test_run_memory_init_wipes_resets_and_rederives(tmp_path: Path):
    db_path = tmp_path / "memory.db"
    artifacts_dir = tmp_path / "artifacts"

    records = RecordStore(db_path, search_index=None)
    await records.open()

    # Seed: 1 pinned survivor + 3 non-pinned victims.
    pinned = await records.add("the user is pinned and must survive", kind="fact")
    await records.set_pinned(pinned.id, True)
    for i in range(3):
        await records.add(f"transient victim record {i}", kind="fact")

    # A chat session whose turns the stub turns into exactly 1 ADD.
    sessions = StubSessions(
        rows={"chat1": [_turn(0, "user", "I always prefer green tea over coffee")]},
        scopes=[_scope("chat1")],
    )
    curator_llm = CuratorStubLLM(
        json.dumps({"records": [{"op": "ADD", "text": "the user prefers green tea over coffee", "kind": "fact"}]})
    )
    curator = Curator(
        curator_llm,
        sessions,
        model="memory-model",
        db_path=db_path,
        record_store=records,
    )

    consolidate = Consolidate(records, ConsolidateStubLLM(), model="memory-model", db_path=db_path)
    # Seed a stale consolidate fingerprint + stale curator watermarks so we can prove
    # they were cleared.
    await consolidate._write_fingerprint("stale-record", "deadbeefdeadbeef")
    await curator._write_watermark("chat1", 999)
    await curator._write_watermark("ghost", 5)

    config = FakeConfig(db_path, artifacts_dir)
    knowledge = FakeKnowledge(records, curator, consolidate, config)

    report = await run_memory_init(knowledge, max_llm_calls=50, wipe=True)

    # (a) pinned survives, (b) the 3 non-pinned are gone, (d) >=1 re-derived.
    active = await records.list(limit=None)
    texts = {r.text for r in active}
    assert "the user is pinned and must survive" in texts
    assert not any(t.startswith("transient victim record") for t in texts)
    assert "the user prefers green tea over coffee" in texts

    # (c) curator watermarks cleared (the stale 999/ghost gone) then a fresh one
    # re-written for the drained chat1; the stale consolidate fingerprint was cleared.
    curate_rows = dict(await _read_meta(db_path, "curate_watermark:%"))
    assert "curate_watermark:ghost" not in curate_rows  # the reset wiped the ghost
    assert curate_rows.get("curate_watermark:chat1") == "0"  # re-derived from seq 0
    assert "stale-record" not in await consolidate._read_fingerprints()  # /init cleared it

    # (e) backup exists.
    assert report["backup_path"]
    assert Path(report["backup_path"]).exists()

    # (f) report counts.
    assert report["deleted"] == 3
    assert report["kept_pinned"] == 1
    assert report["sessions_processed"] == 1
    assert report["admitted"] >= 1
    assert report["capped"] is False
    assert report["consolidate"]["passes"] >= 1

    # Exactly one curator LLM call: one batch drained the single-turn session.
    assert len(curator_llm.calls) == 1
    assert sessions.scope_reads == 1  # enumeration only; full curation receives row area_id explicitly

    # File-canonical: no artifact projection is rebuilt (records were written
    # straight to their pages).

    await consolidate.close()
    await curator.stop()
    await records.close()


async def test_separate_file_store_init_runs_use_distinct_derivation_generations(tmp_path: Path):
    sessions = StubSessions(
        rows={"chat1": [_turn(0, "user", "Re-derive this session")]},
        scopes=[_scope("chat1")],
    )
    llm = CuratorStubLLM(
        json.dumps({"records": [{"op": "ADD", "text": "First derivation", "kind": "fact"}]}),
        json.dumps({"records": [{"op": "ADD", "text": "Second derivation", "kind": "fact"}]}),
    )
    curator, records = await _make_file_curator(tmp_path, llm, sessions)
    knowledge = FakeKnowledge(
        records,
        curator,
        None,
        FakeConfig(tmp_path / "memory.db", tmp_path / "artifacts"),
    )

    await run_memory_init(knowledge)
    await run_memory_init(knowledge)

    assert len(llm.calls) == 2
    assert {record.text for record in await records.list()} == {"First derivation", "Second derivation"}
    await curator.stop()
    await records.close()


async def test_run_memory_init_additive_default_keeps_records_and_uses_bulk_gate(tmp_path: Path):
    """Default /init is ADDITIVE: existing records survive (never go backwards) and
    the re-derivation runs the curator in BULK mode (the generous gate)."""
    db_path = tmp_path / "memory.db"
    records = RecordStore(db_path, search_index=None)
    await records.open()

    # Pre-existing, non-pinned records that must SURVIVE an additive init.
    for i in range(3):
        await records.add(f"existing durable fact {i}", kind="fact")

    sessions = StubSessions(
        rows={"chat1": [_turn(0, "user", "I am a research engineer at ThirdLayer")]},
        scopes=[_scope("chat1")],
    )
    curator_llm = CuratorStubLLM(
        json.dumps(
            {"records": [{"op": "ADD", "text": "the user is a research engineer at ThirdLayer", "kind": "fact"}]}
        )
    )
    curator = Curator(curator_llm, sessions, model="memory-model", db_path=db_path, record_store=records)
    consolidate = Consolidate(records, ConsolidateStubLLM(), model="memory-model", db_path=db_path)
    knowledge = FakeKnowledge(records, curator, consolidate, FakeConfig(db_path, tmp_path / "artifacts"))

    report = await run_memory_init(knowledge, max_llm_calls=50)  # wipe defaults to False

    texts = {r.text for r in await records.list(limit=None)}
    # Existing records kept (additive can only enrich), new one added.
    assert all(f"existing durable fact {i}" in texts for i in range(3))
    assert "the user is a research engineer at ThirdLayer" in texts
    assert report["wiped"] is False
    assert report["deleted"] == 0

    # The re-derivation engaged the BULK gate (generous capture), not the brutal
    # turn-by-turn worthiness bar.
    assert "BULK RE-DERIVATION MODE" in curator_llm.calls[0]["messages"][0]["content"]

    await consolidate.close()
    await curator.stop()
    await records.close()


async def test_run_memory_init_caps_at_budget(tmp_path: Path):
    """max_llm_calls bounds re-derivation: with the budget below the work, the
    report flags capped=true and stops early."""
    db_path = tmp_path / "memory.db"
    records = RecordStore(db_path, search_index=None)
    await records.open()

    # Two chat sessions; budget of 1 should process one then cap.
    sessions = StubSessions(
        rows={
            "chat1": [_turn(0, "user", "fact one")],
            "chat2": [_turn(0, "user", "fact two")],
        },
        scopes=[_scope("chat1"), _scope("chat2")],
    )
    curator_llm = CuratorStubLLM(
        json.dumps({"records": [{"op": "ADD", "text": "the user likes one", "kind": "fact"}]}),
        json.dumps({"records": [{"op": "ADD", "text": "the user likes two", "kind": "fact"}]}),
    )
    curator = Curator(curator_llm, sessions, model="memory-model", db_path=db_path, record_store=records)
    consolidate = Consolidate(records, ConsolidateStubLLM(), model="memory-model", db_path=db_path)
    config = FakeConfig(db_path, tmp_path / "artifacts")
    knowledge = FakeKnowledge(records, curator, consolidate, config)

    report = await run_memory_init(knowledge, max_llm_calls=1)

    assert report["capped"] is True
    assert report["sessions_processed"] == 1

    await consolidate.close()
    await curator.stop()
    await records.close()


def test_prune_init_backups_keeps_newest(tmp_path: Path):
    from arden.memory.init import _prune_init_backups

    db = tmp_path / "memory.db"
    db.write_text("db", encoding="utf-8")
    # Lexicographic order == chronological (fixed-width stamps).
    stamps = ["20260101T000000", "20260102T000000", "20260103T000000", "20260104T000000", "20260105T000000"]
    for s in stamps:
        (tmp_path / f"memory.db.init-bak-{s}").write_text("bak", encoding="utf-8")

    _prune_init_backups(db, keep=3)

    remaining = sorted(p.name for p in tmp_path.glob("memory.db.init-bak-*"))
    assert remaining == [f"memory.db.init-bak-{s}" for s in stamps[-3:]]
    assert db.exists()  # the live db is never pruned
