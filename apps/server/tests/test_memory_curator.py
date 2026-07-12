"""Curator (the Dreamer) — the sleep-time memory writer (ntrp/memory/curator.py).

ONE LLM call per session emits a SINGLE JSON object `{"records": [ADD|UPDATE|
SUPERSEDE|NOOP ...]}` whose write ops carry open-vocabulary `labels`. The call
reconciles the flat record pool; there is NO lens write path. Hermetic: a STUB
LLM (scripted single-call JSON responses) + a STUB sessions store (in-memory
`messages_since`) + a real tmp RecordStore (`search_index=None` -> FTS-only).
The watermark + records live in a tmp sqlite (injected via the Curator's
`db_path` arg so the real ~/.ntrp/memory.db is never touched).

Covers:
  (a) empty ops (admit gate) -> no record writes, watermark advances;
  (b) the watermark filters already-seen seqs;
  (c) a content-less completion -> no writes, watermark NOT advanced;
  (d) record ops: ADD inserts, UPDATE/SUPERSEDE append successors,
      NOOP does not mutate — exactly one LLM call per curation;
  (e) labels: ADD sets them, UPDATE replaces-or-keeps, SUPERSEDE's successor
      takes the op's labels else inherits, and the prompt carries the LABEL
      VOCABULARY (top names + the recalled records' labels);
  (f) the sweep schedules curation for recent USER CHATS only — automation
      channels and agent sessions are skipped.
"""

import asyncio
import json
from pathlib import Path

import pytest

from ntrp.memory.curator import Curator
from ntrp.memory.file_store import FilePageStore
from ntrp.memory.ledger import LedgerEntry, LedgerMeta, render_ledger_entry
from ntrp.memory.models import Kind, SourceRef
from ntrp.memory.page_events import PageEditAnalysis
from ntrp.memory.records import RecordStore
from ntrp.memory.scopes import MemoryScope
from tests.conftest import completion_response

pytestmark = pytest.mark.asyncio


class StubLLM:
    """Scripted completion client: returns queued payloads (FIFO) and records
    every call so a test can assert the cost ceiling (one call per curation)."""

    def __init__(self, *responses: str):
        self._queue = list(responses)
        self.calls: list[dict] = []

    async def completion(self, *, messages, model, reasoning_effort=None, **kwargs):
        self.calls.append({"messages": messages, "model": model, "reasoning_effort": reasoning_effort})
        body = self._queue.pop(0) if self._queue else ""
        return completion_response(body)


class StubSessions:
    """Minimal SessionService stand-in. Holds ordered transcript rows in the
    `_message_row_payload` shape (seq/role/message) and serves messages_since.
    `scopes` is the preset sweep worklist; rows carry the origin fields the
    sweep gates on ({session_id, area_id, session_type, origin_automation_id})."""

    def __init__(
        self,
        rows: dict[str, list[dict]] | None = None,
        scopes: list[dict] | None = None,
        areas: dict[str, dict] | None = None,
    ):
        self._rows = rows or {}
        self._scopes = scopes or []
        self._areas = areas or {}
        self.scope_reads = 0
        self.exact_scope_reads = 0

    def set_rows(self, session_id: str, rows: list[dict]) -> None:
        self._rows[session_id] = rows

    async def messages_since(self, session_id: str, seq: int) -> list[dict]:
        return [r for r in self._rows.get(session_id, []) if r["seq"] > seq]

    async def recent_session_scopes(self, limit: int) -> list[dict]:
        self.scope_reads += 1
        return self._scopes[:limit]

    async def session_scope(self, session_id: str) -> dict | None:
        self.exact_scope_reads += 1
        return next((scope for scope in self._scopes if scope["session_id"] == session_id), None)

    async def get_area(self, area_id: str | None) -> dict | None:
        return self._areas.get(area_id) if area_id is not None else None


def _turn(seq: int, role: str, text: str) -> dict:
    return {
        "seq": seq,
        "message_id": f"m{seq}",
        "role": role,
        "created_at": f"2026-07-12T10:00:{seq:02d}Z",
        "message": {"role": role, "content": text},
    }


def _scope(session_id: str, *, session_type: str = "chat", origin_automation_id: str | None = None) -> dict:
    return {
        "session_id": session_id,
        "area_id": None,
        "session_type": session_type,
        "origin_automation_id": origin_automation_id,
    }


def _ops_json(records: list[dict] | None = None) -> str:
    """The single JSON object the Curator expects from the LLM."""
    return json.dumps({"records": records or []})


def _make_curator(tmp_path: Path, llm, sessions) -> tuple[Curator, RecordStore]:
    records = RecordStore(tmp_path / "memory.db", search_index=None)
    curator = Curator(
        llm,
        sessions,
        model="memory-model",
        db_path=tmp_path / "memory.db",
        record_store=records,
    )
    return curator, records


