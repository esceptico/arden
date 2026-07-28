from __future__ import annotations

import threading
from dataclasses import FrozenInstanceError

import pytest

from arden.core.prompts import build_system_blocks
from arden.revisions import ChangeSet, Create, ManagedFileRepository, RevisionConflictError
from arden.search.types import SearchResult
from arden.wiki.context import (
    WIKI_PAGE_SOURCE,
    WikiContextBuilder,
    WikiPageIndexProjection,
    WikiPageIndexState,
    _normalized_query,
)
from arden.wiki.models import WikiMaintenancePageUpdate
from arden.wiki.service import (
    WikiService,
)


class _Store:
    def __init__(self) -> None:
        self.items: dict[str, tuple[str, str, dict]] = {}

    async def get_indexed_hashes(self, source: str):
        assert source == WIKI_PAGE_SOURCE
        return {
            source_id: (index, content)
            for index, (source_id, (_title, content, _meta)) in enumerate(self.items.items())
        }


class _Index:
    def __init__(self) -> None:
        self.store = _Store()
        self.deleted: list[str] = []

    async def upsert(self, source, source_id, title, content, metadata):
        assert source == WIKI_PAGE_SOURCE
        self.store.items[source_id] = (title, content, metadata)
        return True

    async def delete(self, source, source_id):
        assert source == WIKI_PAGE_SOURCE
        self.deleted.append(source_id)
        self.store.items.pop(source_id, None)
        return True

    async def update_metadata(self, source, source_id, metadata):
        assert source == WIKI_PAGE_SOURCE
        title, content, _old = self.store.items[source_id]
        self.store.items[source_id] = (title, content, metadata)
        return True

    async def search(self, _query, *, sources, limit):
        assert sources == [WIKI_PAGE_SOURCE]
        return [
            SearchResult(source=WIKI_PAGE_SOURCE, source_id="topic", title="Topic", snippet="A useful topic"),
        ][:limit]


async def _fact_revision() -> str:
    return "a" * 64


@pytest.mark.asyncio
async def test_wiki_index_tracks_active_pages_and_context_keeps_residents_separate(tmp_path) -> None:
    wiki = WikiService(ManagedFileRepository(tmp_path / "pages", history_root=tmp_path / "history"))
    wiki.create_page(page_id="home", path="README.md", title="Home", body=b"Start here.\n")
    wiki.create_page(page_id="directives", path="directives.md", title="Directives", body=b"Be concise.\n")
    wiki.create_page(page_id="me", path="me.md", title="Profile", body=b"Likes tea.\n")
    topic = wiki.create_page(
        page_id="topic",
        path="topics/topic.md",
        title="Topic",
        body=b"A useful topic with enough detail for retrieval.\n",
        metadata={"generated_from_revision": "b" * 64, "fact_citations": []},
    )
    feed = wiki.create_page(
        page_id="feed",
        path="feeds/current.md",
        title="Current Feed",
        body=b"Producer-owned content.\n",
        metadata={
            "generated_from_revision": "b" * 64,
            "producer_automation_id": "feed-worker",
        },
    )
    index = _Index()
    projection = WikiPageIndexProjection(wiki, lambda: index, _fact_revision)
    await projection.sync()

    assert set(index.store.items) == {"home", "directives", "me", "topic", "feed"}
    assert projection.last_state == WikiPageIndexState(wiki.repository.head, "ready")
    with pytest.raises(FrozenInstanceError):
        projection.last_state.status = "error"  # type: ignore[misc]
    assert index.store.items["topic"][2] == {
        "resource_path": "topics/topic.md",
        "resource_version": topic.resource.version_id,
        "role": "common",
        "freshness": "stale",
    }
    assert index.store.items["feed"][2] == {
        "resource_path": "feeds/current.md",
        "resource_version": feed.resource.version_id,
        "role": "common",
        "freshness": "current",
    }

    current = wiki.read_page("topic")
    wiki.apply_maintenance_updates(
        (WikiMaintenancePageUpdate("topic", current.resource.version_id, "Renamed topic", (), current.page.body),),
        base_head=wiki.repository.head,
        reason="test",
    )
    await projection.sync()
    assert index.store.items["topic"][0] == "Renamed topic"
    assert projection.last_state == WikiPageIndexState(wiki.repository.head, "ready")

    builder = WikiContextBuilder(wiki, projection, _fact_revision)
    resident = await builder.resident_context()
    retrieved = await builder.retrieval_context("tell me about this topic")
    assert resident is not None
    assert "Home" in resident and "Directives" in resident and "Profile" in resident
    assert retrieved is not None
    assert "Renamed topic" in retrieved
    assert "Role: common; freshness: stale" in retrieved
    assert "Home" not in retrieved


