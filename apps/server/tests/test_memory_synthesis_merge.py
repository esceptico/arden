from __future__ import annotations

from pathlib import Path

import pytest

from ntrp.memory.merge import three_way_merge
from ntrp.memory.page_edit_service import PageEditService
from ntrp.memory.page_events import page_revision
from ntrp.memory.pages import Page
from ntrp.memory.synthesize import _stale


def test_three_way_merge_applies_nonoverlapping_generated_hunks_exactly():
    base = b"---\ntitle: A\n---\n\n# A\n\nGenerated old.\n\nKeep.\n"
    current = b"---\ntitle: A\naliases: [\"Alpha\"]\n---\n\n# A\n\nGenerated old.\n\n*Keep.*\n"
    generated = b"---\ntitle: A\n---\n\n# A\n\nGenerated new.\n\nKeep.\n"

    result = three_way_merge(base, current, generated)

    assert result.merged == (
        b"---\ntitle: A\naliases: [\"Alpha\"]\n---\n\n# A\n\nGenerated new.\n\n*Keep.*\n"
    )
    assert result.review_required is False
    assert result.candidate == result.merged


def test_three_way_merge_keeps_current_on_exact_overlap():
    base = b"# A\n\nOld wording.\n"
    current = b"# A\n\nUser wording.\n"
    generated = b"# A\n\nGenerated wording.\n"

    result = three_way_merge(base, current, generated)

    assert result.merged is None
    assert result.candidate == generated
    assert result.review_required is True
    assert result.reason == "overlap"


def test_three_way_merge_keeps_current_when_base_is_missing():
    current = b"# A\n\nUser prose.\n"
    generated = b"# A\n\nGenerated prose.\n"

    result = three_way_merge(None, current, generated)

    assert result.merged is None
    assert result.candidate == generated
    assert result.review_required is True
    assert result.reason == "missing_base"


def test_freshness_distinguishes_two_canonical_revisions_on_the_same_day():
    first = "a" * 64
    second = "b" * 64
    page = Page(frontmatter={"generated_from_revision": first}, prose="Generated.")

    assert _stale(page, first) is False
    assert _stale(page, second) is True


@pytest.mark.asyncio
async def test_generated_page_preserves_exact_frontmatter_bytes(tmp_path: Path):
    from ntrp.memory.file_store import FilePageStore
    from ntrp.memory.synthesize import _render_generated_page

    vault = tmp_path / "memory"
    (vault / "topics").mkdir(parents=True)
    (vault / "raw/topics").mkdir(parents=True)
    page = vault / "topics/a.md"
    frontmatter = b"---\n# user comment\ntitle:  \"A\"\naliases: ['Alpha']\n---\n"
    page.write_bytes(frontmatter)
    (vault / "raw/topics/a.md").write_text(
        "<!-- ntrp:records schema=2 page=topics/a.md -->\n",
        encoding="utf-8",
    )
    store = FilePageStore(vault)
    await store.open()

    generated = _render_generated_page(store, page, "Generated prose.")

    assert generated == frontmatter + b"\nGenerated prose.\n"
    await store.close()


def test_synthesis_maintenance_write_rejects_symlinked_parent(tmp_path: Path):
    from ntrp.memory.artifacts import ArtifactMemoryStore
    from ntrp.memory.merge import synthesis_base_rel

    vault = tmp_path / "memory"
    outside = tmp_path / "outside"
    outside.mkdir()
    (vault / ".ntrp").mkdir(parents=True)
    (vault / ".ntrp/maintenance").symlink_to(outside, target_is_directory=True)

    with pytest.raises(FileNotFoundError):
        ArtifactMemoryStore(vault).write_synthesis_maintenance(
            synthesis_base_rel("topics/a.md", "a" * 64),
            b"unsafe",
        )

    assert tuple(outside.iterdir()) == ()