async def _make_file_curator(tmp_path: Path, llm, sessions) -> tuple[Curator, FilePageStore]:
    vault = tmp_path / "vault"
    visible = vault / "me.md"
    raw = vault / "raw" / "me.md"
    visible.parent.mkdir(parents=True)
    raw.parent.mkdir(parents=True)
    visible.write_text("# Me\n", encoding="utf-8")
    raw.write_text("<!-- ntrp:records schema=2 page=me.md -->\n", encoding="utf-8")
    records = FilePageStore(vault)
    await records.open()
    curator = Curator(
        llm,
        sessions,
        model="memory-model",
        db_path=tmp_path / "memory.db",
        record_store=records,
    )
    return curator, records


# --- admit gate / watermark -----------------------------------------------------


async def test_reconcile_page_edit_returns_typed_operations_without_applying(tmp_path: Path):
    llm = StubLLM(_ops_json([{"op": "ADD", "text": "New durable statement.", "kind": "fact"}]))
    curator, records = _make_curator(tmp_path, llm, StubSessions())
    await records.open()
    analysis = PageEditAnalysis(
        path="topics/a.md",
        before=("Old durable statement.",),
        after=("New durable statement.",),
        changed_before=("Old durable statement.",),
        changed_after=("New durable statement.",),
        patch="patch",
    )

    operations = await curator.reconcile_page_edit(analysis)

    assert [operation.op for operation in operations] == ["ADD"]
    assert operations[0].text == "New durable statement."
    assert await records.list() == []
    prompt = llm.calls[0]["messages"][1]["content"]
    assert "PAGE EDIT RECONCILIATION" in prompt
    assert json.loads(prompt.splitlines()[-1])["content"] == analysis.model_dump_json()
    system = llm.calls[0]["messages"][0]["content"]
    assert "structural memory page edits" in system
    assert "ASK" in system and "RETRACT" in system
    await curator.stop()
    await records.close()


async def test_empty_ops_skip_writes_but_advance_watermark(tmp_path: Path):
    llm = StubLLM(_ops_json([]))
    sessions = StubSessions({"s1": [_turn(0, "user", "what time is it")]})
    curator, records = _make_curator(tmp_path, llm, sessions)

    await curator.curate_session("s1")

    assert len(llm.calls) == 1  # the gate ran once, then declined
    assert await records.list() == []  # no record writes
    assert await curator._read_watermark("s1") == 0  # advanced to max seq seen
    await curator.stop()
    await records.close()


async def test_no_new_turns_does_not_enter_curate_llm_span(tmp_path: Path, monkeypatch):
    llm = StubLLM(_ops_json([{"op": "ADD", "text": "should not be reached"}]))
    sessions = StubSessions({"s1": [_turn(0, "user", "already curated")]})
    curator, records = _make_curator(tmp_path, llm, sessions)
    await curator._write_watermark("s1", 0)

    async def fail_complete(*_args, **_kwargs):
        raise AssertionError("_complete should only run when there are new turns")

    monkeypatch.setattr(curator, "_complete", fail_complete)

    changed = await curator.curate_session("s1")

    assert changed is False
    assert len(llm.calls) == 0
    await curator.stop()
    await records.close()


async def test_watermark_filters_already_seen_seqs(tmp_path: Path):
    llm = StubLLM(
        _ops_json([{"op": "ADD", "text": "captured fact"}]),
        _ops_json([{"op": "ADD", "text": "should not be reached"}]),
    )
    sessions = StubSessions(
        {
            "s1": [
                _turn(0, "user", "old turn already curated"),
                _turn(1, "user", "brand new durable fact"),
            ]
        }
    )
    curator, records = _make_curator(tmp_path, llm, sessions)

    await curator._write_watermark("s1", 0)  # pretend seq 0 already curated

    changed = await curator.curate_session("s1")
    assert changed is True

    assert len(llm.calls) == 1
    user_prompt = llm.calls[0]["messages"][1]["content"]
    assert "brand new durable fact" in user_prompt
    assert "old turn already curated" not in user_prompt
    assert await curator._read_watermark("s1") == 1
    await curator.stop()
    await records.close()


async def test_curator_ignores_system_rows(tmp_path: Path):
    llm = StubLLM(_ops_json([]))
    sessions = StubSessions(
        {
            "s1": [
                _turn(0, "system", "large repeated system prompt should not enter memory"),
                _turn(1, "user", "I prefer tea"),
            ]
        }
    )
    curator, records = _make_curator(tmp_path, llm, sessions)

    await curator.curate_session("s1")

    assert len(llm.calls) == 1
    user_prompt = llm.calls[0]["messages"][1]["content"]
    assert "I prefer tea" in user_prompt
    assert "large repeated system prompt" not in user_prompt
    assert await curator._read_watermark("s1") == 1
    await curator.stop()
    await records.close()


async def test_curator_chunks_large_backlog_before_llm(tmp_path: Path):
    llm = StubLLM(_ops_json([]))
    rows = [_turn(i, "user", f"fact {i} " + ("x" * 1000)) for i in range(20)]
    sessions = StubSessions({"s1": rows})
    curator, records = _make_curator(tmp_path, llm, sessions)

    await curator.curate_session("s1")

    assert len(llm.calls) == 1
    user_prompt = llm.calls[0]["messages"][1]["content"]
    assert len(user_prompt) < 8_000
    assert await curator._read_watermark("s1") < rows[-1]["seq"]
    await curator.stop()
    await records.close()


