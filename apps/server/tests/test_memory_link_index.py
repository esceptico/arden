from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path

import pytest

from arden.memory.artifacts import ArtifactMemoryStore
from arden.memory.link_index import LinkIndex
from arden.server.runtime.knowledge import LinkIndexProjection


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


def test_rebuild_preserves_explicit_alias_when_label_equals_target(tmp_path: Path):
    _write(tmp_path / "old.md", "# Old\n")
    _write(tmp_path / "source.md", "[[Old]] [[Old|Old]]\n")

    links = LinkIndex(tmp_path).rebuild(ArtifactMemoryStore(tmp_path), "revision-1").outgoing("source.md")

    assert [(link.display, link.alias) for link in links] == [("Old", None), ("Old", "Old")]


def test_context_uses_raw_link_offset_and_preserves_label_whitespace(tmp_path: Path):
    _write(tmp_path / "target.md", "# Target\n")
    _write(
        tmp_path / "source.md",
        f"# Source\n\n{'before ' * 100}[[Target|A  B]] {'after ' * 100}\n",
    )

    link = LinkIndex(tmp_path).rebuild(ArtifactMemoryStore(tmp_path), "revision-1").outgoing("source.md")[0]

    assert "[[Target|A  B]]" in link.context
    assert len(link.context) <= 280
    assert link.context.startswith("…") and link.context.endswith("…")


def test_maximum_allowed_target_has_explicit_elided_context_token(tmp_path: Path):
    target = "t" * 1000
    _write(tmp_path / "source.md", f"[[{target}]]\n")

    link = LinkIndex(tmp_path).rebuild(ArtifactMemoryStore(tmp_path), "revision-1").outgoing("source.md")[0]

    assert link.target == target
    assert len(link.context) <= 280
    assert link.context.startswith("[[tttt")
    assert link.context.endswith("…]]")


def test_empty_commonmark_heading_is_normalized_to_none(tmp_path: Path):
    _write(tmp_path / "target.md", "# Target\n")
    _write(tmp_path / "source.md", "# \n\n[[Target]]\n")

    link = LinkIndex(tmp_path).rebuild(ArtifactMemoryStore(tmp_path), "revision-1").outgoing("source.md")[0]

    assert link.heading is None


def test_commonmark_scanner_excludes_escaped_comments_and_all_code_forms(tmp_path: Path):
    _write(tmp_path / "visible.md", "# Visible\n")
    _write(
        tmp_path / "source.md",
        r"""# Source

\[[Escaped]]

<!--
[[Comment]]
-->

    [[Indented]]

`inline
[[Multiline inline code]]
still inline`

````md
[[Fence]]
```
[[Still fenced]]
````

[[Visible]]
""",
    )

    outgoing = LinkIndex(tmp_path).rebuild(ArtifactMemoryStore(tmp_path), "revision-1").outgoing("source.md")

    assert [(link.target, link.resolved_path) for link in outgoing] == [("Visible", "visible.md")]


def test_fragment_only_and_nested_basename_resolution_are_explicit(tmp_path: Path):
    _write(tmp_path / "nested/alpha.md", "---\ntitle: Different title\n---\n# Different title\n")
    _write(
        tmp_path / "source.md",
        "# Source\n\n[[#Local section]] [[alpha.md]] [[alpha]]\n",
    )
    index = LinkIndex(tmp_path)

    unique = index.rebuild(ArtifactMemoryStore(tmp_path), "revision-1").outgoing("source.md")

    assert [(link.target, link.status, link.resolved_path) for link in unique] == [
        ("#Local section", "resolved", "source.md"),
        ("alpha.md", "resolved", "nested/alpha.md"),
        ("alpha", "resolved", "nested/alpha.md"),
    ]

    _write(tmp_path / "other/alpha.md", "---\ntitle: Another title\n---\n# Another title\n")
    ambiguous = index.rebuild(ArtifactMemoryStore(tmp_path), "revision-2").outgoing("source.md")

    assert ambiguous[0].resolved_path == "source.md"
    assert ambiguous[1].status == ambiguous[2].status == "ambiguous"
    assert (
        ambiguous[1].candidates
        == ambiguous[2].candidates
        == (
            "nested/alpha.md",
            "other/alpha.md",
        )
    )


