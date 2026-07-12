from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path

import pytest

from ntrp.memory.artifacts import ArtifactMemoryStore
from ntrp.memory.link_index import LinkIndex
from ntrp.server.runtime.knowledge import LinkIndexProjection


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_rebuild_resolves_paths_titles_aliases_and_records_context(tmp_path: Path):
    _write(
        tmp_path / "topics/dex.md",
        '---\ntitle: Dex\naliases: ["Dex App", "Shared"]\n---\n# Dex\n',
    )
    _write(
        tmp_path / "projects/other.md",
        '---\ntitle: Other\naliases: ["Shared"]\n---\n# Other\n',
    )
    _write(
        tmp_path / "notes.md",
        """---
title: Notes
aliases: ["[[Not a link]]"]
---
# Notes

## Current work

Before [[topics/dex|Dex note]], then [[Dex App]], [[Missing]], and [[Shared]].

`[[Dex]]`

```md
[[Dex App]]
```
""",
    )

    snapshot = LinkIndex(tmp_path).rebuild(ArtifactMemoryStore(tmp_path), "revision-1")
    outgoing = snapshot.outgoing("notes.md")

    assert [(link.target, link.status, link.resolved_path) for link in outgoing] == [
        ("topics/dex", "resolved", "topics/dex.md"),
        ("Dex App", "resolved", "topics/dex.md"),
        ("Missing", "unresolved", None),
        ("Shared", "ambiguous", None),
    ]
    assert outgoing[0].display == "Dex note"
    assert outgoing[0].heading == "Current work"
    assert outgoing[0].context == "Before [[topics/dex|Dex note]], then [[Dex App]], [[Missing]], and [[Shared]]."
    assert outgoing[-1].candidates == ("projects/other.md", "topics/dex.md")
    assert outgoing[0].source_revision == "revision-1"
    assert [link.source_path for link in snapshot.backlinks("topics/dex.md")] == [
        "notes.md",
        "notes.md",
    ]


def test_context_is_a_bounded_snippet_that_keeps_the_link(tmp_path: Path):
    _write(tmp_path / "target.md", "# Target\n")
    _write(tmp_path / "source.md", f"# Source\n\n{'before ' * 100}[[Target]] {'after ' * 100}\n")

    link = LinkIndex(tmp_path).rebuild(ArtifactMemoryStore(tmp_path), "revision-1").outgoing("source.md")[0]

    assert "[[Target]]" in link.context
    assert len(link.context) <= 280
    assert link.context.startswith("…") and link.context.endswith("…")


def test_rebuild_replaces_renamed_pages_and_round_trips_snapshot(tmp_path: Path):
    _write(tmp_path / "old.md", "---\ntitle: Nova\n---\n# Nova\n")
    _write(tmp_path / "source.md", "# Source\n\nSee [[Nova]].\n")
    index = LinkIndex(tmp_path)

    first = index.rebuild(ArtifactMemoryStore(tmp_path), "revision-1")
    assert first.outgoing("source.md")[0].resolved_path == "old.md"

    (tmp_path / "old.md").rename(tmp_path / "renamed.md")
    second = index.rebuild(ArtifactMemoryStore(tmp_path), "revision-2")
    reloaded = LinkIndex(tmp_path).snapshot

    assert second.outgoing("source.md")[0].resolved_path == "renamed.md"
    assert second.backlinks("old.md") == ()
    assert [link.source_path for link in second.backlinks("renamed.md")] == ["source.md"]
    assert reloaded == second
    assert json.loads((tmp_path / ".ntrp/indexes/links.json").read_text())["revision"] == "revision-2"