def test_wiki_context_uses_bounded_minimum_length_queries() -> None:
    assert _normalized_query("okay") is None
    assert _normalized_query("  THANK  YOU ") is None
    assert _normalized_query("details") is None
    assert _normalized_query("please explain the migration") == "please explain the migration"
    assert len(_normalized_query("x" * 1_000)) == 512


@pytest.mark.asyncio
async def test_malformed_page_is_omitted_without_breaking_valid_chat_context(tmp_path) -> None:
    wiki = WikiService(ManagedFileRepository(tmp_path / "pages", history_root=tmp_path / "history"))
    wiki.create_page(page_id="home", path="README.md", title="Home", body=b"Valid resident context.\n")
    wiki.repository.commit(
        ChangeSet(
            operations=(Create("broken", "broken.md", b"not valid wiki frontmatter"),),
            actor="test",
            origin="test",
            reason="inject malformed page",
            idempotency_key="inject-malformed-page",
            expected_head=wiki.repository.head,
        )
    )
    index = _Index()
    projection = WikiPageIndexProjection(wiki, lambda: index, _fact_revision)
    builder = WikiContextBuilder(wiki, projection, _fact_revision)

    await projection.sync()
    assert set(index.store.items) == {"home"}
    assert "Valid resident context" in (await builder.resident_context() or "")


@pytest.mark.asyncio
async def test_empty_canonical_wiki_has_no_resident_or_retrieval_context(tmp_path) -> None:
    wiki = WikiService(ManagedFileRepository(tmp_path / "pages", history_root=tmp_path / "history"))
    index = _Index()
    projection = WikiPageIndexProjection(wiki, lambda: index, _fact_revision)
    builder = WikiContextBuilder(wiki, projection, _fact_revision)

    assert await builder.resident_context() is None
    assert await builder.retrieval_context("tell me about the project") is None


def test_retrieval_is_after_the_cacheable_resident_block() -> None:
    blocks = build_system_blocks(
        source_details={},
        memory_context="## WIKI RESIDENT CONTEXT\nresident",
        retrieval_context="## RELEVANT WIKI PAGES\nretrieved",
        use_cache_control=True,
    )
    resident_index = next(index for index, block in enumerate(blocks) if block["text"].startswith("## WIKI CONTEXT"))
    retrieval_index = next(
        index for index, block in enumerate(blocks) if block["text"].startswith("## RELEVANT WIKI PAGES")
    )
    assert resident_index < retrieval_index
    assert blocks[resident_index]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in blocks[retrieval_index]


@pytest.mark.asyncio
async def test_wiki_filesystem_reads_run_off_the_event_loop(tmp_path, monkeypatch) -> None:
    wiki = WikiService(ManagedFileRepository(tmp_path / "pages", history_root=tmp_path / "history"))
    wiki.create_page(page_id="home", path="README.md", title="Home", body=b"Resident.\n")
    index = _Index()
    projection = WikiPageIndexProjection(wiki, lambda: index, _fact_revision)
    builder = WikiContextBuilder(wiki, projection, _fact_revision)
    event_loop_thread = threading.get_ident()
    read_threads: list[int] = []
    original = wiki.readable_pages

    def tracked_read():
        read_threads.append(threading.get_ident())
        return original()

    monkeypatch.setattr(wiki, "readable_pages", tracked_read)
    await builder.resident_context()
    await projection.sync()

    assert read_threads
    assert event_loop_thread not in read_threads


@pytest.mark.asyncio
async def test_concurrent_wiki_commit_retries_without_failing_chat(tmp_path, monkeypatch) -> None:
    wiki = WikiService(ManagedFileRepository(tmp_path / "pages", history_root=tmp_path / "history"))
    wiki.create_page(page_id="home", path="README.md", title="Home", body=b"Resident.\n")
    index = _Index()
    projection = WikiPageIndexProjection(wiki, lambda: index, _fact_revision)
    builder = WikiContextBuilder(wiki, projection, _fact_revision)
    original = wiki.readable_pages
    calls = 0

    def conflicted_once():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RevisionConflictError("wiki head changed")
        return original()

    monkeypatch.setattr(wiki, "readable_pages", conflicted_once)

    assert "Resident" in (await builder.resident_context() or "")
    assert calls == 2


