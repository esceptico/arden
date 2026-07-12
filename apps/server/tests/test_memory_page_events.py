from __future__ import annotations

import asyncio
import base64
import json
import os
import stat
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from ntrp.memory.artifacts import ArtifactMemoryStore
from ntrp.memory.file_store import FilePageStore
from ntrp.memory.models import Kind, SourceRef
from ntrp.memory.page_edit_service import (
    PageEditService,
    PreviewExpiredError,
    ReconciliationPendingError,
    StalePageRevisionError,
)
from ntrp.memory.page_events import (
    AppliedPageOperation,
    PageEditDecision,
    PageEditEvent,
    page_revision,
    parse_page_edit_events,
    render_page_edit_event,
    unified_patch,
)
from ntrp.memory.reconciler import RecordOperation


class Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


class Reconciler:
    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.analyses = []

    async def __call__(self, analysis):
        self.analyses.append(analysis)
        return self.answers.pop(0)


async def _store(vault: Path, *, notify=None) -> FilePageStore:
    (vault / "topics").mkdir(parents=True)
    (vault / "raw" / "topics").mkdir(parents=True)
    (vault / "topics" / "a.md").write_bytes(b"# A\n\nOriginal durable statement.\n")
    (vault / "raw" / "topics" / "a.md").write_text(
        "<!-- ntrp:records schema=2 page=topics/a.md -->\n",
        encoding="utf-8",
    )
    store = FilePageStore(vault, post_canonical_commit=notify)
    await store.open()
    return store


def test_revision_hashes_bytes_and_exact_unified_patch():
    base = b"# A\n\nOld.\n"
    result = b"# A\n\nNew.\n"

    assert page_revision(base) == "a578e665b596d40d6c3b173e5331f9ed9a5a891429ecd1ccb14b13185a4462eb"
    assert unified_patch(base, result) == (
        "--- a/page\n"
        "+++ b/page\n"
        "@@ -1,3 +1,3 @@\n"
        " # A\n"
        " \n"
        "-Old.\n"
        "+New.\n"
    )


def test_unified_patch_marks_both_files_without_final_newline():
    assert unified_patch(b"Old", b"New") == (
        "--- a/page\n"
        "+++ b/page\n"
        "@@ -1 +1 @@\n"
        "-Old\n"
        "\\ No newline at end of file\n"
        "+New\n"
        "\\ No newline at end of file\n"
    )


def test_exact_resource_read_cannot_escape_on_parent_swap(tmp_path: Path, monkeypatch):
    vault = tmp_path / "memory"
    (vault / "topics").mkdir(parents=True)
    (vault / "topics" / "a.md").write_bytes(b"SAFE")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.md").write_bytes(b"SECRET")
    resources = ArtifactMemoryStore(vault)
    original = resources._assert_parent_safe
    swapped = False

    def swap_after_check(path):
        nonlocal swapped
        original(path)
        if not swapped:
            swapped = True
            (vault / "topics").rename(vault / "topics-real")
            (vault / "topics").symlink_to(outside, target_is_directory=True)

    monkeypatch.setattr(resources, "_assert_parent_safe", swap_after_check)

    assert resources.read_resource_bytes("topics/a.md") != b"SECRET"


def test_event_markdown_round_trip_preserves_exact_fields():
    event = PageEditEvent(
        id="evt-1",
        occurred_at="2026-07-12T20:10:11.123+04:00",
        sequence=7,
        actor="user:desktop",
        origin="desktop",
        path="topics/a.md",
        base_revision="a" * 64,
        result_revision="b" * 64,
        patch="@@ -1 +1 @@\n-old\n+new\n",
        operations=(
            AppliedPageOperation(
                op="ADD",
                text="New",
                kind=Kind.FACT,
                scope={"kind": "user", "key": None},
                sources=(
                    SourceRef(
                        "page_edit",
                        "page_edit:evt-1",
                        captured_at="2026-07-12T20:10:11.123+04:00",
                        occurred_at="2026-07-12T20:10:11.123+04:00",
                        time_precision="millisecond",
                        role="user:desktop",
                    ),
                ),
            ),
        ),
        reconciliation="applied",
    )

    rendered = render_page_edit_event(event)

    assert parse_page_edit_events(rendered) == (event,)
    assert parse_page_edit_events("# Page edits\n\n" + rendered + rendered) == (event, event)