def test_rebuild_indexes_arbitrary_user_files_but_excludes_engine_and_symlinks(tmp_path: Path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside.md"
    _write(outside, "# Outside\n\n[[Target]]\n")
    _write(tmp_path / "target.md", "# Target\n")
    _write(tmp_path / "nested/custom.txt", "# Custom\n\n[[Target]]\n")
    _write(tmp_path / "raw/private.md", "[[Target]]\n")
    _write(tmp_path / ".ntrp/private.md", "[[Target]]\n")
    try:
        (tmp_path / "linked.md").symlink_to(outside)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    snapshot = LinkIndex(tmp_path).rebuild(ArtifactMemoryStore(tmp_path), "revision-1")

    assert [link.source_path for link in snapshot.backlinks("target.md")] == ["nested/custom.txt"]
    assert snapshot.outgoing("raw/private.md") == ()
    assert snapshot.outgoing(".ntrp/private.md") == ()
    assert snapshot.outgoing("linked.md") == ()


def test_failed_snapshot_publish_keeps_last_valid_snapshot(tmp_path: Path, monkeypatch):
    _write(tmp_path / "target.md", "# Target\n")
    _write(tmp_path / "source.md", "[[Target]]\n")
    index = LinkIndex(tmp_path)
    first = index.rebuild(ArtifactMemoryStore(tmp_path), "revision-1")
    persisted = (tmp_path / ".ntrp/indexes/links.json").read_bytes()
    real_replace = os.replace

    def fail_snapshot(source, target):
        if Path(target) == tmp_path / ".ntrp/indexes/links.json":
            raise OSError("index unavailable")
        return real_replace(source, target)

    monkeypatch.setattr(os, "replace", fail_snapshot)
    with pytest.raises(OSError, match="index unavailable"):
        index.rebuild(ArtifactMemoryStore(tmp_path), "revision-2")

    assert index.snapshot == first
    assert (tmp_path / ".ntrp/indexes/links.json").read_bytes() == persisted


def test_snapshot_write_rejects_symlinked_index_parent(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".ntrp").mkdir()
    try:
        (tmp_path / ".ntrp/indexes").symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    _write(tmp_path / "source.md", "[[Missing]]\n")

    with pytest.raises((FileNotFoundError, ValueError)):
        LinkIndex(tmp_path).rebuild(ArtifactMemoryStore(tmp_path), "revision-1")

    assert tuple(outside.iterdir()) == ()


@pytest.mark.asyncio
async def test_projection_failure_is_stale_and_retry_keeps_canonical_revision(tmp_path: Path):
    _write(tmp_path / "source.md", "[[Missing]]\n")
    index = LinkIndex(tmp_path)
    attempts = 0
    real_rebuild = index.rebuild

    def fail_once(artifacts, revision):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise OSError("index unavailable")
        return real_rebuild(artifacts, revision)

    index.rebuild = fail_once
    projection = LinkIndexProjection(
        index,
        artifacts=ArtifactMemoryStore(tmp_path),
        revision=lambda: "canonical-r1",
        retry_delay=60,
    )

    projection.schedule()
    await projection.wait_idle()
    assert projection.stale is True
    assert projection.retry_scheduled is True
    assert index.snapshot.revision == ""

    projection.retry_now()
    await projection.wait_idle()
    assert projection.stale is False
    assert projection.retry_scheduled is False
    assert index.snapshot.revision == "canonical-r1"
    await projection.close()


def test_projection_marks_persisted_snapshot_stale_when_canonical_revision_advanced(tmp_path: Path):
    _write(tmp_path / "source.md", "[[Missing]]\n")
    index = LinkIndex(tmp_path)
    index.rebuild(ArtifactMemoryStore(tmp_path), "canonical-r1")

    projection = LinkIndexProjection(
        LinkIndex(tmp_path),
        artifacts=ArtifactMemoryStore(tmp_path),
        revision=lambda: "canonical-r2",
    )

    assert projection.stale is True


@pytest.mark.asyncio
async def test_projection_close_joins_active_rebuild_and_rejects_late_callbacks(tmp_path: Path):
    _write(tmp_path / "source.md", "[[Missing]]\n")
    index = LinkIndex(tmp_path)
    real_rebuild = index.rebuild
    started = threading.Event()
    release = threading.Event()

    def blocking_rebuild(artifacts, revision):
        started.set()
        release.wait(timeout=5)
        return real_rebuild(artifacts, revision)

    index.rebuild = blocking_rebuild
    projection = LinkIndexProjection(
        index,
        artifacts=ArtifactMemoryStore(tmp_path),
        revision=lambda: "canonical-r1",
        retry_delay=60,
    )
    projection.schedule()
    assert await asyncio.to_thread(started.wait, 2)

    closing = asyncio.create_task(projection.close())
    await asyncio.sleep(0)
    assert not closing.done()
    release.set()
    await closing
    snapshot = (tmp_path / ".ntrp/indexes/links.json").read_bytes()
    projection.schedule()
    projection.retry_now()
    await asyncio.sleep(0)

    assert projection.closed is True
    assert projection.retry_scheduled is False
    assert (tmp_path / ".ntrp/indexes/links.json").read_bytes() == snapshot
