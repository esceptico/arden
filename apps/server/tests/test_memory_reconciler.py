from pathlib import Path

import pytest

from ntrp.memory.file_store import FilePageStore
from ntrp.memory.models import Kind, Record, SourceRef
from ntrp.memory.reconciler import RecordOperation, validate_operations


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
async def test_noop_records_confirmation_but_ask_never_mutates(tmp_path: Path):
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
    confirmation = _source(ref="s1:m2", occurred_at="2026-07-12T10:01:00Z")

    assert store.plan_operations((RecordOperation.ask("Which tea?"),), confirmation) == {}
    assert store.apply_operations((RecordOperation.ask("Which tea?"),), confirmation) == revision

    store.apply_operations((RecordOperation.noop(record.id),), confirmation)

    confirmed = await store.get(record.id)
    assert store.canonical_revision != revision
    assert [source.ref for source in confirmed.sources] == ["s1:m1", "s1:m2"]
    await store.close()