@pytest.mark.parametrize(
    "occurred_at",
    ["2026-07-12T20:10:11+04:00", "2026-07-12T20:10:11.123456+04:00", "2026-07-12T20:10:11.123"],
)
def test_event_requires_millisecond_timestamp_with_original_offset(occurred_at: str):
    with pytest.raises(ValidationError):
        PageEditEvent(
            id="evt-1",
            occurred_at=occurred_at,
            sequence=1,
            actor="user:desktop",
            origin="desktop",
            path="topics/a.md",
            base_revision="a" * 64,
            result_revision="b" * 64,
            patch="",
            operations=(),
            reconciliation="applied",
        )


@pytest.mark.asyncio
async def test_preview_is_non_mutating_and_passes_only_structural_changes(tmp_path: Path):
    vault = tmp_path / "memory"
    store = await _store(vault)
    page = vault / "topics" / "a.md"
    raw = vault / "raw" / "topics" / "a.md"
    before = {path: path.read_bytes() for path in (page, raw)}
    revision = store.canonical_revision
    reconciler = Reconciler((RecordOperation.noop(),))
    service = PageEditService(vault, store, reconciler=reconciler)
    candidate = b"# A\n\nOriginal durable statement.\n\nA second durable statement.\n"

    preview = await service.preview(
        path="topics/a.md",
        base_revision=page_revision(before[page]),
        content=candidate,
        actor="user:desktop",
    )

    assert preview.result_revision == page_revision(candidate)
    assert preview.patch == unified_patch(before[page], candidate)
    assert preview.operations == (RecordOperation.noop(),)
    assert {path: path.read_bytes() for path in (page, raw)} == before
    assert store.canonical_revision == revision
    assert reconciler.analyses[0].before == ("Original durable statement.",)
    assert reconciler.analyses[0].after == ("Original durable statement.", "A second durable statement.")
    assert reconciler.analyses[0].changed_before == ()
    assert reconciler.analyses[0].changed_after == ("A second durable statement.",)
    assert (vault / ".ntrp" / "maintenance" / "page-edit-previews" / f"{preview.id}.json").is_file()
    await store.close()


@pytest.mark.asyncio
async def test_analysis_omits_unchanged_blocks_outside_local_context(tmp_path: Path):
    vault = tmp_path / "memory"
    store = await _store(vault)
    page = vault / "topics" / "a.md"
    base = b"# A\n\nFar before.\n\nAdjacent before.\n\nOld statement.\n\nAdjacent after.\n\nFar after.\n"
    page.write_bytes(base)
    candidate = base.replace(b"Old statement.", b"New statement.")
    reconciler = Reconciler((RecordOperation.noop(),))
    service = PageEditService(vault, store, reconciler=reconciler)

    await service.preview(
        path="topics/a.md",
        base_revision=page_revision(base),
        content=candidate,
        actor="user:desktop",
    )

    analysis = reconciler.analyses[0]
    assert analysis.before == ("Adjacent before.", "Old statement.", "Adjacent after.")
    assert analysis.after == ("Adjacent before.", "New statement.", "Adjacent after.")
    assert analysis.changed_before == ("Old statement.",)
    assert analysis.changed_after == ("New statement.",)
    await store.close()


