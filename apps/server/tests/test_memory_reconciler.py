from pathlib import Path

import pytest

from ntrp.memory.file_store import FilePageStore
from ntrp.memory.models import Kind, Record, SourceRef
from ntrp.memory.reconciler import RecordOperation, validate_operations
from ntrp.memory.scopes import MemoryScope


def _source(**changes) -> SourceRef:
    values = {
        "kind": "chat_message",
        "ref": "s1:m1",
        "captured_at": "2026-07-12T10:00:01Z",
        "occurred_at": "2026-07-12T10:00:00Z",
        "time_precision": "second",
        "role": "user",
    }
    values.update(changes)
    return SourceRef(**values)


def _fact(record_id: str, text: str, *, scope_kind: str = "user", scope_key: str | None = None) -> Record:
    return Record(id=record_id, text=text, kind=Kind.FACT, scope_kind=scope_kind, scope_key=scope_key)


def test_contradiction_is_not_substring_deduplicated():
    operations = validate_operations(
        [RecordOperation.add("User does not drink coffee", kind=Kind.FACT)],
        records=[_fact("coffee", "User drinks coffee")],
        source=_source(),
    )

    assert operations[0].op == "ADD"


def test_invalid_target_rejects_the_entire_operation_batch():
    operations = [
        RecordOperation.add("User drinks tea", kind=Kind.FACT),
        RecordOperation.supersede("missing", "User drinks coffee", kind=Kind.FACT),
    ]

    with pytest.raises(ValueError, match="missing target"):
        validate_operations(operations, records=[], source=_source())


def test_evidence_is_required_unless_source_is_explicitly_unknown():
    operation = RecordOperation.add("User drinks tea", kind=Kind.FACT)

    with pytest.raises(ValueError, match="evidence"):
        validate_operations([operation], records=[], source=_source(ref=""))

    assert validate_operations(
        [operation],
        records=[],
        source=SourceRef("source", "unknown", captured_at=None),
    ) == (operation,)


def test_source_timestamp_precision_must_match_timestamp():
    operation = RecordOperation.add("User drinks tea", kind=Kind.FACT)

    with pytest.raises(ValueError, match="precision"):
        validate_operations(
            [operation],
            records=[],
            source=_source(occurred_at="2026-07-12T10:00:00Z", time_precision="day"),
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"text": "not allowed"},
        {"kind": Kind.FACT},
        {"scope": MemoryScope("user")},
        {"meta_labels": ("label",)},
        {"entity_labels": ("entity",)},
    ],
)
def test_ask_rejects_every_non_question_field(changes: dict):
    operation = RecordOperation(op="ASK", question="Which tea?", **changes)

    with pytest.raises(ValueError, match="ASK requires only a question and optional target ids"):
        validate_operations([operation], records=[_fact("record", "Fact")], source=_source())


def test_ask_allows_question_only_or_validated_target_ids():
    record = _fact("record", "User drinks tea")

    question_only = RecordOperation.ask("Which tea?")
    assert validate_operations([question_only], records=[record], source=_source()) == (question_only,)

    operation = RecordOperation(op="ASK", question="Forget tea?", target_ids=(record.id,))
    assert validate_operations([operation], records=[record], source=_source()) == (operation,)
    with pytest.raises(ValueError, match="missing target"):
        validate_operations(
            [RecordOperation(op="ASK", question="Forget tea?", target_ids=("missing",))],
            records=[record],
            source=_source(),
        )


@pytest.mark.parametrize(
    "changes",
    [
        {"question": "not allowed"},
        {"text": "not allowed"},
        {"kind": Kind.FACT},
        {"scope": MemoryScope("user")},
        {"target_ids": ("record",)},
        {"meta_labels": ("label",)},
        {"entity_labels": ("entity",)},
    ],
)
def test_noop_rejects_all_payload_fields(changes: dict):
    operation = RecordOperation(op="NOOP", **changes)

    with pytest.raises(ValueError, match="NOOP cannot carry payload fields"):
        validate_operations([operation], records=[_fact("record", "Fact")], source=_source())


