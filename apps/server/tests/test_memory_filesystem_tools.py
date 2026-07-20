"""Memory filesystem tools over the ArtifactMemoryStore projection."""

import os
import types
from pathlib import Path

import pytest

import ntrp.tools.memory as memory_tools
from ntrp.integrations.core import MEMORY
from ntrp.memory.records import RecordStore
from ntrp.tools.core.file_mutation import file_revision
from ntrp.tools.core.registry import ToolRegistry
from ntrp.tools.memory import (
    MEMORY_RECONCILER_SERVICE,
    MEMORY_RECORDS_SERVICE,
    MemoryPatchInput,
    MemoryReadInput,
    MemorySearchInput,
    MemoryTreeInput,
    approve_memory_patch,
    memory_patch,
    memory_read,
    memory_search,
    memory_tree,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
def store(tmp_path: Path) -> RecordStore:
    return RecordStore(tmp_path / "memory.db", search_index=None)


@pytest.fixture
def artifacts_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "memory"
    monkeypatch.setattr(
        memory_tools,
        "get_config",
        lambda: types.SimpleNamespace(
            memory_artifacts_dir=root, memory_db_path=tmp_path / "memory.db", memory_model=None
        ),
    )
    return root


def _execution(store=None):
    services = {MEMORY_RECORDS_SERVICE: store} if store is not None else {}
    return types.SimpleNamespace(
        ctx=types.SimpleNamespace(services=services, session_id="s1", area=None),
        tool_id="t1",
        tool_name="memory",
    )


async def _seed(artifacts_dir: Path):
    """Write a few real markdown pages the live read tools resolve against (the old
    record->projection export is gone; pages ARE canonical)."""
    a = memory_tools.ArtifactMemoryStore(artifacts_dir)
    a.ensure_dirs()
    a._write("me.md", "Profile", "topic", "global", None, "# Profile\n\nThe user prefers oolong tea.\n", 1)
    a._write("directives.md", "Directives", "directive", "global", None,
             "# Directives\n\n- keep projection docs concise\n", 1)
    a._write("topics/dex.md", "Dex", "topic", "entity", "dex",
             "# Dex\n\nDex is the user's area; see [[Profile]].\n", 1)
    return list(artifacts_dir.rglob("*.md"))


def _symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")


def _fifo_or_skip(path: Path) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("FIFOs unavailable")
    try:
        os.mkfifo(path)
    except OSError as exc:
        pytest.skip(f"FIFOs unavailable: {exc}")


def _changed_paths(changes) -> list[str]:
    return [getattr(change, "path", change) for change in changes]


async def test_memory_filesystem_tools_registered_and_permission_gated():
    registry = ToolRegistry()
    for name, tool in MEMORY.tools.items():
        registry.register(name, tool, source="_memory")

    names_without = {s["function"]["name"] for s in registry.get_schemas(capabilities=frozenset())}
    names_with = {s["function"]["name"] for s in registry.get_schemas(capabilities=frozenset({MEMORY_RECORDS_SERVICE}))}
    names_with_reconciler = {
        s["function"]["name"]
        for s in registry.get_schemas(
            capabilities=frozenset({MEMORY_RECORDS_SERVICE, MEMORY_RECONCILER_SERVICE})
        )
    }

    expected = {"memory_tree", "memory_read", "memory_search", "memory_patch"}
    assert expected.isdisjoint(names_without)
    assert expected.issubset(names_with)
    assert "remember" not in names_with
    assert "remember" in names_with_reconciler
    assert MEMORY.tools["memory_patch"].policy.requires_approval is True


async def test_memory_tree_read_and_search_use_artifact_store_safety(store: RecordStore, artifacts_dir: Path):
    await _seed(artifacts_dir)
    execution = _execution(store)

    tree = await memory_tree(execution, MemoryTreeInput(depth=3))
    assert not tree.is_error
    assert "me.md" in tree.content
    assert tree.data["artifacts"]

    read = await memory_read(execution, MemoryReadInput(path="me.md", offset=1, limit=5))
    assert not read.is_error
    assert "oolong" in read.content.lower()
    assert read.data["path"] == "me.md"
    assert read.data["sha256"] == file_revision(artifacts_dir / "me.md").sha256

    search = await memory_search(execution, MemorySearchInput(query="concise", limit=10))
    assert not search.is_error
    assert search.data["matches"]


async def test_memory_read_search_and_tree_open_nested_text_outside_engine_namespaces(
    store: RecordStore, artifacts_dir: Path
):
    await _seed(artifacts_dir)
    (artifacts_dir / "research" / "models").mkdir(parents=True)
    (artifacts_dir / "research" / "models" / "results.txt").write_text("Exact benchmark result.\n", encoding="utf-8")
    (artifacts_dir / "raw").mkdir(exist_ok=True)
    (artifacts_dir / "raw" / "secret.txt").write_text("Exact private raw result.\n", encoding="utf-8")
    (artifacts_dir / ".research").mkdir()
    (artifacts_dir / ".research" / ".result.txt").write_text("Exact hidden benchmark.\n", encoding="utf-8")

    execution = _execution(store)
    read = await memory_read(execution, MemoryReadInput(path="research/models/results.txt"))
    search = await memory_search(execution, MemorySearchInput(query="benchmark", path="research"))
    tree = await memory_tree(execution, MemoryTreeInput(path="research", depth=3))
    hidden = await memory_read(execution, MemoryReadInput(path="raw/secret.txt"))
    user_hidden = await memory_read(execution, MemoryReadInput(path=".research/.result.txt"))
    hidden_search = await memory_search(execution, MemorySearchInput(query="hidden benchmark", path=".research"))

    assert not read.is_error and "benchmark" in read.content
    assert not search.is_error and search.data["matches"][0]["path"] == "research/models/results.txt"
    assert not tree.is_error and "results.txt" in tree.content
    assert hidden.is_error
    assert not user_hidden.is_error and "hidden benchmark" in user_hidden.content
    assert not hidden_search.is_error and hidden_search.data["matches"][0]["path"] == ".research/.result.txt"


async def test_memory_read_resolves_titles_directories_and_wikilinks(store: RecordStore, artifacts_dir: Path):
    await _seed(artifacts_dir)
    execution = _execution(store)

    by_title = await memory_read(execution, MemoryReadInput(path="Dex", offset=1, limit=3))
    assert not by_title.is_error
    assert by_title.data["path"] == "topics/dex.md"

    by_stem = await memory_read(execution, MemoryReadInput(path="dex", offset=1, limit=3))
    assert not by_stem.is_error
    assert by_stem.data["path"] == "topics/dex.md"

    by_wikilink = await memory_read(execution, MemoryReadInput(path="[[Profile]]", offset=1, limit=3))
    assert not by_wikilink.is_error
    assert by_wikilink.data["path"] == "me.md"

    artifact_store = memory_tools.ArtifactMemoryStore(artifacts_dir)
    artifact_store._write("entities/dex.md", "Dex", "topic", "entity", "dex", "# Dex\n\nEntity page.\n", 1)
    artifact_store._write("projects/dex.md", "Dex", "topic", "project", "dex", "# Dex\n\nProject page.\n", 1)
    duplicate_title = await memory_read(execution, MemoryReadInput(path="Dex", offset=1, limit=3))
    assert not duplicate_title.is_error
    assert duplicate_title.data["path"] == "topics/dex.md"  # unified topics/ wins over legacy

    artifact_store._write("entities/foo_bar.md", "Foo_bar", "topic", "entity", "foo_bar", "# Foo_bar\n\nEntity page.\n", 1)
    artifact_store._write("projects/foo_bar.md", "Foo_bar", "topic", "project", "foo_bar", "# Foo_bar\n\nProject page.\n", 1)
    underscore_title = await memory_read(execution, MemoryReadInput(path="Foo_bar", offset=1, limit=3))
    assert not underscore_title.is_error
    assert underscore_title.data["path"] == "entities/foo_bar.md"

    artifact_store._write("entities/a.b.md", "A.B", "topic", "entity", "a.b", "# A.B\n\nEntity page.\n", 1)
    artifact_store._write("projects/a.b.md", "A.B", "topic", "project", "a.b", "# A.B\n\nProject page.\n", 1)
    dotted_title = await memory_read(execution, MemoryReadInput(path="A.B", offset=1, limit=3))
    assert not dotted_title.is_error
    assert dotted_title.data["path"] == "entities/a.b.md"


@pytest.mark.parametrize("bad_path", ["/tmp/me.md", "../me.md", ".secret/file.md", "README.png"])
async def test_memory_read_search_patch_reject_bad_paths(store: RecordStore, artifacts_dir: Path, bad_path: str):
    await _seed(artifacts_dir)
    execution = _execution(store)

    assert (await memory_read(execution, MemoryReadInput(path=bad_path))).is_error
    assert (await memory_search(execution, MemorySearchInput(query="x", path=bad_path))).is_error
    assert (
        await memory_patch(
            execution,
            MemoryPatchInput(
                path=bad_path,
                old_text="x",
                new_text="y",
                force_generated=True,
                expected_sha256="absent",
            ),
        )
    ).is_error


async def test_memory_tools_reject_symlink_and_fifo(store: RecordStore, artifacts_dir: Path):
    await _seed(artifacts_dir)
    outside = artifacts_dir.parent / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    readme = artifacts_dir / "me.md"
    readme.unlink()
    _symlink_or_skip(readme, outside)
    execution = _execution(store)

    assert (await memory_read(execution, MemoryReadInput(path="me.md"))).is_error
    assert (await memory_search(execution, MemorySearchInput(query="outside", path="me.md"))).is_error
    assert (
        await memory_patch(
            execution,
            MemoryPatchInput(
                path="me.md",
                old_text="outside",
                new_text="inside",
                force_generated=True,
                expected_sha256="absent",
            ),
        )
    ).is_error

    readme.unlink()
    _fifo_or_skip(readme)
    assert (await memory_read(execution, MemoryReadInput(path="me.md"))).is_error


async def test_memory_patch_refuses_generated_without_force_and_force_patch_audits(store: RecordStore, artifacts_dir: Path):
    """Canonical pages (me.md, topics/) patch freely; generated reports (health.md)
    refuse without force_generated."""
    await _seed(artifacts_dir)
    execution = _execution(store)
    store_obj = memory_tools.ArtifactMemoryStore(artifacts_dir)
    store_obj._write("health.md", "Health & gaps", "topic", "global", None, "# Memory health\n\nAll good.\n", None)
    old = store_obj.read_artifact("health.md").content.splitlines()[0]
    health_revision = file_revision(artifacts_dir / "health.md").sha256

    refused = await memory_patch(execution, MemoryPatchInput(path="health.md", old_text=old, new_text="# Patched memory", force_generated=False, expected_sha256=health_revision))
    assert refused.is_error
    assert "Refusing to edit generated" in refused.content

    approval = await approve_memory_patch(execution, MemoryPatchInput(path="health.md", old_text=old, new_text="# Patched memory", force_generated=True, expected_sha256=health_revision))
    assert approval is not None
    assert approval.description == "Force edit generated memory artifact health.md"
    assert "-" in (approval.diff or "") and "+" in (approval.diff or "")

    patched = await memory_patch(execution, MemoryPatchInput(path="health.md", old_text=old, new_text="# Patched memory", force_generated=True, expected_sha256=health_revision))
    assert not patched.is_error
    assert store_obj.read_artifact("health.md").content.startswith("# Patched memory")
    changelog = "\n".join(p.read_text(encoding="utf-8") for p in (artifacts_dir / "changelog").glob("**/*.md"))
    assert "memory filesystem patched" not in changelog  # maintenance edits aren't logged as changelog noise

    # canonical prose pages are directly editable — no force needed
    me_old = store_obj.read_artifact("me.md").content.splitlines()[0]
    edited = await memory_patch(execution, MemoryPatchInput(path="me.md", old_text=me_old, new_text="# Me, edited", force_generated=False, expected_sha256=file_revision(artifacts_dir / "me.md").sha256))
    assert not edited.is_error


async def test_memory_patch_requires_unique_old_text(store: RecordStore, artifacts_dir: Path):
    await _seed(artifacts_dir)
    path = artifacts_dir / "me.md"
    path.write_text("# A\nrepeat\nrepeat\n", encoding="utf-8")

    result = await memory_patch(_execution(store), MemoryPatchInput(path="me.md", old_text="repeat", new_text="once", force_generated=True, expected_sha256=file_revision(path).sha256))

    assert result.is_error
    assert result.preview == "Ambiguous"


async def test_memory_patch_rejects_change_after_approval(store: RecordStore, artifacts_dir: Path):
    await _seed(artifacts_dir)
    path = artifacts_dir / "me.md"
    expected = file_revision(path).sha256
    old_text = "oolong"
    execution = _execution(store)

    approval = await approve_memory_patch(
        execution,
        MemoryPatchInput(
            path="me.md",
            old_text=old_text,
            new_text="sencha",
            expected_sha256=expected,
        ),
    )
    path.write_text(path.read_text(encoding="utf-8") + "\nExternal edit.\n", encoding="utf-8")
    result = await memory_patch(
        execution,
        MemoryPatchInput(
            path="me.md",
            old_text=old_text,
            new_text="sencha",
            expected_sha256=expected,
        ),
    )

    assert approval is not None and expected in (approval.diff or "")
    assert result.is_error
    assert result.outcome is not None and result.outcome.error is not None
    assert result.outcome.error.code == "write_conflict"
    assert "External edit." in path.read_text(encoding="utf-8")


async def test_memory_write_creates_and_updates_feed_pages(store: RecordStore, artifacts_dir: Path):
    from ntrp.tools.memory import MemoryWriteInput, approve_memory_write, memory_write

    await _seed(artifacts_dir)
    execution = _execution(store)

    approval = await approve_memory_write(execution, MemoryWriteInput(path="feeds/pr-queue.md", content="# PR queue\n\n- none open", expected_sha256="absent"))
    assert approval is not None and approval.description == "Create memory page feeds/pr-queue.md"

    created = await memory_write(execution, MemoryWriteInput(path="feeds/pr-queue.md", content="# PR queue\n\n- none open", expected_sha256="absent"))
    assert not created.is_error
    assert created.outcome is not None and created.outcome.effect is not None
    assert created.outcome.effect.before_ref == "absent"
    assert created.outcome.effect.after_ref == created.data["sha256"]
    assert (artifacts_dir / "feeds" / "pr-queue.md").read_text(encoding="utf-8").startswith("# PR queue")

    updated = await memory_write(execution, MemoryWriteInput(path="feeds/pr-queue.md", content="# PR queue\n\n- 2 open", expected_sha256=created.data["sha256"]))
    assert not updated.is_error and updated.preview == "Updated"
    assert "2 open" in (artifacts_dir / "feeds" / "pr-queue.md").read_text(encoding="utf-8")


async def test_memory_write_refuses_record_backed_generated_and_bad_paths(store: RecordStore, artifacts_dir: Path):
    from ntrp.tools.memory import MemoryWriteInput, memory_write

    await _seed(artifacts_dir)
    execution = _execution(store)

    # record-backed page (raw/ sidecar exists) -> compiled prose, refuse whole-page write
    (artifacts_dir / "raw").mkdir(parents=True, exist_ok=True)
    (artifacts_dir / "raw" / "me.md").write_text("- 2026-07-02 ^aaaa1111 [fact] (src:user) x\n", encoding="utf-8")
    refused = await memory_write(execution, MemoryWriteInput(path="me.md", content="# clobber", expected_sha256="absent"))
    assert refused.is_error and "record-backed" in refused.content

    # generated report
    store_obj = memory_tools.ArtifactMemoryStore(artifacts_dir)
    store_obj._write("health.md", "Health", "topic", "global", None, "# Memory health\n", None)
    gen = await memory_write(execution, MemoryWriteInput(path="health.md", content="# nope", expected_sha256="absent"))
    assert gen.is_error

    # path escapes / disallowed dirs
    for bad in ("../evil.md", "raw/me.md", "changelog/2026/2026-07.md", "newroot.md"):
        result = await memory_write(execution, MemoryWriteInput(path=bad, content="x", expected_sha256="absent"))
        assert result.is_error, bad


async def test_live_vault_absorbs_external_edits(tmp_path: Path):
    """Obsidian-for-ntrp: an on-disk edit (new page, prose edit, raw timeline
    change, deletion) is absorbed by refresh_from_disk without a restart; the
    store's own writes never read as external changes."""
    from ntrp.memory.file_store import FilePageStore
    from ntrp.memory.models import SourceRef

    root = tmp_path / "memory"
    store = FilePageStore(root)
    await store.open()

    # own writes are echo-suppressed
    rec = await store.add("Tim rides a gravel bike.", kind="fact", source_ref=SourceRef("user", ""))
    own = await store.refresh_from_disk()
    assert _changed_paths(own) == ["me.md"]
    assert own[0].origin == "synthesis"

    # external prose edit on an existing page is absorbed
    me = root / "me.md"
    me.write_text(me.read_text(encoding="utf-8") + "\nEdited in Obsidian.\n", encoding="utf-8")
    changed = await store.refresh_from_disk()
    assert _changed_paths(changed) == ["me.md"], changed
    assert "Edited in Obsidian." in store._pages[me].prose
    assert (await store.get(rec.id)) is not None, "timeline survives a prose edit"
    store.acknowledge_observed_change(changed[0])

    # external new page (a feed write) appears
    feed = root / "feeds" / "pr-queue.md"
    feed.parent.mkdir(parents=True, exist_ok=True)
    feed.write_text("# PR queue\n\n- none open\n", encoding="utf-8")
    feed_changes = await store.refresh_from_disk()
    assert _changed_paths(feed_changes) == ["feeds/", "feeds/pr-queue.md"]
    store.acknowledge_observed_change(next(change for change in feed_changes if hasattr(change, "after")))
    assert feed not in store._pages, "visible-only resources never become record pages"

    # external raw-sidecar edit reloads the page's records
    raw_me = root / "raw" / "me.md"
    raw_text = raw_me.read_text(encoding="utf-8")
    raw_me.write_text(raw_text + "- 2026-07-03 ^ee11ff22 [fact] (src:user) Added via git.\n", encoding="utf-8")
    assert await store.refresh_from_disk() == ["me.md"]
    assert (await store.get("ee11ff22")) is not None

    # deletion drops the page + records
    feed.unlink()
    assert _changed_paths(await store.refresh_from_disk()) == ["feeds/", "feeds/pr-queue.md"]
    assert feed not in store._pages
    await store.close()


async def test_visible_resource_record_syntax_never_enters_canonical_store(tmp_path: Path):
    from ntrp.memory.file_store import FilePageStore
    from ntrp.memory.pages import SENTINEL

    root = tmp_path / "memory"
    (root / "raw").mkdir(parents=True)
    (root / "raw" / "me.md").write_text("<!-- ntrp:records schema=2 page=me.md -->\n", encoding="utf-8")
    (root / "raw" / ".ntrp").mkdir()
    (root / "raw" / ".ntrp" / "secret.md").write_text(
        "- 2026-07-12 ^engine1 [fact] (src:user) Must remain internal.\n",
        encoding="utf-8",
    )
    resource = root / "research" / "fake.md"
    resource.parent.mkdir()
    resource.write_text(
        f"# Resource\n\n{SENTINEL}\n- 2026-07-12 ^escape1 [fact] (src:user) Must remain resource-only.\n",
        encoding="utf-8",
    )

    store = FilePageStore(root)
    await store.open()

    assert resource not in store._pages
    assert "escape1" not in store._loc
    assert "engine1" not in store._loc
    await store.close()


async def test_live_store_rejects_raw_and_visible_symlink_escapes(tmp_path: Path):
    from ntrp.memory.file_store import FilePageStore

    root = tmp_path / "memory"
    outside = tmp_path / "outside"
    (root / "raw").mkdir(parents=True)
    outside.mkdir()
    (outside / "evil.md").write_text("- 2026-07-12 ^escape2 [fact] (src:user) Outside.\n", encoding="utf-8")
    _symlink_or_skip(root / "raw" / "evil.md", outside / "evil.md")
    (root / "raw" / "me.md").write_text("<!-- ntrp:records schema=2 page=me.md -->\n", encoding="utf-8")
    (outside / "visible.md").write_text("# Outside visible\n", encoding="utf-8")
    _symlink_or_skip(root / "me.md", outside / "visible.md")

    store = FilePageStore(root)
    await store.open()

    assert root / "evil.md" not in store._pages
    assert "escape2" not in store._loc
    assert root / "me.md" in store._pages
    assert "Outside visible" not in store._pages[root / "me.md"].prose
    assert root / "me.md" not in store._file_state
    await store.close()


async def test_text_resource_create_move_delete_is_watched_without_record_reload(tmp_path: Path):
    from ntrp.memory.file_store import FilePageStore

    root = tmp_path / "memory"
    store = FilePageStore(root)
    await store.open()
    first = root / ".research" / "notes.txt"
    first.parent.mkdir()
    first.write_text("first\n", encoding="utf-8")

    assert await store.refresh_from_disk() == [".research/", ".research/notes.txt"]
    assert first not in store._pages

    moved = first.with_name("moved.txt")
    first.rename(moved)
    assert await store.refresh_from_disk() == [".research/", ".research/moved.txt", ".research/notes.txt"]
    assert moved not in store._pages

    moved.unlink()
    assert await store.refresh_from_disk() == [".research/", ".research/moved.txt"]
    await store.close()


async def test_empty_and_readme_only_directory_lifecycle_is_watched_without_record_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    from ntrp.memory.file_store import FilePageStore

    root = tmp_path / "memory"
    store = FilePageStore(root)
    await store.open()
    reloaded: list[Path] = []
    monkeypatch.setattr(store, "_reload_page", reloaded.append)

    empty = root / "empty"
    empty.mkdir()
    assert await store.refresh_from_disk() == ["empty/"]
    moved = root / "moved"
    empty.rename(moved)
    assert await store.refresh_from_disk() == ["empty/", "moved/"]
    moved.rmdir()
    assert await store.refresh_from_disk() == ["moved/"]

    docs = root / "docs"
    docs.mkdir()
    (docs / "README.md").write_text("# Docs\n", encoding="utf-8")
    assert _changed_paths(await store.refresh_from_disk()) == ["docs/", "docs/README.md"]
    assert reloaded == []
    await store.close()


async def test_prose_only_write_rejects_nested_visible_parent_symlink(tmp_path: Path):
    from ntrp.memory.file_store import FilePageStore

    root = tmp_path / "memory"
    outside = tmp_path / "outside"
    (root / "raw" / "notes").mkdir(parents=True)
    (root / "raw" / "notes" / "a.md").write_text("- 2026-07-12 ^safe1 [fact] (src:user) Safe.\n", encoding="utf-8")
    outside.mkdir()
    outside_page = outside / "a.md"
    outside_page.write_text("outside original\n", encoding="utf-8")
    _symlink_or_skip(root / "notes", outside)
    store = FilePageStore(root)
    await store.open()
    page = root / "notes" / "a.md"
    store._pages[page].prose = "# Updated prose"

    with pytest.raises(ValueError, match="symlink"):
        store._persist(page)

    assert outside_page.read_text(encoding="utf-8") == "outside original\n"
    assert sorted(path.name for path in outside.iterdir()) == ["a.md"]
    await store.close()


async def test_page_delete_rejects_nested_visible_parent_symlink_before_any_mutation(tmp_path: Path):
    from ntrp.memory.file_store import FilePageStore

    root = tmp_path / "memory"
    outside = tmp_path / "outside"
    raw = root / "raw" / "notes" / "a.md"
    raw.parent.mkdir(parents=True)
    raw.write_text("- 2026-07-12 ^safe2 [fact] (src:user) Safe.\n", encoding="utf-8")
    outside.mkdir()
    outside_page = outside / "a.md"
    outside_page.write_text("outside original\n", encoding="utf-8")
    _symlink_or_skip(root / "notes", outside)
    store = FilePageStore(root)
    await store.open()

    with pytest.raises(ValueError, match="symlink"):
        store._remove_page_files(root / "notes" / "a.md")

    assert outside_page.read_text(encoding="utf-8") == "outside original\n"
    assert raw.read_text(encoding="utf-8").startswith("- 2026-07-12 ^safe2")
    await store.close()