@pytest.mark.asyncio
async def test_frontmatter_is_not_sent_as_a_durable_statement(tmp_path: Path):
    vault = tmp_path / "memory"
    store = await _store(vault)
    page = vault / "topics" / "a.md"
    base = b"---\ntype: topic\ntitle: A\n---\n# A\n\nDurable.\n"
    page.write_bytes(base)
    candidate = base.replace(b"Durable.", b"Changed.")
    reconciler = Reconciler((RecordOperation.noop(),))
    service = PageEditService(vault, store, reconciler=reconciler)

    await service.preview(
        path="topics/a.md",
        base_revision=page_revision(base),
        content=candidate,
        actor="user:desktop",
    )

    assert reconciler.analyses[0].before == ("Durable.",)
    assert reconciler.analyses[0].after == ("Changed.",)
    await store.close()


@pytest.mark.asyncio
async def test_reconciler_exception_can_be_explicitly_saved_as_pending(tmp_path: Path):
    vault = tmp_path / "memory"
    store = await _store(vault)
    page = vault / "topics" / "a.md"
    base = page.read_bytes()

    async def unavailable(_analysis):
        raise OSError("model unavailable")

    service = PageEditService(vault, store, reconciler=unavailable)
    preview = await service.preview(
        path="topics/a.md",
        base_revision=page_revision(base),
        content=base + b"\nCandidate.\n",
        actor="user:desktop",
    )

    assert preview.analysis_pending is True
    event = await service.apply(preview.id, decisions={}, save_as_pending=True)
    assert event.reconciliation == "pending"
    await store.close()


@pytest.mark.asyncio
async def test_apply_commits_page_patch_event_and_operations_together(tmp_path: Path, monkeypatch):
    vault = tmp_path / "memory"
    notifications = []
    store = await _store(vault, notify=lambda: notifications.append("project"))
    notifications.clear()
    now = Clock(datetime(2026, 7, 12, 20, 10, 11, 123456, tzinfo=ZoneInfo("Asia/Yerevan")))
    service = PageEditService(
        vault,
        store,
        reconciler=Reconciler(
            (
                RecordOperation.add("A durable statement."),
                RecordOperation.add("Another durable statement."),
            )
        ),
        timezone="Asia/Yerevan",
        now=now,
    )
    page = vault / "topics" / "a.md"
    base = page.read_bytes()
    candidate = base + b"\nA durable statement.\n\nAnother durable statement.\n"
    preview = await service.preview(
        path="topics/a.md",
        base_revision=page_revision(base),
        content=candidate,
        actor="user:desktop",
    )
    commits = []
    commit = store._journal.commit

    def capture(files, **kwargs):
        commits.append(dict(files))
        return commit(files, **kwargs)

    monkeypatch.setattr(store._journal, "commit", capture)

    event = await service.apply(preview.id, decisions={})

    assert len(commits) == 1
    assert Path("topics/a.md") in commits[0]
    assert Path("raw/events/2026-07-12.md") in commits[0]
    assert Path("me.md") in commits[0]
    assert Path("raw/me.md") in commits[0]
    assert notifications == ["project"]
    assert page.read_bytes() == candidate
    assert event.occurred_at == "2026-07-12T20:10:11.123+04:00"
    assert event.sequence == 1
    assert event.actor == "user:desktop"
    assert event.patch == unified_patch(base, page.read_bytes())
    assert event.base_revision == page_revision(base)
    assert event.result_revision == page_revision(page.read_bytes())
    assert [operation.text for operation in event.operations] == [
        "A durable statement.",
        "Another durable statement.",
    ]
    assert event.operations[0].sources[0].ref == f"page_edit:{event.id}"
    assert parse_page_edit_events((vault / "raw" / "events" / "2026-07-12.md").read_text())[0] == event
    assert not (vault / ".ntrp" / "maintenance" / "page-edit-previews" / f"{preview.id}.json").exists()
    await store.close()


