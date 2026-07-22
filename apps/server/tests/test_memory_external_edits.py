from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from ntrp.events.sse import MemoryChangedEvent
from ntrp.memory.artifacts import ArtifactMemoryStore
from ntrp.memory.file_store import FilePageStore, ObservedFileChange
from ntrp.memory.models import SourceRef
from ntrp.memory.page_edit_service import PageEditService, StalePageRevisionError
from ntrp.memory.page_events import page_revision, unified_patch
from ntrp.memory.reconciler import RecordOperation
from ntrp.server.runtime.knowledge import KnowledgeRuntime

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.asyncio


class Reconciler:
    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.calls = 0

    async def __call__(self, _analysis):
        self.calls += 1
        return self.answers.pop(0)


async def _store(vault: Path) -> tuple[FilePageStore, Path]:
    page = vault / "topics" / "a.md"
    page.parent.mkdir(parents=True)
    page.write_bytes(b"# A\n\nOriginal.\n")
    raw = vault / "raw" / "topics" / "a.md"
    raw.parent.mkdir(parents=True)
    raw.write_text("<!-- ntrp:records schema=2 page=topics/a.md -->\n", encoding="utf-8")
    store = FilePageStore(vault)
    await store.open()
    return store, page


def _observed(vault: Path) -> dict:
    return json.loads((vault / ".ntrp" / "maintenance" / "observed-pages.json").read_text())


async def test_external_edit_has_exact_durable_base_and_advances_only_after_event(tmp_path: Path):
    vault = tmp_path / "memory"
    store, page = await _store(vault)
    before = page.read_bytes()
    after = b"# A\n\nChanged in Obsidian.\n"
    page.write_bytes(after)

    changes = await store.refresh_from_disk()

    assert [change.path for change in changes] == ["topics/a.md"]
    change = changes[0]
    assert isinstance(change, ObservedFileChange)
    assert change.before == before
    assert change.after == after
    assert change.base_revision == page_revision(before)
    assert change.result_revision == page_revision(after)
    assert _observed(vault)["pages"]["topics/a.md"] == page_revision(before)
    assert (vault / ".ntrp" / "maintenance" / "observed-page-bases" / page_revision(before)).read_bytes() == before

    service = PageEditService(vault, store, reconciler=Reconciler((RecordOperation.noop(),)))
    event = await service.ingest_external(change)

    assert event is not None
    assert event.origin == "external"
    assert event.patch == unified_patch(before, after)
    assert event.reconciliation == "applied"
    assert _observed(vault)["pages"]["topics/a.md"] == page_revision(after)
    assert service.history(path="topics/a.md") == (event,)
    await store.close()


async def test_external_edit_is_accepted_without_memory_reconciliation(tmp_path: Path):
    vault = tmp_path / "memory"
    store, page = await _store(vault)
    record = await store.add("Original.", kind="fact", source_ref=SourceRef("user", "test"))
    await store.refresh_from_disk()
    page.write_bytes(b"# A\n\nTrusted external edit.\n")
    change = next(change for change in await store.refresh_from_disk() if isinstance(change, ObservedFileChange))
    reconciler = Reconciler((RecordOperation.ask("Forget the matching memory?", record.id),))
    service = PageEditService(vault, store, reconciler=reconciler)

    event = await service.ingest_external(change)

    assert event is not None
    assert event.reconciliation == "applied"
    assert event.operations == ()
    assert event.review_operations == ()
    assert event.questions == ()
    assert reconciler.calls == 0
    assert await store.get(record.id) is not None
    await store.close()