async def test_no_new_turns_advances_without_llm(tmp_path: Path):
    llm = StubLLM(_ops_json([{"op": "ADD", "text": "never called"}]))
    sessions = StubSessions({"s1": [_turn(5, "user", "hello")]})
    curator, records = _make_curator(tmp_path, llm, sessions)
    await curator._write_watermark("s1", 5)  # everything already seen

    changed = await curator.curate_session("s1")

    assert changed is False
    assert llm.calls == []  # the admit gate never even spent an LLM call
    assert await curator._read_watermark("s1") == 5
    await curator.stop()
    await records.close()


@pytest.mark.parametrize("blank", ["", "   ", "\n\n"])
async def test_empty_completion_does_not_write_or_advance(tmp_path: Path, blank: str):
    """A content-less but non-erroring completion must NOT write records or advance
    the watermark — the turns retry next session."""
    llm = StubLLM(blank)
    sessions = StubSessions({"s1": [_turn(0, "user", "some durable new fact")]})
    curator, records = _make_curator(tmp_path, llm, sessions)

    changed = await curator.curate_session("s1")

    assert changed is False
    assert len(llm.calls) == 1  # the call happened…
    assert await records.list() == []  # …no record writes
    assert await curator._read_watermark("s1") == -1  # NOT advanced -> retried
    await curator.stop()
    await records.close()


async def test_unparseable_completion_does_not_advance(tmp_path: Path):
    llm = StubLLM("not json at all")
    sessions = StubSessions({"s1": [_turn(0, "user", "a durable fact")]})
    curator, records = _make_curator(tmp_path, llm, sessions)

    changed = await curator.curate_session("s1")

    assert changed is False
    assert await records.list() == []
    assert await curator._read_watermark("s1") == -1
    await curator.stop()
    await records.close()


async def test_assistant_envelope_and_area_scope_are_preserved(tmp_path: Path):
    llm = StubLLM(_ops_json([{"op": "ADD", "text": "The deployment passed", "kind": "fact"}]))
    sessions = StubSessions(
        {"s1": [_turn(0, "assistant", "The deployment passed")]},
        scopes=[{**_scope("s1"), "area_id": "a1"}],
        areas={"a1": {"area_id": "a1", "knowledge_scope": "area:a1"}},
    )
    curator, records = await _make_file_curator(tmp_path, llm, sessions)

    await curator.curate_session("s1")

    prompt = llm.calls[0]["messages"][1]["content"]
    assert '"role":"assistant"' in prompt
    assert '"message_id":"m0"' in prompt
    assert '"created_at":"2026-07-12T10:00:00Z"' in prompt
    record = (await records.list())[0]
    assert (record.scope_kind, record.scope_key) == ("area", "a1")
    await curator.stop()
    await records.close()


async def test_session_older_than_sweep_window_keeps_exact_area_scope(tmp_path: Path):
    llm = StubLLM(_ops_json([{"op": "ADD", "text": "Old area fact", "kind": "fact"}]))
    recent = [_scope(f"recent-{index}") for index in range(50)]
    sessions = StubSessions(
        {"old": [_turn(0, "user", "Old area fact")]},
        scopes=[*recent, {**_scope("old"), "area_id": "a1"}],
        areas={"a1": {"area_id": "a1", "knowledge_scope": "area:a1"}},
    )
    curator, records = await _make_file_curator(tmp_path, llm, sessions)

    await curator.curate_session("old")

    record = (await records.list())[0]
    assert (record.scope_kind, record.scope_key) == ("area", "a1")
    assert sessions.exact_scope_reads == 1
    await curator.stop()
    await records.close()


async def test_ask_keeps_watermark_retryable_and_does_not_commit(tmp_path: Path, monkeypatch):
    llm = StubLLM(_ops_json([{"op": "ASK", "question": "Which tea do you mean?"}]))
    sessions = StubSessions({"s1": [_turn(0, "user", "I prefer that tea")]})
    curator, records = await _make_file_curator(tmp_path, llm, sessions)
    commits = 0

    def reject_commit(_files):
        nonlocal commits
        commits += 1
        raise AssertionError("ASK must not commit")

    monkeypatch.setattr(records._journal, "commit", reject_commit)

    assert await curator.curate_session("s1") is False
    assert await curator._read_watermark("s1") == -1
    assert curator.pending_question("s1") == "Which tea do you mean?"
    assert commits == 0
    await curator.stop()
    await records.close()