@pytest.mark.asyncio
async def test_apply_is_idempotent_after_commit_before_preview_cleanup(tmp_path: Path, monkeypatch):
    vault = tmp_path / "memory"
    store = await _store(vault)
    service = PageEditService(vault, store, reconciler=Reconciler((RecordOperation.noop(),)))
    page = vault / "topics" / "a.md"
    base = page.read_bytes()
    preview = await service.preview(
        path="topics/a.md",
        base_revision=page_revision(base),
        content=base,
        actor="user:desktop",
    )
    delete = service._resources.delete_page_edit_preview

    def fail_cleanup(_rel):
        raise OSError("simulated response-loss boundary")

    monkeypatch.setattr(service._resources, "delete_page_edit_preview", fail_cleanup)
    with pytest.raises(OSError, match="response-loss"):
        await service.apply(preview.id, decisions={})
    monkeypatch.setattr(service._resources, "delete_page_edit_preview", delete)

    retried = await service.apply(preview.id, decisions={})

    assert retried.id == preview.id
    assert retried.sequence == 1
    assert len(service.history(path="topics/a.md")) == 1
    assert await service.apply(preview.id, decisions={}) == retried
    await store.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["id", "candidate", "patch", "analysis"])
async def test_apply_rejects_tampered_persisted_preview_before_commit(tmp_path: Path, monkeypatch, tamper: str):
    vault = tmp_path / "memory"
    store = await _store(vault)
    service = PageEditService(vault, store, reconciler=Reconciler((RecordOperation.noop(),)))
    page = vault / "topics" / "a.md"
    base = page.read_bytes()
    preview = await service.preview(
        path="topics/a.md",
        base_revision=page_revision(base),
        content=base + b"\nCandidate.\n",
        actor="user:desktop",
    )
    persisted = vault / ".ntrp" / "maintenance" / "page-edit-previews" / f"{preview.id}.json"
    payload = json.loads(persisted.read_text(encoding="utf-8"))
    if tamper == "id":
        payload["preview"]["id"] = "other-id"
    elif tamper == "candidate":
        payload["content"] = base64.b64encode(base + b"\nTampered.\n").decode("ascii")
    elif tamper == "patch":
        payload["preview"]["patch"] = "forged patch"
    else:
        payload["analysis"]["path"] = "topics/other.md"
    persisted.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(store._journal, "commit", lambda *args, **kwargs: pytest.fail("tampered preview committed"))

    with pytest.raises(ValueError, match="persisted preview"):
        await service.apply(preview.id, decisions={})

    assert page.read_bytes() == base
    assert service.history() == ()
    await store.close()


@pytest.mark.asyncio
async def test_same_millisecond_uses_sequence_as_history_tie_breaker(tmp_path: Path):
    vault = tmp_path / "memory"
    store = await _store(vault)
    clock = Clock(datetime(2026, 7, 12, 20, 10, 11, 123000, tzinfo=timezone(timedelta(hours=4))))
    service = PageEditService(vault, store, reconciler=Reconciler((RecordOperation.noop(),), (RecordOperation.noop(),)), now=clock)
    page = vault / "topics" / "a.md"

    base = page.read_bytes()
    first = await service.preview(path="topics/a.md", base_revision=page_revision(base), content=base + b"\nFirst.\n", actor="agent:x")
    event1 = await service.apply(first.id, decisions={})
    base = page.read_bytes()
    second = await service.preview(path="topics/a.md", base_revision=page_revision(base), content=base + b"\nSecond.\n", actor="agent:x")
    event2 = await service.apply(second.id, decisions={})

    assert event1.occurred_at == event2.occurred_at
    assert [event.sequence for event in service.history(path="topics/a.md")] == [1, 2]
    await store.close()