async def test_external_deletion_is_accepted_without_changing_records(tmp_path: Path):
    vault = tmp_path / "memory"
    store, page = await _store(vault)
    record = await store.add("Original.", kind="fact", source_ref=SourceRef("user", "test"))
    await store.refresh_from_disk()
    before = page.read_bytes()
    page.unlink()
    change = next(change for change in await store.refresh_from_disk() if isinstance(change, ObservedFileChange))
    reconciler = Reconciler((RecordOperation.ask("Forget the matching memory?", record.id),))
    service = PageEditService(vault, store, reconciler=reconciler)

    event = await service.ingest_external(change)

    assert event is not None
    assert event.origin == "external"
    assert event.reconciliation == "applied"
    assert event.patch == unified_patch(before, b"")
    assert event.questions == ()
    assert event.review_operations == ()
    assert reconciler.calls == 0
    assert not page.exists()
    assert "topics/a.md" not in _observed(vault)["pages"]
    assert await store.get(record.id) is not None
    assert not page.exists()
    await store.close()


async def test_committed_external_event_is_reused_after_acknowledgement_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    vault = tmp_path / "memory"
    store, page = await _store(vault)
    page.write_bytes(page.read_bytes() + b"\nExternal.\n")
    change = (await store.refresh_from_disk())[0]
    service = PageEditService(vault, store, reconciler=Reconciler((RecordOperation.noop(),)))
    acknowledge = store.acknowledge_observed_change
    monkeypatch.setattr(store, "acknowledge_observed_change", lambda _change: (_ for _ in ()).throw(RuntimeError("crash")))

    with pytest.raises(RuntimeError, match="crash"):
        await service.ingest_external(change)

    monkeypatch.setattr(store, "acknowledge_observed_change", acknowledge)
    retried_change = (await store.refresh_from_disk())[0]
    event = await service.ingest_external(retried_change)

    assert event is not None and event.observation_id == change.observation_id
    assert len([item for item in service.history() if item.observation_id == change.observation_id]) == 1
    await store.close()


async def test_engine_write_marker_suppresses_exact_revision_once(tmp_path: Path):
    vault = tmp_path / "memory"
    store, page = await _store(vault)
    service = PageEditService(vault, store, reconciler=Reconciler((RecordOperation.noop(),)))
    before = page.read_bytes()
    after = before + b"\nEngine edit.\n"
    preview = await service.preview(
        path="topics/a.md",
        base_revision=page_revision(before),
        content=after,
        actor="agent:test",
        origin="agent",
    )
    await service.apply(preview.id, decisions={})

    marker = _observed(vault)["engine_writes"][0]
    assert (marker["origin"], marker["path"], marker["result_revision"], marker["result_exists"]) == (
        "agent",
        "topics/a.md",
        page_revision(after),
        True,
    )
    acknowledged = await store.refresh_from_disk()
    assert [change.path for change in acknowledged] == ["topics/a.md"]
    assert acknowledged[0].origin == "agent"
    assert _observed(vault)["engine_writes"] == []

    page.write_bytes(before)
    changes = await store.refresh_from_disk()
    assert [change.path for change in changes] == ["topics/a.md"]
    assert isinstance(changes[0], ObservedFileChange)
    await store.close()


async def test_generated_and_changelog_writes_never_become_user_page_edits(tmp_path: Path):
    vault = tmp_path / "memory"
    store, _page = await _store(vault)
    changelog = vault / "changelog/2026/2026-07.md"
    changelog.parent.mkdir(parents=True)
    changelog.write_text("# Generated changelog\n", encoding="utf-8")
    daily = vault / "daily/2026-07-13.md"
    daily.parent.mkdir()
    daily.write_text("---\ngenerated: true\n---\n\n# Daily\n", encoding="utf-8")
    managed_index = vault / "daily/README.md"
    managed_index.write_text(
        "<!-- ntrp:index:start -->\n- day.md — Day\n<!-- ntrp:index:end -->\n",
        encoding="utf-8",
    )
    manual = vault / "notes/manual.md"
    manual.parent.mkdir()
    manual.write_text("# Manual\n", encoding="utf-8")

    changes = await store.refresh_from_disk()
    observed = [change for change in changes if isinstance(change, ObservedFileChange)]

    assert [change.path for change in observed] == ["notes/manual.md"]
    assert "changelog/2026/2026-07.md" not in _observed(vault)["pages"]
    assert "daily/2026-07-13.md" not in _observed(vault)["pages"]
    assert "daily/README.md" not in _observed(vault)["pages"]
    await store.close()