@pytest.mark.asyncio
async def test_repeated_wiki_read_conflict_surfaces_after_one_retry(tmp_path, monkeypatch) -> None:
    wiki = WikiService(ManagedFileRepository(tmp_path / "pages", history_root=tmp_path / "history"))
    index = _Index()
    index.store.items["existing"] = ("Existing", "Preserve me.", {})
    projection = WikiPageIndexProjection(wiki, lambda: index, _fact_revision)
    builder = WikiContextBuilder(wiki, projection, _fact_revision)

    def always_conflicted():
        raise RevisionConflictError("wiki head keeps changing")

    monkeypatch.setattr(wiki, "readable_pages", always_conflicted)

    with pytest.raises(RevisionConflictError, match="wiki head keeps changing"):
        await builder.resident_context()
    with pytest.raises(RevisionConflictError, match="wiki head keeps changing"):
        await builder.retrieval_context("tell me about the current project")
    with pytest.raises(RevisionConflictError, match="wiki head keeps changing"):
        await projection.sync()
    assert set(index.store.items) == {"existing"}
    assert projection.last_state == WikiPageIndexState(
        None,
        "error",
        "RevisionConflictError: wiki head keeps changing",
    )


@pytest.mark.asyncio
async def test_unexpected_wiki_read_failure_surfaces_and_records_error_state(tmp_path, monkeypatch) -> None:
    wiki = WikiService(ManagedFileRepository(tmp_path / "pages", history_root=tmp_path / "history"))
    index = _Index()
    projection = WikiPageIndexProjection(wiki, lambda: index, _fact_revision)
    builder = WikiContextBuilder(wiki, projection, _fact_revision)

    def broken_read():
        raise OSError("wiki storage is unavailable")

    monkeypatch.setattr(wiki, "readable_pages", broken_read)

    with pytest.raises(OSError, match="wiki storage is unavailable"):
        await builder.resident_context()
    with pytest.raises(OSError, match="wiki storage is unavailable"):
        await projection.sync()
    assert projection.last_state == WikiPageIndexState(None, "error", "OSError: wiki storage is unavailable")


@pytest.mark.asyncio
async def test_projection_retries_one_wiki_head_change_before_marking_the_index_ready(tmp_path) -> None:
    wiki = WikiService(ManagedFileRepository(tmp_path / "pages", history_root=tmp_path / "history"))
    wiki.create_page(page_id="first", path="first.md", title="First", body=b"First page.\n")
    writes = 0

    class _HeadChangingIndex(_Index):
        async def upsert(self, *args):
            nonlocal writes
            await super().upsert(*args)
            writes += 1
            if writes == 1:
                wiki.create_page(page_id="second", path="second.md", title="Second", body=b"Second page.\n")

    index = _HeadChangingIndex()
    projection = WikiPageIndexProjection(wiki, lambda: index, _fact_revision)

    assert await projection.sync() is index
    assert writes >= 3
    assert set(index.store.items) == {"first", "second"}
    assert projection.last_state == WikiPageIndexState(wiki.repository.head, "ready")


@pytest.mark.asyncio
async def test_projection_never_marks_a_repeatedly_changing_wiki_head_current(tmp_path) -> None:
    wiki = WikiService(ManagedFileRepository(tmp_path / "pages", history_root=tmp_path / "history"))
    wiki.create_page(page_id="first", path="first.md", title="First", body=b"First page.\n")
    writes = 0

    class _AlwaysChangingIndex(_Index):
        async def upsert(self, *args):
            nonlocal writes
            await super().upsert(*args)
            writes += 1
            wiki.create_page(
                page_id=f"concurrent-{writes}",
                path=f"concurrent-{writes}.md",
                title=f"Concurrent {writes}",
                body=b"Changed during index work.\n",
            )

    index = _AlwaysChangingIndex()
    projection = WikiPageIndexProjection(wiki, lambda: index, _fact_revision)

    assert await projection.sync() is None
    assert writes >= 2
    assert projection.last_state == WikiPageIndexState(None, "not_ready", "wiki head changed during index sync")