@pytest.mark.asyncio
async def test_concurrent_applies_cannot_overwrite_page_or_event(tmp_path: Path, monkeypatch):
    vault = tmp_path / "memory"
    store = await _store(vault)
    page = vault / "topics" / "a.md"
    base = page.read_bytes()
    first_service = PageEditService(vault, store, reconciler=Reconciler((RecordOperation.noop(),)))
    second_service = PageEditService(vault, store, reconciler=Reconciler((RecordOperation.noop(),)))
    first = await first_service.preview(
        path="topics/a.md",
        base_revision=page_revision(base),
        content=base + b"\nFirst.\n",
        actor="user:desktop",
    )
    second = await second_service.preview(
        path="topics/a.md",
        base_revision=page_revision(base),
        content=base + b"\nSecond.\n",
        actor="user:desktop",
    )
    real_list = store.list

    async def yielding_list(*args, **kwargs):
        await asyncio.sleep(0)
        return await real_list(*args, **kwargs)

    monkeypatch.setattr(store, "list", yielding_list)

    results = await asyncio.gather(
        first_service.apply(first.id, decisions={}),
        second_service.apply(second.id, decisions={}),
        return_exceptions=True,
    )

    assert sum(isinstance(result, PageEditEvent) for result in results) == 1
    assert sum(isinstance(result, StalePageRevisionError) for result in results) == 1
    assert len(first_service.history(path="topics/a.md")) == 1
    await store.close()


@pytest.mark.asyncio
async def test_ask_requires_explicit_note_only_or_forget_decision(tmp_path: Path):
    vault = tmp_path / "memory"
    store = await _store(vault)
    store.apply_operations(
        (RecordOperation.add("User drinks coffee"),),
        SourceRef("test", "seed", occurred_at="2026-07-12T10:00:00.000+04:00", time_precision="millisecond"),
    )
    record = (await store.list())[0]
    service = PageEditService(
        vault,
        store,
        reconciler=Reconciler((RecordOperation.ask("Forget the coffee memory?", record.id),)),
    )
    page = vault / "topics" / "a.md"
    base = page.read_bytes()
    preview = await service.preview(path="topics/a.md", base_revision=page_revision(base), content=base + b"\nNo coffee.\n", actor="user:desktop")

    with pytest.raises(ValueError, match="decision"):
        await service.apply(preview.id, decisions={})

    event = await service.apply(
        preview.id,
        decisions={
            preview.questions[0].id: PageEditDecision(choice="Forget memory", target_ids=(record.id,)),
        },
    )

    assert event.operations[0].op == "RETRACT"
    assert await store.get(record.id) is None
    await store.close()


@pytest.mark.asyncio
async def test_note_only_resolves_ask_without_semantic_mutation(tmp_path: Path):
    vault = tmp_path / "memory"
    store = await _store(vault)
    store.apply_operations(
        (RecordOperation.add("User drinks coffee"),),
        SourceRef("test", "seed", occurred_at="2026-07-12T10:00:00.000+04:00", time_precision="millisecond"),
    )
    record = (await store.list())[0]
    service = PageEditService(vault, store, reconciler=Reconciler((RecordOperation.ask("Forget it?", record.id),)))
    page = vault / "topics" / "a.md"
    base = page.read_bytes()
    preview = await service.preview(path="topics/a.md", base_revision=page_revision(base), content=base + b"\nPage note only.\n", actor="user:desktop")

    event = await service.apply(preview.id, decisions={preview.questions[0].id: "Note only"})

    assert event.operations[0].op == "NOOP"
    assert (await store.get(record.id)).text == "User drinks coffee"
    await store.close()


@pytest.mark.asyncio
async def test_apply_rejects_stale_page_revision_without_committing(tmp_path: Path, monkeypatch):
    vault = tmp_path / "memory"
    store = await _store(vault)
    service = PageEditService(vault, store, reconciler=Reconciler((RecordOperation.noop(),)))
    page = vault / "topics" / "a.md"
    base = page.read_bytes()
    preview = await service.preview(path="topics/a.md", base_revision=page_revision(base), content=base + b"\nCandidate.\n", actor="user:desktop")
    page.write_bytes(base + b"\nExternal edit.\n")
    monkeypatch.setattr(store._journal, "commit", lambda files: pytest.fail("stale edit committed"))

    with pytest.raises(StalePageRevisionError):
        await service.apply(preview.id, decisions={})

    assert page.read_bytes() == base + b"\nExternal edit.\n"
    await store.close()