@pytest.mark.asyncio
async def test_missing_base_persists_candidate_without_touching_visible_page(tmp_path: Path):
    from ntrp.memory.file_store import FilePageStore
    from ntrp.memory.merge import synthesis_candidate_rel
    from ntrp.memory.synthesize import _merge_generated_page

    vault = tmp_path / "memory"
    (vault / "topics").mkdir(parents=True)
    (vault / "raw" / "topics").mkdir(parents=True)
    page = vault / "topics" / "a.md"
    current = b"# A\n\nUser prose.\n"
    generated = b"# A\n\nGenerated prose.\n"
    page.write_bytes(current)
    (vault / "raw" / "topics" / "a.md").write_text(
        "<!-- ntrp:records schema=2 page=topics/a.md -->\n",
        encoding="utf-8",
    )
    store = FilePageStore(vault)
    await store.open()
    await store.add("Fact")
    revision = store.canonical_revision

    result = await _merge_generated_page(
        store,
        page,
        generated,
        source_revision=revision,
        prose_cites=(),
    )

    assert result.review_required is True
    assert page.read_bytes() == current
    assert (vault / synthesis_candidate_rel("topics/a.md", revision)).read_bytes() == generated
    assert PageEditService(vault, store, reconciler=None).history(path="topics/a.md") == ()
    await store.close()


@pytest.mark.asyncio
async def test_accepted_merge_preserves_user_formatting_and_rotates_exact_base(tmp_path: Path):
    from ntrp.memory.file_store import FilePageStore
    from ntrp.memory.merge import synthesis_base_rel
    from ntrp.memory.pages import render_raw
    from ntrp.memory.synthesize import _merge_generated_page

    vault = tmp_path / "memory"
    (vault / "topics").mkdir(parents=True)
    (vault / "raw" / "topics").mkdir(parents=True)
    page = vault / "topics" / "a.md"
    old_base = b"---\ntitle: A\n---\n\n# A\n\nGenerated old.\n\nKeep.\n"
    current = b"---\ntitle: A\naliases: [\"Alpha\"]\n---\n\n# A\n\nGenerated old.\n\n*Keep.*\n"
    generated = b"---\ntitle: A\n---\n\n# A\n\nGenerated new.\n\nKeep.\n"
    page.write_bytes(current)
    raw_rel = Path("raw/topics/a.md")
    (vault / raw_rel).write_text("<!-- ntrp:records schema=2 page=topics/a.md -->\n", encoding="utf-8")
    store = FilePageStore(vault)
    await store.open()
    await store.add("Fact")
    revision = store.canonical_revision
    store._pages[page].frontmatter["generated_from_revision"] = revision
    store._journal.commit_projection({raw_rel: render_raw(store._pages[page]).encode()})
    from ntrp.memory.artifacts import ArtifactMemoryStore

    ArtifactMemoryStore(vault).write_synthesis_maintenance(synthesis_base_rel("topics/a.md", revision), old_base)
    store._reload_canonical_state()

    result = await _merge_generated_page(
        store,
        page,
        generated,
        source_revision=revision,
        prose_cites=("deadbeef",),
    )

    assert result.review_required is False
    assert page.read_bytes() == (
        b"---\ntitle: A\naliases: [\"Alpha\"]\n---\n\n# A\n\nGenerated new.\n\n*Keep.*\n"
    )
    assert (vault / synthesis_base_rel("topics/a.md", revision)).read_bytes() == generated
    assert store._pages[page].frontmatter["generated_from_revision"] == revision
    assert store._pages[page].frontmatter["prose_cites"] == ["deadbeef"]
    events = PageEditService(vault, store, reconciler=None).history(path="topics/a.md")
    assert len(events) == 1
    assert events[0].event_type == "SYNTHESIS_MERGE"
    assert events[0].source_canonical_revision == revision
    assert store.canonical_revision == revision
    repeated = await _merge_generated_page(
        store,
        page,
        generated,
        source_revision=revision,
        prose_cites=("deadbeef",),
    )
    assert repeated.review_required is False
    assert len(PageEditService(vault, store, reconciler=None).history(path="topics/a.md")) == 1
    await store.close()