async def test_committed_engine_event_suppresses_external_ingest_if_marker_write_crashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    vault = tmp_path / "memory"
    store, page = await _store(vault)
    service = PageEditService(vault, store, reconciler=Reconciler((RecordOperation.noop(),)))
    before = page.read_bytes()
    after = before + b"\nEngine edit.\n"
    preview = await service.preview(
        path="topics/a.md",
        base_revision=page_revision(before),
        content=after,
        actor="agent:test",
        origin="agent",
    )
    commit = store._journal.commit

    def commit_then_crash(files, **kwargs):
        commit(files, **kwargs)
        raise RuntimeError("crash after commit")

    monkeypatch.setattr(store._journal, "commit", commit_then_crash)

    with pytest.raises(RuntimeError, match="crash after commit"):
        await service.apply(preview.id, decisions={})

    monkeypatch.setattr(store._journal, "commit", commit)
    change = next(change for change in await store.refresh_from_disk() if isinstance(change, ObservedFileChange))

    assert change.origin == "agent"
    assert len(service.history(path="topics/a.md")) == 1
    assert _observed(vault)["pages"]["topics/a.md"] == page_revision(after)
    await store.close()


async def test_rapid_external_edits_preserve_order_and_bases(tmp_path: Path):
    vault = tmp_path / "memory"
    store, page = await _store(vault)
    base = page.read_bytes()
    first = base + b"\nFirst.\n"
    second = first + b"\nSecond.\n"
    service = PageEditService(
        vault,
        store,
        reconciler=Reconciler((RecordOperation.noop(),), (RecordOperation.noop(),)),
    )

    page.write_bytes(first)
    first_event = await service.ingest_external((await store.refresh_from_disk())[0])
    page.write_bytes(second)
    second_event = await service.ingest_external((await store.refresh_from_disk())[0])

    assert first_event is not None and second_event is not None
    assert (first_event.base_revision, first_event.result_revision) == (
        page_revision(base),
        page_revision(first),
    )
    assert (second_event.base_revision, second_event.result_revision) == (
        page_revision(first),
        page_revision(second),
    )
    assert service.history(path="topics/a.md") == (first_event, second_event)
    await store.close()


async def test_restart_recovers_observed_base_before_external_edit(tmp_path: Path):
    vault = tmp_path / "memory"
    store, page = await _store(vault)
    before = page.read_bytes()
    await store.close()
    after = before + b"\nEdited while stopped.\n"
    page.write_bytes(after)

    reopened = FilePageStore(vault)
    await reopened.open()
    changes = await reopened.refresh_from_disk()

    assert [change.path for change in changes] == ["topics/a.md"]
    assert changes[0].before == before
    assert changes[0].after == after
    await reopened.close()


async def test_runtime_publishes_only_after_external_event_and_snapshot_are_durable(tmp_path: Path):
    vault = tmp_path / "memory"
    store, page = await _store(vault)
    page.write_bytes(page.read_bytes() + b"\nExternal.\n")
    change = (await store.refresh_from_disk())[0]
    service = PageEditService(vault, store, reconciler=Reconciler((RecordOperation.noop(),)))
    captured = {}
    store.start_watch = lambda callback: captured.setdefault("callback", callback)
    runtime = KnowledgeRuntime.__new__(KnowledgeRuntime)
    runtime._record_store = store
    runtime._page_edit_service = service
    runtime._vault_index = SimpleNamespace(schedule=lambda: None)

    async def publish(paths, *, revision=None, review_required=False):
        assert service.history(path="topics/a.md")
        assert _observed(vault)["pages"]["topics/a.md"] == change.result_revision
        captured["published"] = (paths, revision, review_required)

    runtime.start_memory_watch(publish)
    await captured["callback"]([change])

    assert captured["published"] == (["topics/a.md"], change.result_revision, False)
    await store.close()