async def test_retry_after_watermark_failure_does_not_duplicate_committed_batch(tmp_path: Path, monkeypatch):
    response = _ops_json(
        [
            {"op": "ADD", "text": "User drinks tea", "kind": "fact"},
            {"op": "ADD", "text": "User likes ceramic mugs", "kind": "fact"},
        ]
    )
    llm = StubLLM(response)
    sessions = StubSessions({"s1": [_turn(0, "user", "I drink tea from ceramic mugs")]})
    curator, records = await _make_file_curator(tmp_path, llm, sessions)
    write_watermark = curator._write_watermark
    failed = False

    async def fail_once(session_id: str, value: int):
        nonlocal failed
        if not failed:
            failed = True
            raise RuntimeError("injected watermark failure")
        await write_watermark(session_id, value)

    monkeypatch.setattr(curator, "_write_watermark", fail_once)

    with pytest.raises(RuntimeError, match="injected watermark failure"):
        await curator.curate_session("s1")

    before = await records.list()
    before_history = sum(len(records.history(record.id)) for record in before)
    batch_key = curator._batch_key("s1", 0)
    assert {
        entry.meta.extra.get("batch_key")
        for record in before
        for entry in records.history(record.id)
    } == {batch_key}

    assert await curator.curate_session("s1") is False
    after = await records.list()
    assert len(after) == len(before) == 2
    assert sum(len(records.history(record.id)) for record in after) == before_history == 2
    assert await curator._read_watermark("s1") == 0
    assert len(llm.calls) == 1
    await curator.stop()
    await records.close()


async def test_mixed_roles_persist_one_exact_source_per_turn(tmp_path: Path):
    llm = StubLLM(
        _ops_json(
            [
                {"op": "ADD", "text": "Deployment passed", "kind": "fact"},
                {"op": "ADD", "text": "User asked about deployment", "kind": "fact"},
            ]
        )
    )
    sessions = StubSessions(
        {
            "s1": [
                _turn(0, "user", "Did the deployment pass?"),
                _turn(1, "assistant", "Yes, the deployment passed."),
            ]
        }
    )
    curator, records = await _make_file_curator(tmp_path, llm, sessions)

    await curator.curate_session("s1")

    for record in await records.list():
        assert [(source.ref, source.role) for source in record.sources] == [("m0", "user"), ("m1", "assistant")]
        assert [source.occurred_at for source in record.sources] == [
            "2026-07-12T10:00:00Z",
            "2026-07-12T10:00:01Z",
        ]
        assert all(source.time_precision == "second" for source in record.sources)
        assert all(source.captured_at is not None for source in record.sources)
    await curator.stop()
    await records.close()


async def test_failed_second_operation_keeps_watermark_retryable(tmp_path: Path, monkeypatch):
    response = _ops_json(
        [
            {"op": "ADD", "text": "User drinks tea", "kind": "fact"},
            {"op": "ADD", "text": "User likes ceramic mugs", "kind": "fact"},
        ]
    )
    llm = StubLLM(response, response)
    sessions = StubSessions({"s1": [_turn(0, "user", "I drink tea from ceramic mugs")]})
    curator, records = await _make_file_curator(tmp_path, llm, sessions)

    failed = False

    def fail_mid_commit(point: str):
        nonlocal failed
        if point == "after_replace:0" and not failed:
            failed = True
            raise RuntimeError("injected batch failure")

    monkeypatch.setattr(records._journal, "_checkpoint", fail_mid_commit)

    with pytest.raises(RuntimeError, match="injected batch failure"):
        await curator.curate_session("s1")

    assert await records.list() == []
    assert await curator._read_watermark("s1") == -1

    assert await curator.curate_session("s1") is True
    assert {record.text for record in await records.list()} == {
        "User drinks tea",
        "User likes ceramic mugs",
    }
    assert await curator._read_watermark("s1") == 0
    await curator.stop()
    await records.close()


async def test_full_curation_uses_explicit_area_without_scope_query(tmp_path: Path):
    llm = StubLLM(_ops_json([]), _ops_json([]))
    sessions = StubSessions(
        {"s1": [_turn(seq, "user", f"fact {seq}") for seq in range(41)]},
        scopes=[{**_scope("s1"), "area_id": "a1"}],
        areas={"a1": {"area_id": "a1", "knowledge_scope": "area:a1"}},
    )
    curator, records = await _make_file_curator(tmp_path, llm, sessions)

    result = await curator.curate_session_fully("s1", area_id="a1")

    assert result["calls"] == 2
    assert sessions.scope_reads == 0
    assert sessions.exact_scope_reads == 0
    await curator.stop()
    await records.close()


async def test_full_curation_without_area_uses_exact_session_scope(tmp_path: Path):
    llm = StubLLM(_ops_json([{"op": "ADD", "text": "Exact area fact", "kind": "fact"}]))
    sessions = StubSessions(
        {"s1": [_turn(0, "user", "Exact area fact")]},
        scopes=[{**_scope("s1"), "area_id": "a1"}],
        areas={"a1": {"area_id": "a1", "knowledge_scope": "area:a1"}},
    )
    curator, records = await _make_file_curator(tmp_path, llm, sessions)

    await curator.curate_session_fully("s1")

    record = (await records.list())[0]
    assert (record.scope_kind, record.scope_key) == ("area", "a1")
    assert sessions.exact_scope_reads == 1
    await curator.stop()
    await records.close()


