from pathlib import Path

import pytest

from arden.revisions import ManagedFileRepository
from arden.revisions.errors import RevisionConflictError
from arden.wiki.navigation.projection import WikiNavigationError, WikiNavigationProjection
from arden.wiki.navigation.store import WikiNavigationStore
from arden.wiki.pages import extract_generated_region, update_generated_region
from arden.wiki.service import GeneratedRegionConflictError, WikiService


async def _projection(tmp_path: Path) -> tuple[WikiService, WikiNavigationStore, WikiNavigationProjection]:
    wiki = WikiService(ManagedFileRepository(tmp_path / "pages", history_root=tmp_path / "history"))
    store = await WikiNavigationStore.open(tmp_path / "state.db")
    return wiki, store, WikiNavigationProjection(wiki, store)


async def _close(store: WikiNavigationStore) -> None:
    await store.close()


@pytest.mark.asyncio
async def test_navigation_creates_the_required_root_readme_in_an_empty_wiki(tmp_path: Path) -> None:
    wiki, store, projection = await _projection(tmp_path)
    try:
        result = await projection.run()

        assert result.published and result.advanced and result.readme_count == 1
        root = next(page for page in wiki.list_pages() if page.resource.path == "README.md")
        assert extract_generated_region(root.content, expected_page_id=root.page.page_id) == (
            b"## Navigation\n\n- _No managed pages._\n"
        )
    finally:
        await _close(store)


@pytest.mark.asyncio
async def test_navigation_creates_root_and_nested_readmes_with_direct_children_only(tmp_path: Path) -> None:
    wiki, store, projection = await _projection(tmp_path)
    try:
        wiki.create_page(page_id="root", path="root.md", title="Root")
        wiki.create_page(page_id="project", path="projects/alpha.md", title="Alpha")
        wiki.create_page(page_id="deep", path="projects/deep/beta.md", title="Beta")
        wiki.create_page(page_id="note", path="notes/note.md", title="Note")

        result = await projection.run()

        assert result.published and result.advanced and result.readme_count == 4
        root = next(page for page in wiki.list_pages() if page.resource.path == "README.md")
        projects = next(page for page in wiki.list_pages() if page.resource.path == "projects/README.md")
        assert extract_generated_region(root.content, expected_page_id=root.page.page_id) == (
            b"## Navigation\n\n- [[notes/README|notes]]\n- [[projects/README|projects]]\n- [[root|Root]]\n"
        )
        assert extract_generated_region(projects.content, expected_page_id=projects.page.page_id) == (
            b"## Navigation\n\n- [[projects/deep/README|projects/deep]]\n- [[projects/alpha|Alpha]]\n"
        )
        assert len(wiki.repository.history(limit=1)[0].changes) == 4
        assert (await store.get()).revision == result.checkpoint
    finally:
        await _close(store)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "title"),
    [
        ("topics/a#b.md", "Safe"),
        ("topics/safe.md", "A]] [[B"),
    ],
)
async def test_navigation_rejects_page_names_that_cannot_form_one_wikilink(
    tmp_path: Path,
    path: str,
    title: str,
) -> None:
    wiki, store, projection = await _projection(tmp_path)
    try:
        wiki.create_page(page_id="unsafe", path=path, title=title)

        with pytest.raises(WikiNavigationError, match="cannot be represented as a wikilink"):
            await projection.run()

        assert await store.get() is None
    finally:
        await _close(store)


@pytest.mark.asyncio
async def test_navigation_preserves_existing_readme_identity_metadata_and_user_notes(tmp_path: Path) -> None:
    wiki, store, projection = await _projection(tmp_path)
    try:
        existing = wiki.create_page(
            page_id="home",
            path="README.md",
            title="My Home",
            aliases=("Start",),
            body=b"## User notes\nKeep this.\n",
            metadata={"color": "blue"},
        )
        wiki.create_page(page_id="one", path="one.md", title="One")

        await projection.run()

        root = wiki.read_page("home")
        assert root.page.page_id == existing.page.page_id
        assert root.page.title == "My Home"
        assert root.page.aliases == ("Start",)
        assert root.page.lifecycle == "active"
        assert root.page.metadata["color"] == "blue"
        assert b"## User notes\nKeep this.\n" in root.content
        assert extract_generated_region(root.content, expected_page_id="home") == b"## Navigation\n\n- [[one|One]]\n"
    finally:
        await _close(store)


