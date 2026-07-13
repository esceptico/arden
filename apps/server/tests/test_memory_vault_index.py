from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path
from urllib.parse import quote

import pytest

from ntrp.memory.artifacts import ArtifactMemoryStore
from ntrp.memory.file_store import FilePageStore
from ntrp.memory.models import SourceRef
from ntrp.memory.vault_index import INDEX_END, INDEX_START, VaultIndexer


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "memory"
    root.mkdir()
    return root


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _managed(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    return text.split(INDEX_START, 1)[1].split(INDEX_END, 1)[0]


def _symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")


def test_nested_user_file_is_searchable_and_indexed(vault: Path):
    _write(vault / "research/models/notes.md", "---\nsummary: Model notes\n---\n# Notes\n")

    report = VaultIndexer(vault).scan()

    assert "research/ —" in _managed(vault / "index.md")
    assert "models/ —" in _managed(vault / "research/README.md")
    assert "notes.md — Model notes" in _managed(vault / "research/models/README.md")
    assert report.missing_descriptions == ("research/", "research/models/")
    assert "research/models/ — Needs description" in report.health_output
    artifacts = ArtifactMemoryStore(vault).list_artifacts(q="Model notes")
    assert "research/models/notes.md" in {artifact.path for artifact in artifacts}


def test_daily_directory_has_a_useful_default_description(vault: Path):
    _write(vault / "daily/2026-07-13.md", "# 2026-07-13\n\n## Timeline\n")

    report = VaultIndexer(vault).scan()

    assert "daily/ — Chronological memory activity." in _managed(vault / "index.md")
    assert "daily/" not in report.missing_descriptions


def test_render_updates_is_side_effect_free_and_apply_preserves_user_prose(vault: Path):
    _write(vault / "notes.md", "# Notes\n")
    _write(vault / "index.md", f"My intro\n\n{INDEX_START}\nold\n{INDEX_END}\nFooter")
    indexer = VaultIndexer(vault)

    updates = indexer.render_updates()

    assert _managed(vault / "index.md").strip() == "old"
    assert Path("index.md") in updates
    indexer.apply()
    rendered = (vault / "index.md").read_text(encoding="utf-8")
    assert rendered.startswith("My intro")
    assert rendered.endswith("Footer")
    assert "notes.md — Notes" in _managed(vault / "index.md")


def test_discovery_rejects_symlinks_engine_namespaces_health_and_special_files(vault: Path):
    outside = vault.parent / "outside.md"
    _write(outside, "# Outside\n")
    _symlink_or_skip(vault / "linked.md", outside)
    _write(vault / "raw/private.md", "secret raw")
    _write(vault / ".ntrp/private.txt", "secret engine")
    _write(vault / "health.md", "generated health")
    _write(vault / "safe/readme.txt", "Safe text")
    fifo = vault / "pipe.txt"
    if hasattr(os, "mkfifo"):
        os.mkfifo(fifo)

    indexer = VaultIndexer(vault)
    paths = {entry.path for entry in indexer.entries}
    indexer.apply()

    assert "safe/readme.txt" in paths
    assert not {"linked.md", "raw/private.md", ".ntrp/private.txt", "health.md", "pipe.txt"} & paths
    root_block = _managed(vault / "index.md")
    assert "raw/" not in root_block and ".ntrp/" not in root_block and "health.md" not in root_block


def test_move_and_delete_remove_stale_managed_rows(vault: Path):
    _write(vault / "research/old.md", "---\nsummary: Durable description\n---\n")
    indexer = VaultIndexer(vault)
    indexer.apply()
    assert "old.md — Durable description" in _managed(vault / "research/README.md")

    (vault / "research/old.md").rename(vault / "research/new.md")
    indexer.apply()
    block = _managed(vault / "research/README.md")
    assert "new.md — Durable description" in block
    assert "old.md" not in block

    (vault / "research/new.md").unlink()
    indexer.apply()
    assert "new.md" not in _managed(vault / "research/README.md")


def test_generated_readme_heading_does_not_become_a_directory_description(vault: Path):
    _write(vault / "research/empty.md", "\n")
    indexer = VaultIndexer(vault)
    indexer.apply()

    indexer.apply()

    assert "research/ — Needs description" in _managed(vault / "index.md")


def test_empty_and_readme_only_directories_are_indexed_without_listing_readme(vault: Path):
    (vault / "empty").mkdir()
    _write(vault / "documented/README.md", "---\nsummary: Curated directory\n---\nUser prose.\n")

    report = VaultIndexer(vault).apply()

    root = _managed(vault / "index.md")
    assert "empty/ — Needs description" in root
    assert "documented/ — Curated directory" in root
    assert "README.md" not in _managed(vault / "documented/README.md")
    assert [entry.path for entry in VaultIndexer(vault).root_entries()] == ["documented/", "empty/"]
    assert report.missing_descriptions == ("empty/",)
    assert "empty/ — Needs description" in report.health_output


@pytest.mark.parametrize(
    "corrupt",
    [
        f"{INDEX_START}\none\n{INDEX_START}\ntwo\n{INDEX_END}",
        f"{INDEX_START}\none",
        f"one\n{INDEX_END}",
        f"{INDEX_END}\none\n{INDEX_START}",
        f"{INDEX_START}\nouter\n{INDEX_START}\ninner\n{INDEX_END}\n{INDEX_END}",
    ],
    ids=["duplicate-start", "missing-end", "missing-start", "reversed", "nested"],
)
def test_corrupt_markers_skip_write_and_report_health_error(vault: Path, corrupt: str):
    _write(vault / "notes.md", "# Notes\n")
    original = f"User bytes\n{corrupt}\nFooter"
    _write(vault / "index.md", original)

    report = VaultIndexer(vault).apply()

    assert (vault / "index.md").read_text(encoding="utf-8") == original
    assert report.errors == ("index.md: invalid managed index markers",)
    assert "index.md — Invalid managed index markers" in report.health_output


def test_marker_free_user_bytes_are_preserved_exactly_before_appended_block(vault: Path):
    _write(vault / "notes.md", "# Notes\n")
    original = "Intro with spaces  \n\nTrailing blank follows\n \n"
    _write(vault / "index.md", original)

    VaultIndexer(vault).apply()

    assert (vault / "index.md").read_bytes().startswith(original.encode())


def test_filename_with_description_delimiter_keeps_existing_description(vault: Path):
    name = "a — b.md"
    _write(vault / name, "# Original description\n")
    indexer = VaultIndexer(vault)
    indexer.apply()
    assert f"ntrp:path={quote(name, safe='')}" in _managed(vault / "index.md")

    _write(vault / name, "# Changed heading\n")
    indexer.apply()

    assert f"{name} — Original description" in _managed(vault / "index.md")


def test_legacy_markdown_link_target_is_description_identity(vault: Path):
    name = "a — b.md"
    _write(vault / name, "# Changed heading\n")
    _write(
        vault / "index.md",
        f"{INDEX_START}\n- [Display — label](a%20%E2%80%94%20b.md) — Curated description\n{INDEX_END}\n",
    )

    VaultIndexer(vault).apply()

    assert f"{name} — Curated description" in _managed(vault / "index.md")


def test_legacy_markdown_directory_target_preserves_trailing_slash_identity(vault: Path):
    (vault / "docs").mkdir()
    _write(vault / "index.md", f"{INDEX_START}\n- [Docs](docs/) — Curated directory\n{INDEX_END}\n")

    VaultIndexer(vault).apply()

    assert "docs/ — Curated directory" in _managed(vault / "index.md")


def test_ambiguous_plain_legacy_row_is_ignored(vault: Path):
    name = "a — b.md"
    _write(vault / name, "# Current heading\n")
    _write(vault / "index.md", f"{INDEX_START}\n- a — b.md — Wrong description\n{INDEX_END}\n")

    VaultIndexer(vault).apply()

    assert f"{name} — Current heading" in _managed(vault / "index.md")


def test_corrupt_nested_readme_is_preserved_and_not_used_as_parent_description(vault: Path):
    (vault / "research").mkdir()
    corrupt = f"Safe intro\n{INDEX_START}\nInjected parent description\n{INDEX_START}\n{INDEX_END}\n"
    _write(vault / "research/README.md", corrupt)

    report = VaultIndexer(vault).apply()

    assert (vault / "research/README.md").read_text(encoding="utf-8") == corrupt
    assert "research/ — Needs description" in _managed(vault / "index.md")
    assert report.errors == ("research/README.md: invalid managed index markers",)
    assert "research/README.md — Invalid managed index markers" in report.health_output
    assert "research/" in report.missing_descriptions


def test_hidden_user_paths_are_indexed_but_hidden_engine_paths_are_not(vault: Path):
    _write(vault / ".research/.notes.md", "# Hidden user note\n")
    _write(vault / ".ntrp/secret.md", "# Engine secret\n")
    _write(vault / ".maintenance/secret.md", "# Internal secret\n")

    VaultIndexer(vault).apply()

    assert ".research/" in _managed(vault / "index.md")
    assert ".notes.md — Hidden user note" in _managed(vault / ".research/README.md")
    assert ".ntrp/" not in _managed(vault / "index.md")
    assert ".maintenance/" not in _managed(vault / "index.md")


def test_immediate_children_sort_directories_before_files_by_casefolded_name(vault: Path):
    (vault / "zeta").mkdir()
    (vault / "Alpha").mkdir()
    _write(vault / "beta.md", "# Beta\n")
    _write(vault / "Able.md", "# Able\n")

    VaultIndexer(vault).apply()

    block = _managed(vault / "index.md")
    assert block.index("Alpha/") < block.index("zeta/") < block.index("Able.md") < block.index("beta.md")


def test_unchanged_projection_apply_does_not_replace_managed_files(vault: Path):
    _write(vault / "research/notes.md", "# Notes\n")
    indexer = VaultIndexer(vault)
    indexer.apply()
    targets = (vault / "index.md", vault / "research/README.md")
    before = {path: path.stat().st_ino for path in targets}

    report = indexer.apply()

    assert {path: path.stat().st_ino for path in targets} == before
    assert report.updated_paths == ()


def test_description_fallback_order_and_health_output(vault: Path):
    _write(vault / "summary.md", "---\nsummary: Frontmatter wins\n---\n# Ignored heading\n")
    _write(vault / "existing.md", "# New heading\n")
    _write(vault / "heading.md", "# Heading fallback\n")
    _write(vault / "sentence.txt", "First meaningful sentence.\nSecond line.\n")
    _write(vault / "empty.md", "\n")
    _write(
        vault / "index.md",
        f"# My map\n\n{INDEX_START}\n- existing.md — Curated description\n{INDEX_END}\n",
    )

    report = VaultIndexer(vault).scan()
    block = _managed(vault / "index.md")

    assert "summary.md — Frontmatter wins" in block
    assert "existing.md — Curated description" in block
    assert "heading.md — Heading fallback" in block
    assert "sentence.txt — First meaningful sentence." in block
    assert "empty.md — Needs description" in block
    assert report.missing_descriptions == ("empty.md",)
    assert "empty.md — Needs description" in report.health_output


@pytest.mark.asyncio
async def test_canonical_commit_survives_projection_failure_and_retry_repairs_index(vault: Path):
    from ntrp.server.runtime.knowledge import VaultIndexProjection

    _write(vault / "raw/me.md", "<!-- ntrp:records schema=2 page=me.md -->\n")
    projection = VaultIndexProjection(vault, retry_delay=60)
    real_apply = projection._indexer.apply
    attempts = 0

    def fail_once():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("projection unavailable")
        return real_apply()

    projection._indexer.apply = fail_once
    store = FilePageStore(vault, post_canonical_commit=projection.schedule)
    await store.open()
    before = store.canonical_revision

    record = await store.add("Canonical knowledge survives.", source_ref=SourceRef("user", "test"))
    await projection.wait_idle()

    assert record.text == "Canonical knowledge survives."
    assert store.canonical_revision != before
    assert projection.stale is True
    assert projection.retry_scheduled is True
    assert not (vault / "index.md").exists()

    projection.retry_now()
    await projection.wait_idle()
    assert projection.stale is False
    assert projection.retry_scheduled is False
    assert "me.md" in _managed(vault / "index.md")
    await projection.close()
    await store.close()


@pytest.mark.asyncio
async def test_projection_close_cancels_pending_work_and_rejects_late_callbacks(vault: Path):
    from ntrp.server.runtime.knowledge import VaultIndexProjection

    _write(vault / "notes.md", "# Notes\n")
    projection = VaultIndexProjection(vault, retry_delay=60)
    projection.schedule()

    await projection.close()
    projection.schedule()
    projection.retry_now()
    await asyncio.sleep(0)

    assert projection.closed is True
    assert projection.retry_scheduled is False
    assert not (vault / "index.md").exists()


@pytest.mark.asyncio
async def test_projection_close_waits_for_active_executor_before_returning(vault: Path):
    from ntrp.server.runtime.knowledge import VaultIndexProjection

    _write(vault / "notes.md", "# Notes\n")
    projection = VaultIndexProjection(vault, retry_delay=60)
    real_apply = projection._indexer.apply
    started = threading.Event()
    release = threading.Event()

    def blocking_apply():
        started.set()
        release.wait(timeout=5)
        return real_apply()

    projection._indexer.apply = blocking_apply
    projection.schedule()
    assert await asyncio.to_thread(started.wait, 2)

    closing = asyncio.create_task(projection.close())
    await asyncio.sleep(0)
    assert not closing.done(), "close must join active executor work"
    release.set()
    await closing
    after_close = (vault / "index.md").read_bytes()
    await asyncio.sleep(0.05)

    assert (vault / "index.md").read_bytes() == after_close