def test_oversized_target_or_display_is_not_indexed(tmp_path: Path):
    _write(tmp_path / "target.md", "# Target\n")
    _write(
        tmp_path / "source.md",
        f"[[{'t' * 1001}]]\n[[Target|{'d' * 1001}]]\n[[Target]]\n",
    )

    outgoing = LinkIndex(tmp_path).rebuild(ArtifactMemoryStore(tmp_path), "revision-1").outgoing("source.md")

    assert [(link.target, link.display) for link in outgoing] == [("Target", "Target")]


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
    assert json.loads((tmp_path / ".arden/indexes/links.json").read_text())["revision"] == "revision-2"


def test_rebuild_indexes_arbitrary_user_files_but_excludes_engine_and_symlinks(tmp_path: Path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside.md"
    _write(outside, "# Outside\n\n[[Target]]\n")
    _write(tmp_path / "target.md", "# Target\n")
    _write(tmp_path / "nested/custom.txt", "# Custom\n\n[[Target]]\n")
    _write(tmp_path / "raw/private.md", "[[Target]]\n")
    _write(tmp_path / ".arden/private.md", "[[Target]]\n")
    try:
        (tmp_path / "linked.md").symlink_to(outside)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    snapshot = LinkIndex(tmp_path).rebuild(ArtifactMemoryStore(tmp_path), "revision-1")

    assert [link.source_path for link in snapshot.backlinks("target.md")] == ["nested/custom.txt"]
    assert snapshot.outgoing("raw/private.md") == ()
    assert snapshot.outgoing(".arden/private.md") == ()
    assert snapshot.outgoing("linked.md") == ()


def test_failed_snapshot_publish_keeps_last_valid_snapshot(tmp_path: Path, monkeypatch):
    _write(tmp_path / "target.md", "# Target\n")
    _write(tmp_path / "source.md", "[[Target]]\n")
    index = LinkIndex(tmp_path)
    first = index.rebuild(ArtifactMemoryStore(tmp_path), "revision-1")
    persisted = (tmp_path / ".arden/indexes/links.json").read_bytes()
    real_rename = os.rename

    def fail_snapshot(source, target, **kwargs):
        if target == "links.json" or Path(target) == tmp_path / ".arden/indexes/links.json":
            raise OSError("index unavailable")
        return real_rename(source, target, **kwargs)

    monkeypatch.setattr(os, "rename", fail_snapshot)
    with pytest.raises(OSError, match="index unavailable"):
        index.rebuild(ArtifactMemoryStore(tmp_path), "revision-2")

    assert index.snapshot == first
    assert (tmp_path / ".arden/indexes/links.json").read_bytes() == persisted


def test_snapshot_publish_stays_anchored_when_parent_path_is_swapped(tmp_path: Path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    _write(tmp_path / "source.md", "[[Missing]]\n")
    index = LinkIndex(tmp_path)
    index.rebuild(ArtifactMemoryStore(tmp_path), "revision-1")
    real_rename = os.rename
    swapped = False

    def swap_parent_before_publish(source, target, **kwargs):
        nonlocal swapped
        if target == "links.json" and not swapped:
            swapped = True
            real_rename(tmp_path / ".arden/indexes", tmp_path / ".arden/indexes-real")
            (tmp_path / ".arden/indexes").symlink_to(outside, target_is_directory=True)
        return real_rename(source, target, **kwargs)

    monkeypatch.setattr(os, "rename", swap_parent_before_publish)
    index.rebuild(ArtifactMemoryStore(tmp_path), "revision-2")

    assert tuple(outside.iterdir()) == ()
    assert json.loads((tmp_path / ".arden/indexes-real/links.json").read_text())["revision"] == "revision-2"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda data: data.update(pages=["source.md", "source.md"]),
        lambda data: data.update(pages=["../source.md"]),
        lambda data: (
            data.update(pages=["source//x.md", "target.md"]),
            data["links"][0].update(source_path="source//x.md"),
        ),
        lambda data: (
            data.update(pages=["source\x00.md", "target.md"]),
            data["links"][0].update(source_path="source\x00.md"),
        ),
        lambda data: data["links"][0].update(source_path="missing.md"),
        lambda data: data["links"][0].update(status="resolved", resolved_path="missing.md", candidates=["missing.md"]),
        lambda data: data["links"][0].update(status="ambiguous", resolved_path=None, candidates=["target.md"]),
        lambda data: data["links"][0].update(source_revision="different"),
        lambda data: data["links"][0].update(target="x" * 1001),
        lambda data: data["links"][0].update(context="x" * 281),
    ],
    ids=[
        "duplicate-pages",
        "unsafe-page",
        "noncanonical-page",
        "nul-page",
        "missing-source",
        "missing-target",
        "invalid-ambiguity",
        "wrong-revision",
        "long-target",
        "long-context",
    ],
)
def test_semantically_corrupt_persisted_snapshot_loads_empty_and_stale(tmp_path: Path, mutate):
    _write(tmp_path / "target.md", "# Target\n")
    _write(tmp_path / "source.md", "[[Target]]\n")
    LinkIndex(tmp_path).rebuild(ArtifactMemoryStore(tmp_path), "revision-1")
    path = tmp_path / ".arden/indexes/links.json"
    data = json.loads(path.read_text())
    mutate(data)
    path.write_text(json.dumps(data), encoding="utf-8")
    reloaded = LinkIndex(tmp_path)
    projection = LinkIndexProjection(
        reloaded,
        artifacts=ArtifactMemoryStore(tmp_path),
        revision=lambda: "",
    )

    assert reloaded.snapshot.revision == ""
    assert reloaded.snapshot.pages == reloaded.snapshot.links == ()
    assert projection.stale is True


