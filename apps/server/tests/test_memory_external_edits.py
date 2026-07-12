from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from ntrp.events.sse import MemoryChangedEvent
from ntrp.memory.artifacts import ArtifactMemoryStore
from ntrp.memory.file_store import FilePageStore, ObservedFileChange
from ntrp.memory.models import SourceRef
from ntrp.memory.page_edit_service import PageEditService
from ntrp.memory.page_events import PageEditDecision, page_revision, unified_patch
from ntrp.memory.reconciler import RecordOperation
from ntrp.server.runtime.knowledge import KnowledgeRuntime

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.asyncio


class Reconciler:
    def __init__(self, *answers) -> None:
        self.answers = list(answers)

    async def __call__(self, _analysis):
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


async def test_external_deletion_ask_is_review_only_and_never_restores_file(tmp_path: Path):
    vault = tmp_path / "memory"
    store, page = await _store(vault)
    record = await store.add("Original.", kind="fact", source_ref=SourceRef("user", "test"))
    await store.refresh_from_disk()
    before = page.read_bytes()
    page.unlink()
    change = next(change for change in await store.refresh_from_disk() if isinstance(change, ObservedFileChange))
    answer = (RecordOperation.ask("Forget the matching memory?", record.id),)
    service = PageEditService(vault, store, reconciler=Reconciler(answer))

    event = await service.ingest_external(change)

    assert event is not None
    assert event.origin == "external"
    assert event.reconciliation == "needs_review"
    assert event.patch == unified_patch(before, b"")
    assert event.questions and event.review_operations[0].op == "ASK"
    assert not page.exists()
    assert "topics/a.md" not in _observed(vault)["pages"]

    resolved = await service.retry(
        event.id,
        decisions={
            "operation:0": PageEditDecision(choice="Forget memory", target_ids=(record.id,))
        },
    )

    assert resolved.reconciliation == "applied"
    assert resolved.reconciles_event_id == event.id
    assert await store.get(record.id) is None
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

    assert _observed(vault)["engine_writes"] == [
        {"origin": "agent", "path": "topics/a.md", "result_revision": page_revision(after)}
    ]
    acknowledged = await store.refresh_from_disk()
    assert [change.path for change in acknowledged] == ["topics/a.md"]
    assert acknowledged[0].origin == "agent"
    assert _observed(vault)["engine_writes"] == []

    page.write_bytes(before)
    changes = await store.refresh_from_disk()
    assert [change.path for change in changes] == ["topics/a.md"]
    assert isinstance(changes[0], ObservedFileChange)
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
    register = store.register_engine_write
    monkeypatch.setattr(
        store,
        "register_engine_write",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("marker crash")),
    )

    with pytest.raises(RuntimeError, match="marker crash"):
        await service.apply(preview.id, decisions={})

    monkeypatch.setattr(store, "register_engine_write", register)
    change = next(change for change in await store.refresh_from_disk() if isinstance(change, ObservedFileChange))
    restarted = PageEditService(
        vault,
        store,
        reconciler=lambda _analysis: (_ for _ in ()).throw(AssertionError("must not reconcile")),
    )

    assert await restarted.ingest_external(change) is None
    assert len(restarted.history(path="topics/a.md")) == 1
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