@pytest.mark.asyncio
async def test_apply_cas_rejects_page_change_after_revision_check(tmp_path: Path, monkeypatch):
    vault = tmp_path / "memory"
    store = await _store(vault)
    service = PageEditService(vault, store, reconciler=Reconciler((RecordOperation.noop(),)))
    page = vault / "topics" / "a.md"
    base = page.read_bytes()
    preview = await service.preview(
        path="topics/a.md",
        base_revision=page_revision(base),
        content=base + b"\nCandidate.\n",
        actor="user:desktop",
    )
    prepare = store._journal.prepare

    def prepare_then_edit(files):
        prepared = prepare(files)
        page.write_bytes(base + b"\nExternal edit.\n")
        return prepared

    monkeypatch.setattr(store._journal, "prepare", prepare_then_edit)

    with pytest.raises(ValueError, match="expected state changed"):
        await service.apply(preview.id, decisions={})

    assert page.read_bytes() == base + b"\nExternal edit.\n"
    assert service.history() == ()
    await store.close()


@pytest.mark.asyncio
async def test_analysis_unavailable_can_save_pending_and_retry_exact_patch_once(tmp_path: Path):
    vault = tmp_path / "memory"
    store = await _store(vault)
    unavailable = Reconciler(None)
    service = PageEditService(vault, store, reconciler=unavailable)
    page = vault / "topics" / "a.md"
    base = page.read_bytes()
    candidate = base + b"\nOriginal candidate statement.\n"
    preview = await service.preview(path="topics/a.md", base_revision=page_revision(base), content=candidate, actor="user:desktop")

    with pytest.raises(ReconciliationPendingError):
        await service.apply(preview.id, decisions={})
    pending = await service.apply(preview.id, decisions={}, save_as_pending=True)
    assert pending.reconciliation == "pending"
    assert pending.operations == ()

    newer = candidate + b"\nNewer unrelated edit.\n"
    page.write_bytes(newer)
    reconciler = Reconciler((RecordOperation.add("Original candidate statement."),))
    restarted = PageEditService(vault, store, reconciler=reconciler)

    resolved = await restarted.retry(pending.id)

    assert resolved.reconciliation == "applied"
    assert resolved.reconciles_event_id == pending.id
    assert resolved.patch == pending.patch
    assert page.read_bytes() == newer
    assert reconciler.analyses[0].after == ("Original durable statement.", "Original candidate statement.")
    assert resolved.operations[0].sources[0].ref == f"page_edit:{pending.id}"
    assert await restarted.retry(pending.id) == resolved
    await store.close()


@pytest.mark.asyncio
async def test_pending_ask_is_persisted_and_resolved_after_restart_without_reanalysis(tmp_path: Path):
    vault = tmp_path / "memory"
    store = await _store(vault)
    store.apply_operations(
        (RecordOperation.add("User drinks coffee"),),
        SourceRef("test", "seed", occurred_at="2026-07-12T10:00:00.000+04:00", time_precision="millisecond"),
    )
    record = (await store.list())[0]
    page = vault / "topics" / "a.md"
    base = page.read_bytes()
    unavailable = PageEditService(vault, store, reconciler=Reconciler(None))
    preview = await unavailable.preview(
        path="topics/a.md",
        base_revision=page_revision(base),
        content=base + b"\nNo coffee.\n",
        actor="user:desktop",
    )
    pending = await unavailable.apply(preview.id, decisions={}, save_as_pending=True)
    first_reconciler = Reconciler((RecordOperation.ask("Forget the coffee memory?", record.id),))
    first_retry = PageEditService(vault, store, reconciler=first_reconciler)

    review = await first_retry.retry(pending.id)

    assert review.reconciliation == "needs_review"
    assert review.review_operations == (RecordOperation.ask("Forget the coffee memory?", record.id),)
    assert review.questions[0].question == "Forget the coffee memory?"
    assert len(first_reconciler.analyses) == 1
    calls = 0

    async def must_not_reconcile(_analysis):
        nonlocal calls
        calls += 1
        raise AssertionError("persisted review was reanalyzed")

    restarted = PageEditService(vault, store, reconciler=must_not_reconcile)
    with pytest.raises(ValueError, match="invalid decision id"):
        await restarted.retry(
            pending.id,
            decisions={"unrelated": PageEditDecision(choice="Note only")},
        )
    applied = await restarted.retry(
        pending.id,
        decisions={review.questions[0].id: PageEditDecision(choice="Forget memory", target_ids=(record.id,))},
    )

    assert calls == 0
    assert applied.reconciliation == "applied"
    assert applied.review_event_id == review.id
    assert applied.operations[0].op == "RETRACT"
    assert await store.get(record.id) is None
    assert await restarted.retry(pending.id) == applied
    await store.close()