async def test_runtime_never_reingests_a_receipted_engine_write_as_external(tmp_path: Path):
    vault = tmp_path / "memory"
    store, page = await _store(vault)
    service = PageEditService(
        vault,
        store,
        reconciler=Reconciler((RecordOperation.noop(),), (RecordOperation.noop(),)),
    )
    before = page.read_bytes()
    preview = await service.preview(
        path="topics/a.md",
        base_revision=page_revision(before),
        content=before + b"\nEngine-authored.\n",
        actor="agent:test",
        origin="agent",
    )
    committed = await service.apply(preview.id, decisions={})
    change = next(change for change in await store.refresh_from_disk() if isinstance(change, ObservedFileChange))
    assert change.origin == "agent"

    captured = {}
    store.start_watch = lambda callback: captured.setdefault("callback", callback)
    runtime = KnowledgeRuntime.__new__(KnowledgeRuntime)
    runtime._record_store = store
    runtime._page_edit_service = service
    runtime._vault_index = SimpleNamespace(schedule=lambda: None)
    runtime._link_index = SimpleNamespace(schedule=lambda: None)

    async def publish(paths, *, revision=None, review_required=False):
        captured["published"] = (paths, revision, review_required)

    runtime.start_memory_watch(publish)
    await captured["callback"]([change])

    assert service.history(path="topics/a.md") == (committed,)
    assert captured["published"] == (["topics/a.md"], change.result_revision, False)
    await store.close()


async def test_runtime_publishes_revision_metadata_per_changed_page(tmp_path: Path):
    vault = tmp_path / "memory"
    store, first = await _store(vault)
    second = vault / "topics" / "b.md"
    first.write_bytes(first.read_bytes() + b"\nFirst external.\n")
    second.write_bytes(b"# B\n\nSecond external.\n")
    changes = await store.refresh_from_disk()
    page_changes = [change for change in changes if isinstance(change, ObservedFileChange)]
    service = PageEditService(
        vault,
        store,
        reconciler=Reconciler((RecordOperation.noop(),), (RecordOperation.noop(),)),
    )
    captured = {}
    store.start_watch = lambda callback: captured.setdefault("callback", callback)
    runtime = KnowledgeRuntime.__new__(KnowledgeRuntime)
    runtime._record_store = store
    runtime._page_edit_service = service
    runtime._vault_index = SimpleNamespace(schedule=lambda: None)
    published = []

    async def publish(paths, *, revision=None, review_required=False):
        published.append((paths, revision, review_required))

    runtime.start_memory_watch(publish)
    await captured["callback"](page_changes)

    assert published == [
        ([change.path], change.result_revision, False) for change in page_changes
    ]
    await store.close()


async def test_memory_changed_sse_keeps_paths_and_adds_revision_metadata():
    event = MemoryChangedEvent(paths=["topics/a.md"], revision="a" * 64, review_required=True)

    data = json.loads(event.to_sse()["data"])

    assert data["paths"] == ["topics/a.md"]
    assert data["revision"] == "a" * 64
    assert data["review_required"] is True


async def test_observed_state_read_rejects_symlinked_maintenance_parent(tmp_path: Path):
    vault = tmp_path / "memory"
    store, _page = await _store(vault)
    maintenance = vault / ".ntrp" / "maintenance"
    maintenance.rename(vault / ".ntrp" / "maintenance-real")
    outside = tmp_path / "outside"
    outside.mkdir()
    maintenance.symlink_to(outside, target_is_directory=True)

    with pytest.raises((FileNotFoundError, ValueError)):
        store._read_observed_state()
    await store.close()