async def test_full_curation_retry_is_idempotent_within_derivation_run(tmp_path: Path, monkeypatch):
    response = _ops_json([{"op": "ADD", "text": "Derived once", "kind": "fact"}])
    llm = StubLLM(response)
    sessions = StubSessions({"s1": [_turn(0, "user", "Derived once")]})
    curator, records = await _make_file_curator(tmp_path, llm, sessions)
    write_watermark = curator._write_watermark
    failed = False

    async def fail_once(session_id: str, value: int):
        nonlocal failed
        if not failed:
            failed = True
            raise RuntimeError("injected watermark failure")
        await write_watermark(session_id, value)

    monkeypatch.setattr(curator, "_write_watermark", fail_once)

    with pytest.raises(RuntimeError, match="injected watermark failure"):
        await curator.curate_session_fully("s1", derivation_run_id="init-run-1")

    before = await records.list()
    await curator.curate_session_fully("s1", derivation_run_id="init-run-1")
    after = await records.list()
    assert [record.id for record in after] == [record.id for record in before]
    assert len(llm.calls) == 1
    assert await curator._read_watermark("s1") == 0
    await curator.stop()
    await records.close()


# --- record ops: ADD / UPDATE / SUPERSEDE / NOOP ------------------------------


async def test_add_op_inserts_a_record(tmp_path: Path):
    llm = StubLLM(_ops_json([{"op": "ADD", "text": "the user uses Linux", "kind": "fact"}]))
    sessions = StubSessions({"s1": [_turn(0, "user", "I run Linux")]})
    curator, records = _make_curator(tmp_path, llm, sessions)

    changed = await curator.curate_session("s1")

    assert changed is True
    assert len(llm.calls) == 1
    rows = await records.list()
    assert [r.text for r in rows] == ["the user uses Linux"]
    assert rows[0].kind == "fact"
    # Dreamer-written records preserve exact turn provenance.
    assert rows[0].source_ref is not None
    assert rows[0].source_ref.kind == "chat_message"
    assert rows[0].source_ref.ref == "m0"
    assert await curator._read_watermark("s1") == 0
    await curator.stop()
    await records.close()


async def test_update_op_edits_and_confirms(tmp_path: Path):
    curator, records = _make_curator(tmp_path, StubLLM(), StubSessions())
    existing = await records.add("the user lives in Berlin")
    before = (await records.get(existing.id)).last_confirmed_at

    llm = StubLLM(_ops_json([{"op": "UPDATE", "id": existing.id, "text": "the user lives in Munich"}]))
    sessions = StubSessions({"s1": [_turn(0, "user", "I moved to Munich")]})
    curator2 = Curator(
        llm,
        sessions,
        model="memory-model",
        db_path=tmp_path / "memory.db",
        record_store=records,
    )
    await asyncio.sleep(0.01)

    changed = await curator2.curate_session("s1")

    assert changed is True
    got = (await records.list())[0]
    assert got.id != existing.id
    assert got.text == "the user lives in Munich"
    assert got.last_confirmed_at > before
    assert len(llm.calls) == 1
    await curator2.stop()
    await curator.stop()
    await records.close()


async def test_update_op_uses_v2_successor_for_labels_and_evidence(tmp_path: Path):
    vault = tmp_path / "vault"
    visible = vault / "topics" / "a.md"
    raw = vault / "raw" / "topics" / "a.md"
    visible.parent.mkdir(parents=True)
    raw.parent.mkdir(parents=True)
    visible.write_text("# A\n", encoding="utf-8")
    original_source = SourceRef("chat_message", "s:m0", captured_at="2026-07-12T10:00:00Z")
    entry = LedgerEntry(
        id="original",
        text="the user lives in Berlin",
        kind=Kind.FACT,
        occurred_at="2026-07-12T10:00:00Z",
        meta=LedgerMeta(
            recorded_at="2026-07-12T10:00:01Z",
            sequence=1,
            time_precision="second",
            scope_kind="user",
            scope_key=None,
            sources=(original_source,),
        ),
    )
    raw.write_text(
        "<!-- ntrp:records schema=2 page=topics/a.md -->\n" + render_ledger_entry(entry) + "\n",
        encoding="utf-8",
    )
    records = FilePageStore(vault)
    await records.open()
    curator = Curator(StubLLM(), StubSessions(), model="memory-model", db_path=tmp_path / "m.db", record_store=records)
    correction = SourceRef("curator", "s1", captured_at="2026-07-12T10:01:00Z")

    updated = await curator._apply_op(
        {"op": "UPDATE", "id": entry.id, "text": "the user lives in Munich", "labels": ["Munich"]},
        "s1",
        correction,
    )

    assert updated is not None
    assert updated.id != entry.id
    assert await records.get(entry.id) is None
    assert (await records.get(updated.id)).text == "the user lives in Munich"
    assert await records.labels_of(updated.id) == ["Munich"]
    assert [source.ref for source in records.history(entry.id)[-1].meta.sources] == ["s:m0", "s1"]
    await curator.stop()
    await records.close()