@pytest.mark.asyncio
async def test_overlapping_generated_change_persists_candidate_only(tmp_path: Path):
    from ntrp.memory.file_store import FilePageStore
    from ntrp.memory.merge import synthesis_base_rel, synthesis_candidate_rel
    from ntrp.memory.pages import render_raw
    from ntrp.memory.synthesize import _merge_generated_page

    vault = tmp_path / "memory"
    (vault / "topics").mkdir(parents=True)
    (vault / "raw" / "topics").mkdir(parents=True)
    page = vault / "topics" / "a.md"
    base = b"# A\n\nOld.\n"
    current = b"# A\n\nUser.\n"
    generated = b"# A\n\nGenerated.\n"
    page.write_bytes(current)
    raw_rel = Path("raw/topics/a.md")
    (vault / raw_rel).write_text("<!-- ntrp:records schema=2 page=topics/a.md -->\n", encoding="utf-8")
    store = FilePageStore(vault)
    await store.open()
    await store.add("Fact")
    revision = store.canonical_revision
    store._pages[page].frontmatter["generated_from_revision"] = revision
    store._journal.commit_projection({raw_rel: render_raw(store._pages[page]).encode()})
    from ntrp.memory.artifacts import ArtifactMemoryStore

    ArtifactMemoryStore(vault).write_synthesis_maintenance(synthesis_base_rel("topics/a.md", revision), base)
    store._reload_canonical_state()

    result = await _merge_generated_page(
        store,
        page,
        generated,
        source_revision=revision,
        prose_cites=(),
    )

    assert result.reason == "overlap"
    assert page.read_bytes() == current
    assert (vault / synthesis_candidate_rel("topics/a.md", revision)).read_bytes() == generated
    assert (vault / synthesis_base_rel("topics/a.md", revision)).read_bytes() == base
    assert PageEditService(vault, store, reconciler=None).history(path="topics/a.md") == ()
    await store.close()


@pytest.mark.asyncio
async def test_new_in_memory_generated_page_bootstraps_from_exact_empty_page(tmp_path: Path):
    from ntrp.memory.file_store import FilePageStore
    from ntrp.memory.merge import synthesis_base_rel
    from ntrp.memory.synthesize import _merge_generated_page

    vault = tmp_path / "memory"
    store = FilePageStore(vault)
    await store.open()
    await store.add("Fact")
    revision = store.canonical_revision
    path = vault / "active-work.md"
    store._ensure_page(path, title="Active work")
    generated = b"---\ntype: topic\ntitle: Active work\nupdated: 2026-07-12\n---\n\nGenerated.\n"

    result = await _merge_generated_page(
        store,
        path,
        generated,
        source_revision=revision,
        prose_cites=(),
    )

    assert result.review_required is False
    assert path.read_bytes() == generated
    assert (vault / synthesis_base_rel("active-work.md", revision)).read_bytes() == generated
    events = PageEditService(vault, store, reconciler=None).history(path="active-work.md")
    assert len(events) == 1
    assert events[0].base_revision == page_revision(b"")
    await store.close()


@pytest.mark.asyncio
async def test_revision_change_during_generation_persists_retry_candidate(tmp_path: Path):
    from ntrp.memory.file_store import FilePageStore
    from ntrp.memory.merge import synthesis_candidate_rel
    from ntrp.memory.synthesize import _merge_generated_page

    vault = tmp_path / "memory"
    store = FilePageStore(vault)
    await store.open()
    await store.add("Fact")
    path = vault / "active-work.md"
    store._ensure_page(path, title="Active work")
    stale_revision = "a" * 64
    generated = b"# Active work\n\nGenerated.\n"

    result = await _merge_generated_page(
        store,
        path,
        generated,
        source_revision=stale_revision,
        prose_cites=(),
    )

    assert result.reason == "stale_source"
    assert not path.exists()
    assert (vault / synthesis_candidate_rel("active-work.md", stale_revision)).read_bytes() == generated
    await store.close()


@pytest.mark.asyncio
async def test_failed_atomic_merge_does_not_advance_generated_base(tmp_path: Path, monkeypatch):
    from ntrp.memory.file_store import FilePageStore
    from ntrp.memory.merge import synthesis_base_rel

    vault = tmp_path / "memory"
    (vault / "topics").mkdir(parents=True)
    (vault / "raw" / "topics").mkdir(parents=True)
    page = vault / "topics/a.md"
    current = b"# A\n"
    generated = b"# A\n\nGenerated.\n"
    page.write_bytes(current)
    (vault / "raw/topics/a.md").write_text(
        "<!-- ntrp:records schema=2 page=topics/a.md -->\n",
        encoding="utf-8",
    )
    store = FilePageStore(vault)
    await store.open()
    await store.add("Fact")
    revision = store.canonical_revision
    service = PageEditService(vault, store, reconciler=None)

    def fail(*_args, **_kwargs):
        raise RuntimeError("injected projection failure")

    monkeypatch.setattr(store._journal, "commit_projection", fail)
    with pytest.raises(RuntimeError, match="injected projection failure"):
        await service.apply_synthesis_merge(
            path="topics/a.md",
            base=current,
            result=generated,
            source_revision=revision,
            generated_base=generated,
        )

    assert page.read_bytes() == current
    assert not (vault / synthesis_base_rel("topics/a.md", revision)).exists()
    assert service.history(path="topics/a.md") == ()
    await store.close()