async def test_observed_state_write_stays_on_open_parent_during_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    vault = tmp_path / "memory"
    store, _page = await _store(vault)
    maintenance = vault / ".ntrp" / "maintenance"
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "observed-pages.json"
    sentinel.write_text("outside\n", encoding="utf-8")
    state = _observed(vault)
    original = ArtifactMemoryStore._open_anchored_parent
    swapped = False

    def swap_after_open(resources, rel, *, create_parents):
        nonlocal swapped
        result = original(resources, rel, create_parents=create_parents)
        if not swapped:
            swapped = True
            maintenance.rename(vault / ".ntrp" / "maintenance-real")
            maintenance.symlink_to(outside, target_is_directory=True)
        return result

    monkeypatch.setattr(ArtifactMemoryStore, "_open_anchored_parent", swap_after_open)

    store._write_observed_state(state)

    assert sentinel.read_text(encoding="utf-8") == "outside\n"
    await store.close()


async def test_editable_page_read_cannot_escape_on_parent_swap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    vault = tmp_path / "memory"
    store, page = await _store(vault)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.md").write_bytes(b"SECRET")
    original_open = __import__("os").open
    swapped = False

    def swap_before_anchored_component_open(path, *args, **kwargs):
        nonlocal swapped
        if not swapped and path == "topics" and kwargs.get("dir_fd") is not None:
            swapped = True
            page.parent.rename(vault / "topics-real")
            page.parent.symlink_to(outside, target_is_directory=True)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr("os.open", swap_before_anchored_component_open)

    assert b"SECRET" not in store._editable_page_bytes().values()
    assert swapped is True
    await store.close()


async def test_matching_old_engine_event_without_marker_never_suppresses_external_edit(tmp_path: Path):
    vault = tmp_path / "memory"
    store, page = await _store(vault)
    service = PageEditService(vault, store, reconciler=Reconciler((RecordOperation.noop(),)))
    base = page.read_bytes()
    result = base + b"\nSame transition.\n"
    preview = await service.preview(
        path="topics/a.md",
        base_revision=page_revision(base),
        content=result,
        actor="agent:test",
        origin="agent",
    )
    await service.apply(preview.id, decisions={})
    await store.refresh_from_disk()
    page.write_bytes(base)
    reverted = next(change for change in await store.refresh_from_disk() if isinstance(change, ObservedFileChange))
    store.acknowledge_observed_change(reverted)
    page.write_bytes(result)
    repeated = next(change for change in await store.refresh_from_disk() if isinstance(change, ObservedFileChange))

    event = await service.ingest_external(repeated)

    assert event is not None and event.origin == "external"
    assert len(service.history(path="topics/a.md")) == 2
    await store.close()


async def test_unbound_exact_result_marker_is_pruned_without_suppression(tmp_path: Path):
    vault = tmp_path / "memory"
    store, page = await _store(vault)
    base = page.read_bytes()
    result = base + b"\nExternal matching bytes.\n"
    state = _observed(vault)
    state["engine_writes"].append(
        {
            "origin": "synthesis",
            "path": "topics/a.md",
            "base_revision": page_revision(base),
            "base_exists": True,
            "result_revision": page_revision(result),
            "result_exists": True,
            "event_id": None,
            "batch_key": None,
            "entry_ids": [],
        }
    )
    store._store_observed_base(result)
    store._write_observed_state(state)
    page.write_bytes(result)

    change = next(change for change in await store.refresh_from_disk() if isinstance(change, ObservedFileChange))

    assert change.origin == "external"
    assert _observed(vault)["engine_writes"] == []
    await store.close()


async def test_live_synthesis_write_uses_atomic_receipt_for_crash_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    vault = tmp_path / "memory"
    store, page = await _store(vault)
    store._pages[page].prose += "\nSynthesized live.\n"
    commit = store._journal.commit_projection

    def commit_then_crash(files, **kwargs):
        commit(files, **kwargs)
        raise RuntimeError("crash after synthesis commit")

    monkeypatch.setattr(store._journal, "commit_projection", commit_then_crash)
    with pytest.raises(RuntimeError, match="crash after synthesis commit"):
        store._persist(page)
    await store.close()

    reopened = FilePageStore(vault)
    await reopened.open()
    change = next(change for change in await reopened.refresh_from_disk() if isinstance(change, ObservedFileChange))

    assert change.path == "topics/a.md"
    assert change.origin == "synthesis"
    await reopened.close()