@pytest.mark.asyncio
async def test_projection_state_callback_is_outside_the_lock_and_propagates_failure(tmp_path) -> None:
    wiki = WikiService(ManagedFileRepository(tmp_path / "pages", history_root=tmp_path / "history"))
    wiki.create_page(page_id="topic", path="topic.md", title="Topic", body=b"Callback-safe topic.\n")
    index = _Index()
    states: list[WikiPageIndexState] = []
    lock_states: list[bool] = []
    projection: WikiPageIndexProjection

    async def callback(state: WikiPageIndexState) -> None:
        states.append(state)
        lock_states.append(projection._lock.locked())
        raise RuntimeError("health consumer is unavailable")

    projection = WikiPageIndexProjection(wiki, lambda: index, _fact_revision, on_state_change=callback)

    with pytest.raises(RuntimeError, match="health consumer is unavailable"):
        await projection.sync()
    assert await projection.sync() is index
    assert states == [WikiPageIndexState(wiki.repository.head, "ready")]
    assert lock_states == [False]


@pytest.mark.asyncio
async def test_projection_uses_the_current_search_index_after_runtime_rebind(tmp_path) -> None:
    wiki = WikiService(ManagedFileRepository(tmp_path / "pages", history_root=tmp_path / "history"))
    wiki.create_page(page_id="topic", path="topic.md", title="Topic", body=b"Useful topic context.\n")
    first = _Index()
    second = _Index()
    current = {"index": first}
    projection = WikiPageIndexProjection(wiki, lambda: current["index"], _fact_revision)

    await projection.sync()
    current["index"] = second
    await projection.sync()

    assert set(first.store.items) == {"topic"}
    assert set(second.store.items) == {"topic"}


@pytest.mark.asyncio
async def test_resident_search_hits_cannot_starve_dynamic_context(tmp_path) -> None:
    wiki = WikiService(ManagedFileRepository(tmp_path / "pages", history_root=tmp_path / "history"))
    for page_id, path in (("home", "README.md"), ("directives", "directives.md"), ("me", "me.md")):
        wiki.create_page(page_id=page_id, path=path, title=page_id.title(), body=b"Resident material.\n")
    wiki.create_page(page_id="topic", path="topic.md", title="Topic", body=b"Relevant ordinary material.\n")

    class _ResidentFirstIndex(_Index):
        async def search(self, _query, *, sources, limit):
            assert sources == [WIKI_PAGE_SOURCE]
            assert limit >= 6
            return [
                SearchResult(source=WIKI_PAGE_SOURCE, source_id=page_id, title=page_id, snippet=page_id)
                for page_id in ("home", "directives", "me", "topic")
            ][:limit]

    index = _ResidentFirstIndex()
    projection = WikiPageIndexProjection(wiki, lambda: index, _fact_revision)
    context = await WikiContextBuilder(wiki, projection, _fact_revision).retrieval_context(
        "tell me about the relevant ordinary material"
    )

    assert context is not None
    assert "Relevant ordinary material" in context
    assert "Resident material" not in context


@pytest.mark.asyncio
async def test_concurrent_search_index_reload_retries_without_failing_chat(tmp_path) -> None:
    wiki = WikiService(ManagedFileRepository(tmp_path / "pages", history_root=tmp_path / "history"))
    wiki.create_page(page_id="topic", path="topic.md", title="Topic", body=b"Reload-safe topic material.\n")
    current: dict[str, _Index]
    replacement = _Index()

    class _ClosingIndex(_Index):
        async def search(self, _query, *, sources, limit):
            current["index"] = replacement
            raise RuntimeError("old search database closed during config reload")

    current = {"index": _ClosingIndex()}
    projection = WikiPageIndexProjection(wiki, lambda: current["index"], _fact_revision)
    context = await WikiContextBuilder(wiki, projection, _fact_revision).retrieval_context(
        "tell me about reload safe topic material"
    )

    assert context is not None
    assert "Reload-safe topic material" in context
    assert set(replacement.store.items) == {"topic"}


@pytest.mark.asyncio
async def test_search_failure_surfaces_instead_of_becoming_empty_context(tmp_path) -> None:
    wiki = WikiService(ManagedFileRepository(tmp_path / "pages", history_root=tmp_path / "history"))
    wiki.create_page(page_id="topic", path="topic.md", title="Topic", body=b"Search failure evidence.\n")

    class _BrokenSearchIndex(_Index):
        async def search(self, _query, *, sources, limit):
            del sources, limit
            raise RuntimeError("search backend is unavailable")

    index = _BrokenSearchIndex()
    projection = WikiPageIndexProjection(wiki, lambda: index, _fact_revision)
    builder = WikiContextBuilder(wiki, projection, _fact_revision)

    with pytest.raises(RuntimeError, match="search backend is unavailable"):
        await builder.retrieval_context("tell me about the search failure evidence")