@pytest.mark.asyncio
async def test_post_commit_base_failure_does_not_duplicate_accepted_event(tmp_path: Path, monkeypatch):
    from ntrp.memory.file_store import FilePageStore
    from ntrp.memory.merge import synthesis_base_rel
    from ntrp.memory.synthesize import _merge_generated_page

    vault = tmp_path / "memory"
    (vault / "topics").mkdir(parents=True)
    (vault / "raw" / "topics").mkdir(parents=True)
    page = vault / "topics/a.md"
    current = b"# A\n"
    generated = b"# A\n\nGenerated.\n"
    page.write_bytes(current)
    (vault / "raw/topics/a.md").write_text(
        "<!-- ntrp:records schema=2 page=topics/a.md -->\n",
        encoding="utf-8",
    )
    store = FilePageStore(vault)
    await store.open()
    await store.add("Fact")
    revision = store.canonical_revision
    service = PageEditService(vault, store, reconciler=None)

    def fail(*_args, **_kwargs):
        raise OSError("injected base failure")

    monkeypatch.setattr(service._resources, "write_synthesis_maintenance", fail)
    event = await service.apply_synthesis_merge(
        path="topics/a.md",
        base=current,
        result=generated,
        source_revision=revision,
        generated_base=generated,
    )

    assert event.event_type == "SYNTHESIS_MERGE"
    assert page.read_bytes() == generated
    assert not (vault / synthesis_base_rel("topics/a.md", revision)).exists()
    retry = await _merge_generated_page(
        store,
        page,
        generated,
        source_revision=revision,
        prose_cites=(),
    )
    assert retry.reason == "missing_base"
    assert len(service.history(path="topics/a.md")) == 1
    await store.close()


@pytest.mark.asyncio
async def test_synthesis_merge_commits_visible_page_and_exact_event_together(tmp_path: Path, monkeypatch):
    vault = tmp_path / "memory"
    (vault / "topics").mkdir(parents=True)
    (vault / "raw" / "topics").mkdir(parents=True)
    page = vault / "topics" / "a.md"
    base = b"# A\n\nOld.\n"
    result = b"# A\n\nNew.\n"
    page.write_bytes(base)
    (vault / "raw" / "topics" / "a.md").write_text(
        "<!-- ntrp:records schema=2 page=topics/a.md -->\n",
        encoding="utf-8",
    )
    from ntrp.memory.file_store import FilePageStore

    store = FilePageStore(vault)
    await store.open()
    service = PageEditService(vault, store, reconciler=None)
    source_revision = store.canonical_revision
    commits: list[dict[Path, bytes]] = []
    commit_projection = store._journal.commit_projection

    def capture(files, **kwargs):
        commits.append(dict(files))
        return commit_projection(files, **kwargs)

    monkeypatch.setattr(store._journal, "commit_projection", capture)

    event = await service.apply_synthesis_merge(
        path="topics/a.md",
        base=base,
        result=result,
        source_revision=source_revision,
    )

    assert page.read_bytes() == result
    assert event.event_type == "SYNTHESIS_MERGE"
    assert event.actor == "synthesis"
    assert event.origin == "synthesis"
    assert event.source_canonical_revision == source_revision
    assert event.base_revision == page_revision(base)
    assert event.result_revision == page_revision(result)
    assert len(commits) == 1
    assert Path("topics/a.md") in commits[0]
    assert any(path.parts[:2] == ("raw", "events") for path in commits[0])
    assert store.canonical_revision == source_revision
    await store.close()