def test_snapshot_write_rejects_symlinked_index_parent(tmp_path: Path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".arden").mkdir()
    try:
        (tmp_path / ".arden/indexes").symlink_to(outside, target_is_directory=True)
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


@pytest.mark.asyncio
async def test_projection_uses_a_stable_revision_for_an_empty_canonical_vault(tmp_path: Path):
    _write(tmp_path / "index.md", "# Memory\n")
    index = LinkIndex(tmp_path)
    projection = LinkIndexProjection(
        index,
        artifacts=ArtifactMemoryStore(tmp_path),
        revision=lambda: "",
        retry_delay=60,
    )

    projection.schedule()
    await projection.wait_idle()

    assert projection.stale is False
    assert len(index.snapshot.revision) == 64
    assert index.snapshot.pages == ("index.md",)
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
async def test_projection_rebuilds_again_when_revision_changes_during_worker(tmp_path: Path):
    _write(tmp_path / "source.md", "[[Missing]]\n")
    current_revision = "canonical-r1"
    index = LinkIndex(tmp_path)
    real_rebuild = index.rebuild
    revisions: list[str] = []

    def advance_after_first_rebuild(artifacts, revision):
        nonlocal current_revision
        revisions.append(revision)
        snapshot = real_rebuild(artifacts, revision)
        if revision == "canonical-r1":
            current_revision = "canonical-r2"
        return snapshot

    index.rebuild = advance_after_first_rebuild
    projection = LinkIndexProjection(
        index,
        artifacts=ArtifactMemoryStore(tmp_path),
        revision=lambda: current_revision,
        retry_delay=60,
    )

    projection.schedule()
    await projection.wait_idle()

    assert revisions == ["canonical-r1", "canonical-r2"]
    assert index.snapshot.revision == "canonical-r2"
    assert projection.stale is False
    await projection.close()


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
    snapshot = (tmp_path / ".arden/indexes/links.json").read_bytes()
    projection.schedule()
    projection.retry_now()
    await asyncio.sleep(0)

    assert projection.closed is True
    assert projection.retry_scheduled is False
    assert (tmp_path / ".arden/indexes/links.json").read_bytes() == snapshot