def test_noop_without_payload_is_valid():
    operation = RecordOperation(op="NOOP")

    assert validate_operations([operation], records=[], source=_source()) == (operation,)


def test_unsupported_runtime_precision_is_rejected():
    operation = RecordOperation.add("User drinks tea")
    unsupported = _source(time_precision="microsecond")

    with pytest.raises(ValueError, match="invalid source time_precision"):
        validate_operations([operation], records=[], source=unsupported)


def test_millisecond_precision_requires_exactly_three_fractional_digits():
    operation = RecordOperation.add("User drinks tea")

    assert validate_operations(
        [operation],
        records=[],
        source=_source(occurred_at="2026-07-12T10:00:00.123Z", time_precision="millisecond"),
    ) == (operation,)
    with pytest.raises(ValueError, match="millisecond precision"):
        validate_operations(
            [operation],
            records=[],
            source=_source(occurred_at="2026-07-12T10:00:00.123456Z", time_precision="millisecond"),
        )


@pytest.mark.asyncio
async def test_plan_is_read_only_and_apply_commits_the_whole_batch_once(tmp_path: Path, monkeypatch):
    vault = tmp_path / "vault"
    visible = vault / "me.md"
    raw = vault / "raw" / "me.md"
    visible.parent.mkdir(parents=True)
    raw.parent.mkdir(parents=True)
    visible.write_text("# Me\n", encoding="utf-8")
    raw.write_text("<!-- ntrp:records schema=2 page=me.md -->\n", encoding="utf-8")
    store = FilePageStore(vault)
    await store.open()
    before = {path: path.read_bytes() for path in (visible, raw)}
    operations = (
        RecordOperation.add("User drinks tea", kind=Kind.FACT),
        RecordOperation.add("User likes ceramic mugs", kind=Kind.FACT),
    )
    commits: list[dict[Path, bytes]] = []
    commit = store._journal.commit

    def capture(files):
        commits.append(dict(files))
        return commit(files)

    monkeypatch.setattr(store._journal, "commit", capture)

    plan = store.plan_operations(operations, _source())

    assert {path: path.read_bytes() for path in (visible, raw)} == before
    assert set(plan) == {Path("me.md"), Path("raw/me.md")}

    revision = store.apply_operations(operations, _source())

    assert revision == store.canonical_revision
    assert len(commits) == 1
    assert {record.text for record in await store.list()} == {
        "User drinks tea",
        "User likes ceramic mugs",
    }
    await store.close()


@pytest.mark.asyncio
async def test_noop_and_ask_never_mutate(tmp_path: Path):
    vault = tmp_path / "vault"
    (vault / "raw").mkdir(parents=True)
    (vault / "me.md").write_text("# Me\n", encoding="utf-8")
    (vault / "raw" / "me.md").write_text(
        "<!-- ntrp:records schema=2 page=me.md -->\n",
        encoding="utf-8",
    )
    store = FilePageStore(vault)
    await store.open()
    store.apply_operations((RecordOperation.add("User drinks tea"),), _source())
    record = (await store.list())[0]
    revision = store.canonical_revision
    source = _source(ref="s1:m2", occurred_at="2026-07-12T10:01:00Z")

    assert store.plan_operations((RecordOperation.ask("Which tea?"),), source) == {}
    assert store.apply_operations((RecordOperation.ask("Which tea?"),), source) == revision
    assert store.plan_operations((RecordOperation(op="NOOP"),), source) == {}
    assert store.apply_operations((RecordOperation(op="NOOP"),), source) == revision

    unchanged = await store.get(record.id)
    assert store.canonical_revision == revision
    assert [evidence.ref for evidence in unchanged.sources] == ["s1:m1"]
    await store.close()