@pytest.mark.asyncio
async def test_navigation_converges_after_move_and_archive_without_stale_folder_links(tmp_path: Path) -> None:
    wiki, store, projection = await _projection(tmp_path)
    try:
        created = wiki.create_page(page_id="one", path="projects/one.md", title="One")
        await projection.run()
        move = wiki.prepare_rename(
            "one",
            new_path="archive/one.md",
            new_title="Archived One",
            expected_version=created.resource.version_id,
            base_head=wiki.repository.head,
        )
        wiki.apply_rename(move)

        await projection.run()
        root = next(page for page in wiki.list_pages() if page.resource.path == "README.md")
        assert extract_generated_region(root.content, expected_page_id=root.page.page_id) == (
            b"## Navigation\n\n- [[archive/README|archive]]\n"
        )

        temporary = wiki.create_page(page_id="temporary", path="scratch/two.md", title="Temporary")
        await projection.run()
        wiki.archive_page(
            "temporary",
            expected_version=temporary.resource.version_id,
            base_head=wiki.repository.head,
        )
        await projection.run()
        root = next(page for page in wiki.list_pages() if page.resource.path == "README.md")
        assert (
            extract_generated_region(root.content, expected_page_id=root.page.page_id)
            == b"## Navigation\n\n- [[archive/README|archive]]\n"
        )
        scratch = next(page for page in wiki.list_pages() if page.resource.path == "scratch/README.md")
        assert extract_generated_region(scratch.content, expected_page_id=scratch.page.page_id) == (
            b"## Navigation\n\n- _No managed pages._\n"
        )
        assert wiki.link_report(scratch.page.page_id).outgoing == ()
    finally:
        await _close(store)


@pytest.mark.asyncio
async def test_navigation_noops_once_its_output_commit_is_checkpointed(tmp_path: Path) -> None:
    wiki, store, projection = await _projection(tmp_path)
    try:
        wiki.create_page(page_id="one", path="one.md", title="One")
        first = await projection.run()
        head = wiki.repository.head

        second = await projection.run()

        assert first.published and first.advanced
        assert not second.published and not second.advanced
        assert wiki.repository.head == head
        assert (await store.get()).revision == head
    finally:
        await _close(store)


@pytest.mark.asyncio
async def test_navigation_replays_publication_after_checkpoint_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki, store, projection = await _projection(tmp_path)
    try:
        wiki.create_page(page_id="one", path="one.md", title="One")
        original_advance = store.advance

        async def fail_advance(*, expected_revision: str | None, revision: str):
            raise RuntimeError("checkpoint unavailable")

        monkeypatch.setattr(store, "advance", fail_advance)
        with pytest.raises(RuntimeError, match="checkpoint unavailable"):
            await projection.run()
        published_head = wiki.repository.head
        assert published_head is not None
        assert await store.get() is None

        monkeypatch.setattr(store, "advance", original_advance)
        replay = await projection.run()

        assert not replay.published and replay.advanced
        assert replay.checkpoint == published_head
        assert (await store.get()).revision == published_head
    finally:
        await _close(store)


@pytest.mark.asyncio
async def test_navigation_rejects_user_edit_to_its_generated_region_without_advancing(tmp_path: Path) -> None:
    wiki, store, projection = await _projection(tmp_path)
    try:
        wiki.create_page(page_id="one", path="one.md", title="One")
        await projection.run()
        root = next(page for page in wiki.list_pages() if page.resource.path == "README.md")
        edited = update_generated_region(
            root.content,
            expected_page_id=root.page.page_id,
            generated=b"## Navigation\n\n- [[one|User override]]\n",
        )
        wiki.update_page(
            root.page.page_id,
            content=edited,
            expected_version=root.resource.version_id,
            expected_head=wiki.repository.head,
        )
        one = wiki.read_page("one")
        rename = wiki.prepare_rename(
            "one",
            new_path="renamed.md",
            new_title="Renamed",
            expected_version=one.resource.version_id,
            base_head=wiki.repository.head,
        )
        wiki.apply_rename(rename)
        before = wiki.repository.head
        checkpoint = (await store.get()).revision

        with pytest.raises(GeneratedRegionConflictError, match="generated region changed by a user"):
            await projection.run()

        assert wiki.repository.head == before
        assert (await store.get()).revision == checkpoint
    finally:
        await _close(store)


@pytest.mark.asyncio
async def test_navigation_does_not_advance_after_a_concurrent_wiki_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    wiki, store, projection = await _projection(tmp_path)
    try:
        wiki.create_page(page_id="one", path="one.md", title="One")
        original_publish = wiki.publish_generated

        def publish_after_concurrent_write(*args, **kwargs):
            wiki.create_page(page_id="other", path="other.md", title="Other")
            return original_publish(*args, **kwargs)

        monkeypatch.setattr(wiki, "publish_generated", publish_after_concurrent_write)
        with pytest.raises(RevisionConflictError, match="current head changed"):
            await projection.run()

        assert await store.get() is None
    finally:
        await _close(store)