async def test_supersede_op_closes_old_and_inserts_new(tmp_path: Path):
    curator, records = _make_curator(tmp_path, StubLLM(), StubSessions())
    old = await records.add("the user works at Acme")

    llm = StubLLM(_ops_json([{"op": "SUPERSEDE", "id": old.id, "text": "the user works at Globex", "kind": "fact"}]))
    sessions = StubSessions({"s1": [_turn(0, "user", "I switched jobs to Globex")]})
    curator2 = Curator(
        llm,
        sessions,
        model="memory-model",
        db_path=tmp_path / "memory.db",
        record_store=records,
    )

    changed = await curator2.curate_session("s1")

    assert changed is True
    closed = await records.get(old.id)
    assert closed.superseded_by is not None
    active = await records.list()  # list excludes superseded
    assert [r.text for r in active] == ["the user works at Globex"]
    assert closed.superseded_by == active[0].id
    assert active[0].kind == "fact"
    assert len(llm.calls) == 1
    await curator2.stop()
    await curator.stop()
    await records.close()


async def test_noop_op_does_not_mutate_existing(tmp_path: Path):
    curator, records = _make_curator(tmp_path, StubLLM(), StubSessions())
    existing = await records.add("the user prefers dark mode")
    before = (await records.get(existing.id)).last_confirmed_at

    llm = StubLLM(_ops_json([{"op": "NOOP"}]))
    sessions = StubSessions({"s1": [_turn(0, "user", "still on dark mode")]})
    curator2 = Curator(
        llm,
        sessions,
        model="memory-model",
        db_path=tmp_path / "memory.db",
        record_store=records,
    )
    await asyncio.sleep(0.01)

    changed = await curator2.curate_session("s1")

    assert changed is True
    got = await records.get(existing.id)
    assert got.text == "the user prefers dark mode"
    assert got.last_confirmed_at == before
    assert got.superseded_by is None
    assert len(llm.calls) == 1
    system_prompt = llm.calls[0]["messages"][0]["content"]
    assert '{"op": "NOOP"}' in system_prompt
    assert '"NOOP",      "id"' not in system_prompt
    await curator2.stop()
    await curator.stop()
    await records.close()


async def test_batch_source_precision_inference_preserves_timestamp_text():
    turns = [
        {**_turn(0, "user", "seconds"), "created_at": "2026-07-12T10:00:00Z"},
        {**_turn(1, "assistant", "milliseconds"), "created_at": "2026-07-12T10:00:00.123Z"},
        {**_turn(2, "user", "unsupported fraction"), "created_at": "2026-07-12T10:00:00.123456Z"},
    ]

    sources = Curator._batch_sources("s1", turns, MemoryScope("user"))

    assert [source.occurred_at for source in sources] == [turn["created_at"] for turn in turns]
    assert [source.time_precision for source in sources] == ["second", "millisecond", "unknown"]


# --- worthiness gate: narrative / dev-chatter is not minted -------------------


async def test_narrative_summary_kind_is_not_minted(tmp_path: Path):
    """An ADD the model tags as a narrative 'summary' (or any non-writable kind)
    is SKIPPED, not coerced into a record."""
    llm = StubLLM(
        _ops_json([{"op": "ADD", "text": "Recap: we debugged the curator and ran the tests", "kind": "summary"}])
    )
    sessions = StubSessions({"s1": [_turn(0, "user", "let's wrap up this session")]})
    curator, records = _make_curator(tmp_path, llm, sessions)

    await curator.curate_session("s1")

    assert await records.list() == []  # narrative recap dropped
    assert await curator._read_watermark("s1") == 0  # watermark still advances
    await curator.stop()
    await records.close()


async def test_curator_add_kinds_exclude_summary(tmp_path: Path):
    """The curator's writable ADD kinds are directive|fact|source|lesson — the
    prompt no longer offers 'summary'. `lesson` is the continual-learning playbook kind."""
    from ntrp.memory.curator import _SYSTEM_PROMPT, ALLOWED_KINDS

    assert {"directive", "fact", "source", "lesson"} == ALLOWED_KINDS
    assert "summary" not in ALLOWED_KINDS
    assert '"summary"' not in _SYSTEM_PROMPT
    assert "directive|fact|source|lesson" in _SYSTEM_PROMPT


async def test_legacy_note_action_kinds_are_dropped(tmp_path: Path):
    """Legacy 'note'/'action' kinds used to coerce to 'summary'; they now map to
    no writable kind, so the ADD is skipped."""
    llm = StubLLM(
        _ops_json(
            [
                {"op": "ADD", "text": "note to self about the refactor", "kind": "note"},
                {"op": "ADD", "text": "the user prefers tabs over spaces", "kind": "fact"},
            ]
        )
    )
    sessions = StubSessions({"s1": [_turn(0, "user", "some chatter")]})
    curator, records = _make_curator(tmp_path, llm, sessions)

    await curator.curate_session("s1")

    rows = await records.list()
    assert [r.text for r in rows] == ["the user prefers tabs over spaces"]  # only the fact landed
    await curator.stop()
    await records.close()


# --- labels: write-time membership ---------------------------------------------


