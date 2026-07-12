from __future__ import annotations

import os
from pathlib import Path

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
    assert report.missing_descriptions == ()
    artifacts = ArtifactMemoryStore(vault).list_artifacts(q="Model notes")
    assert "research/models/notes.md" in {artifact.path for artifact in artifacts}


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
    projection.close()
    await store.close()