async def test_engine_deletion_moves_page_to_durable_receipt_before_suppression(tmp_path: Path):
    vault = tmp_path / "memory"
    store, page = await _store(vault)
    before = page.read_bytes()

    store._remove_page_files(page)
    marker = next(marker for marker in _observed(vault)["engine_writes"] if marker["path"] == "topics/a.md")
    change = next(change for change in await store.refresh_from_disk() if isinstance(change, ObservedFileChange))

    assert change.origin == "synthesis"
    receipt = vault / marker["delete_receipt"]
    assert receipt.read_bytes() == before
    await store.close()


async def test_external_ingest_never_stages_the_already_changed_page(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    vault = tmp_path / "memory"
    store, page = await _store(vault)
    page.write_bytes(page.read_bytes() + b"\nExternal.\n")
    change = next(change for change in await store.refresh_from_disk() if isinstance(change, ObservedFileChange))
    service = PageEditService(vault, store, reconciler=Reconciler((RecordOperation.noop(),)))
    commit = store._journal.commit
    staged = []

    def capture(files, **kwargs):
        staged.append(set(files))
        return commit(files, **kwargs)

    monkeypatch.setattr(store._journal, "commit", capture)

    await service.ingest_external(change)

    assert change.path not in {path.as_posix() for path in staged[0]}
    await store.close()


async def test_committed_pending_observation_preserves_intermediate_base_after_new_disk_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    vault = tmp_path / "memory"
    store, page = await _store(vault)
    base = page.read_bytes()
    first = base + b"\nFirst.\n"
    second = first + b"\nSecond.\n"
    page.write_bytes(first)
    first_change = next(change for change in await store.refresh_from_disk() if isinstance(change, ObservedFileChange))
    service = PageEditService(
        vault,
        store,
        reconciler=Reconciler((RecordOperation.noop(),), (RecordOperation.noop(),)),
    )
    acknowledge = store.acknowledge_observed_change
    monkeypatch.setattr(
        store,
        "acknowledge_observed_change",
        lambda _change: (_ for _ in ()).throw(RuntimeError("ack crash")),
    )
    with pytest.raises(RuntimeError, match="ack crash"):
        await service.ingest_external(first_change)
    page.write_bytes(second)
    monkeypatch.setattr(store, "acknowledge_observed_change", acknowledge)

    retried_first = next(change for change in await store.refresh_from_disk() if isinstance(change, ObservedFileChange))
    first_event = await service.ingest_external(retried_first)
    second_change = next(change for change in await store.refresh_from_disk() if isinstance(change, ObservedFileChange))
    second_event = await service.ingest_external(second_change)

    assert first_event is not None and first_event.observation_id == first_change.observation_id
    assert (second_change.before, second_change.after) == (first, second)
    assert second_event is not None
    assert [(event.base_revision, event.result_revision) for event in service.history(path="topics/a.md")] == [
        (page_revision(base), page_revision(first)),
        (page_revision(first), page_revision(second)),
    ]
    await store.close()


async def test_stale_engine_markers_are_pruned_without_a_file_change_and_track_existence(tmp_path: Path):
    vault = tmp_path / "memory"
    store, page = await _store(vault)
    base = page.read_bytes()
    first = base + b"\nR1.\n"
    second = first + b"\nR2.\n"
    store.register_engine_write_intent(
        "topics/a.md", first, origin="synthesis", event_id="missing-first"
    )
    store.register_engine_write_intent(
        "topics/a.md", second, origin="synthesis", event_id="missing-second"
    )

    assert await store.refresh_from_disk() == []
    assert _observed(vault)["engine_writes"] == []

    page.write_bytes(first)
    change = next(change for change in await store.refresh_from_disk() if isinstance(change, ObservedFileChange))
    assert change.origin == "external"
    store.acknowledge_observed_change(change)

    page.write_bytes(b"")
    store.register_engine_write_intent(
        "topics/a.md", None, origin="synthesis", event_id="missing-delete"
    )
    empty_change = next(change for change in await store.refresh_from_disk() if isinstance(change, ObservedFileChange))
    assert empty_change.origin == "external"
    await store.close()


async def test_generic_write_intent_survives_commit_then_exception_and_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    vault = tmp_path / "memory"
    store, _page = await _store(vault)
    commit = store._journal.commit

    def commit_then_crash(files, **kwargs):
        commit(files, **kwargs)
        raise RuntimeError("crash after commit")

    monkeypatch.setattr(store._journal, "commit", commit_then_crash)
    with pytest.raises(RuntimeError, match="crash after commit"):
        store.apply_operations(
            (RecordOperation.add("Committed generic write"),),
            SourceRef("test", "intent"),
            batch_key="intent-test",
        )
    await store.close()

    reopened = FilePageStore(vault)
    await reopened.open()
    changes = [change for change in await reopened.refresh_from_disk() if isinstance(change, ObservedFileChange)]

    assert changes and all(change.origin == "synthesis" for change in changes)
    await reopened.close()


async def test_uncommitted_generic_intent_cannot_suppress_later_matching_external_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    vault = tmp_path / "memory"
    store, _page = await _store(vault)
    monkeypatch.setattr(
        store._journal,
        "commit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("pre-commit crash")),
    )
    with pytest.raises(RuntimeError, match="pre-commit crash"):
        store.apply_operations(
            (RecordOperation.add("Uncommitted generic write"),),
            SourceRef("test", "uncommitted-intent"),
        )
    marker = _observed(vault)["engine_writes"][0]
    desired = (vault / ".ntrp" / "maintenance" / "observed-page-bases" / marker["result_revision"]).read_bytes()
    target = vault / marker["path"]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(desired)

    change = next(change for change in await store.refresh_from_disk() if isinstance(change, ObservedFileChange))

    assert change.origin == "external"
    await store.close()


async def test_runtime_publishes_first_page_before_second_ingest_failure(tmp_path: Path):
    runtime = KnowledgeRuntime.__new__(KnowledgeRuntime)
    callbacks = {}
    runtime._record_store = SimpleNamespace(start_watch=lambda callback: callbacks.setdefault("watch", callback))
    runtime._vault_index = SimpleNamespace(schedule=lambda: None)
    changes = [
        ObservedFileChange("one", "a.md", b"a", b"b", page_revision(b"a"), page_revision(b"b")),
        ObservedFileChange("two", "b.md", b"c", b"d", page_revision(b"c"), page_revision(b"d")),
    ]
    calls = 0

    async def ingest(change):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("page two failed")
        return SimpleNamespace(result_revision=change.result_revision, reconciliation="needs_review")

    runtime._page_edit_service = SimpleNamespace(ingest_external=ingest)
    published = []

    async def publish(paths, *, revision=None, review_required=False):
        published.append((paths, revision, review_required))

    runtime.start_memory_watch(publish)
    with pytest.raises(RuntimeError, match="page two failed"):
        await callbacks["watch"](changes)

    assert published == [(["a.md"], page_revision(b"b"), False)]


async def test_uncommitted_stale_observation_is_abandoned_for_latest_disk_state(tmp_path: Path):
    vault = tmp_path / "memory"
    store, page = await _store(vault)
    base = page.read_bytes()
    first = base + b"\nFirst.\n"
    latest = base + b"\nLatest.\n"
    page.write_bytes(first)
    stale = next(change for change in await store.refresh_from_disk() if isinstance(change, ObservedFileChange))
    page.write_bytes(latest)
    service = PageEditService(vault, store, reconciler=Reconciler((RecordOperation.noop(),)))

    with pytest.raises(StalePageRevisionError):
        await service.ingest_external(stale)

    current = next(change for change in await store.refresh_from_disk() if isinstance(change, ObservedFileChange))
    assert (current.before, current.after) == (base, latest)
    assert current.observation_id != stale.observation_id
    await store.close()