async def test_add_op_sets_labels(tmp_path: Path):
    llm = StubLLM(
        _ops_json([{"op": "ADD", "text": "the user uses Linux", "kind": "fact", "labels": ["Linux", "tools"]}])
    )
    sessions = StubSessions({"s1": [_turn(0, "user", "I run Linux")]})
    curator, records = _make_curator(tmp_path, llm, sessions)

    await curator.curate_session("s1")

    rows = await records.list()
    assert await records.labels_of(rows[0].id) == ["Linux", "tools"]
    await curator.stop()
    await records.close()


async def test_add_op_labels_sanitized_and_capped(tmp_path: Path):
    llm = StubLLM(
        _ops_json(
            [
                {
                    "op": "ADD",
                    "text": "the user uses Linux",
                    "labels": ["Linux", " Linux ", "", 7, "a", "b", "c"],
                }
            ]
        )
    )
    sessions = StubSessions({"s1": [_turn(0, "user", "I run Linux")]})
    curator, records = _make_curator(tmp_path, llm, sessions)

    await curator.curate_session("s1")

    rows = await records.list()
    # Stripped + deduped + non-strings dropped, capped at 4.
    assert await records.labels_of(rows[0].id) == ["Linux", "a", "b", "c"]
    await curator.stop()
    await records.close()


async def test_update_op_replaces_labels_when_provided(tmp_path: Path):
    curator, records = _make_curator(tmp_path, StubLLM(), StubSessions())
    existing = await records.add("the user lives in Berlin")
    await records.set_labels(existing.id, ["Berlin", "places"])

    llm = StubLLM(
        _ops_json([{"op": "UPDATE", "id": existing.id, "text": "the user lives in Munich", "labels": ["Munich"]}])
    )
    sessions = StubSessions({"s1": [_turn(0, "user", "I moved to Munich")]})
    curator2 = Curator(
        llm,
        sessions,
        model="memory-model",
        db_path=tmp_path / "memory.db",
        record_store=records,
    )

    await curator2.curate_session("s1")

    active = (await records.list())[0]
    assert await records.labels_of(active.id) == ["Munich"]
    await curator2.stop()
    await curator.stop()
    await records.close()


async def test_update_op_keeps_labels_when_absent(tmp_path: Path):
    curator, records = _make_curator(tmp_path, StubLLM(), StubSessions())
    existing = await records.add("the user lives in Berlin")
    await records.set_labels(existing.id, ["Berlin", "places"])

    llm = StubLLM(_ops_json([{"op": "UPDATE", "id": existing.id, "text": "the user lives in Munich"}]))
    sessions = StubSessions({"s1": [_turn(0, "user", "I moved to Munich")]})
    curator2 = Curator(
        llm,
        sessions,
        model="memory-model",
        db_path=tmp_path / "memory.db",
        record_store=records,
    )

    await curator2.curate_session("s1")

    active = (await records.list())[0]
    assert await records.labels_of(active.id) == ["Berlin", "places"]
    await curator2.stop()
    await curator.stop()
    await records.close()


async def test_supersede_op_successor_takes_op_labels(tmp_path: Path):
    curator, records = _make_curator(tmp_path, StubLLM(), StubSessions())
    old = await records.add("the user works at Acme")
    await records.set_labels(old.id, ["Acme", "work"])

    llm = StubLLM(
        _ops_json([{"op": "SUPERSEDE", "id": old.id, "text": "the user works at Globex", "labels": ["Globex", "work"]}])
    )
    sessions = StubSessions({"s1": [_turn(0, "user", "I switched jobs to Globex")]})
    curator2 = Curator(
        llm,
        sessions,
        model="memory-model",
        db_path=tmp_path / "memory.db",
        record_store=records,
    )

    await curator2.curate_session("s1")

    active = await records.list()
    assert await records.labels_of(active[0].id) == ["Globex", "work"]
    assert await records.labels_of(old.id) == ["Acme", "work"]  # history keeps its labels
    await curator2.stop()
    await curator.stop()
    await records.close()


async def test_supersede_op_successor_inherits_labels_when_absent(tmp_path: Path):
    curator, records = _make_curator(tmp_path, StubLLM(), StubSessions())
    old = await records.add("the user works at Acme")
    await records.set_labels(old.id, ["Acme", "work"])

    llm = StubLLM(_ops_json([{"op": "SUPERSEDE", "id": old.id, "text": "the user works at Globex"}]))
    sessions = StubSessions({"s1": [_turn(0, "user", "I switched jobs to Globex")]})
    curator2 = Curator(
        llm,
        sessions,
        model="memory-model",
        db_path=tmp_path / "memory.db",
        record_store=records,
    )

    await curator2.curate_session("s1")

    active = await records.list()
    assert await records.labels_of(active[0].id) == ["Acme", "work"]
    await curator2.stop()
    await curator.stop()
    await records.close()