@pytest.mark.asyncio
async def test_pending_retry_cas_rejects_canonical_change_during_analysis(tmp_path: Path, monkeypatch):
    vault = tmp_path / "memory"
    store = await _store(vault)
    page = vault / "topics" / "a.md"
    base = page.read_bytes()
    unavailable = PageEditService(vault, store, reconciler=Reconciler(None))
    preview = await unavailable.preview(
        path="topics/a.md",
        base_revision=page_revision(base),
        content=base + b"\nCandidate.\n",
        actor="user:desktop",
    )
    pending = await unavailable.apply(preview.id, decisions={}, save_as_pending=True)
    retry = PageEditService(vault, store, reconciler=Reconciler((RecordOperation.add("Candidate."),)))
    list_records = store.list
    changed = False

    async def list_then_external_commit(*args, **kwargs):
        nonlocal changed
        records = await list_records(*args, **kwargs)
        if not changed:
            changed = True
            store._journal.commit({Path("external.md"): b"external\n"})
        return records

    monkeypatch.setattr(store, "list", list_then_external_commit)

    with pytest.raises(ValueError, match="expected state changed"):
        await retry.retry(pending.id)

    assert (vault / "external.md").read_bytes() == b"external\n"
    assert not any(event.reconciliation == "applied" and event.reconciles_event_id == pending.id for event in retry.history())
    await store.close()


def test_preview_creation_fsyncs_parent_directory(tmp_path: Path, monkeypatch):
    vault = tmp_path / "memory"
    (vault / ".ntrp" / "maintenance" / "page-edit-previews").mkdir(parents=True)
    resources = ArtifactMemoryStore(vault)
    synced_directory = False
    fsync = os.fsync

    def capture(descriptor: int) -> None:
        nonlocal synced_directory
        synced_directory = synced_directory or stat.S_ISDIR(os.fstat(descriptor).st_mode)
        fsync(descriptor)

    monkeypatch.setattr(os, "fsync", capture)

    resources.write_page_edit_preview(
        ".ntrp/maintenance/page-edit-previews/test.json",
        b"{}\n",
    )

    assert synced_directory is True


@pytest.mark.asyncio
async def test_restart_uses_persisted_preview_and_expiry(tmp_path: Path):
    vault = tmp_path / "memory"
    store = await _store(vault)
    clock = Clock(datetime(2026, 7, 12, 10, 0, tzinfo=UTC))
    service = PageEditService(vault, store, reconciler=Reconciler((RecordOperation.noop(),)), now=clock, preview_ttl=timedelta(minutes=5))
    page = vault / "topics" / "a.md"
    base = page.read_bytes()
    preview = await service.preview(path="topics/a.md", base_revision=page_revision(base), content=base + b"\nCandidate.\n", actor="user:desktop")
    clock.value += timedelta(minutes=6)
    restarted = PageEditService(vault, store, reconciler=Reconciler(), now=clock, preview_ttl=timedelta(days=1))

    with pytest.raises(PreviewExpiredError):
        await restarted.apply(preview.id, decisions={})

    assert page.read_bytes() == base
    assert not (vault / ".ntrp" / "maintenance" / "page-edit-previews" / f"{preview.id}.json").exists()
    await store.close()