async def test_prompt_carries_label_vocabulary_and_recalled_labels(tmp_path: Path):
    curator, records = _make_curator(tmp_path, StubLLM(), StubSessions())
    recalled = await records.add("the user runs Linux on the desktop")
    await records.set_labels(recalled.id, ["Linux"])
    other = await records.add("venlafaxine dose changed in May")
    await records.set_labels(other.id, ["venlafaxine", "health"])

    llm = StubLLM(_ops_json([]))
    sessions = StubSessions({"s1": [_turn(0, "user", "I run Linux on my desktop")]})
    curator2 = Curator(
        llm,
        sessions,
        model="memory-model",
        db_path=tmp_path / "memory.db",
        record_store=records,
    )

    await curator2.curate_session("s1")

    user_prompt = llm.calls[0]["messages"][1]["content"]
    assert "LABEL VOCABULARY" in user_prompt
    # Top-by-count vocabulary names, not just the recalled record's.
    assert "venlafaxine" in user_prompt
    assert "health" in user_prompt
    # The recalled record line shows its labels inline (UPDATE replace needs them).
    assert f"- {recalled.id} [Linux]:" in user_prompt
    await curator2.stop()
    await curator.stop()
    await records.close()


# --- sweep --------------------------------------------------------------------


async def test_sweep_schedules_curation_for_recent_user_chats(tmp_path: Path):
    """The sweep schedules curation for recent USER CHATS by id (no scope)."""
    llm = StubLLM()
    sessions = StubSessions(
        scopes=[
            _scope("chat1"),
            _scope("chat3"),
        ]
    )
    curator, records = _make_curator(tmp_path, llm, sessions)

    recorded: list[str] = []
    curator.schedule_curation = lambda sid: recorded.append(sid)  # type: ignore[method-assign]

    scheduled = await curator.sweep_once()

    assert scheduled == 2
    assert recorded == ["chat1", "chat3"]
    await curator.stop()
    await records.close()


async def test_sweep_skips_automation_and_agent_sessions(tmp_path: Path):
    """Automation channels (origin_automation_id), channel sessions, and spawned
    agent sessions never enter curation — operational transcripts stay out of
    memory entirely."""
    llm = StubLLM()
    sessions = StubSessions(
        scopes=[
            _scope("chat1"),
            _scope("auto1", session_type="channel", origin_automation_id="task_1"),
            _scope("agent1", session_type="agent"),
            _scope("odd1", origin_automation_id="task_2"),  # automation-origin even as 'chat'
            _scope("chat2"),
        ]
    )
    curator, records = _make_curator(tmp_path, llm, sessions)

    recorded: list[str] = []
    curator.schedule_curation = lambda sid: recorded.append(sid)  # type: ignore[method-assign]

    scheduled = await curator.sweep_once()

    assert scheduled == 2
    assert recorded == ["chat1", "chat2"]
    await curator.stop()
    await records.close()


async def test_sweep_curates_only_sessions_with_new_turns(tmp_path: Path):
    """End-to-end: the new-turns session gets curated (one LLM call); the session
    already at its watermark no-ops (no LLM call)."""
    llm = StubLLM(_ops_json([{"op": "ADD", "text": "the user prefers tea"}]))
    sessions = StubSessions(
        rows={
            "fresh": [_turn(0, "user", "I prefer tea")],
            "stale": [_turn(0, "user", "already curated long ago")],
        },
        scopes=[
            _scope("fresh"),
            _scope("stale"),
        ],
    )
    curator, records = _make_curator(tmp_path, llm, sessions)
    await curator._write_watermark("stale", 0)  # stale is fully up to date

    await curator.sweep_once()
    await asyncio.gather(*curator._tasks.values(), return_exceptions=True)

    assert len(llm.calls) == 1  # only the fresh session spent an LLM call
    assert [r.text for r in await records.list()] == ["the user prefers tea"]
    assert await curator._read_watermark("fresh") == 0
    await curator.stop()
    await records.close()


async def test_backfill_entity_labels_promotes_recurring_subject(tmp_path: Path):
    """Untagged records that share a named subject get entity-tagged, so the subject
    accumulates >=2 records and promotes to a topic page; pure self-facts stay untagged."""
    import json as _json

    from ntrp.memory.file_store import FilePageStore
    from ntrp.memory.models import SourceRef

    store = FilePageStore(tmp_path / "memory")
    await store.open()
    a = await store.add("You worked at Replika on post-training.", kind="fact", source_ref=SourceRef("user", ""))
    b = await store.add("Replika reached roughly 2M daily active users.", kind="fact", source_ref=SourceRef("user", ""))
    c = await store.add("Your birthday is January 24.", kind="fact", source_ref=SourceRef("user", ""))

    llm = StubLLM(_json.dumps({a.id: "Replika", b.id: "Replika", c.id: None}))
    curator = Curator(llm, StubSessions(), model="memory-model", db_path=tmp_path / "m.db", record_store=store)

    tagged = await curator.backfill_entity_labels()

    assert tagged == 2
    assert (tmp_path / "memory" / "topics" / "replika.md").exists(), "recurring subject promoted to a topic"
    assert "Replika" in {l["label"] for l in await store.list_labels()}
    await curator.stop()
    await store.close()
