"""Artifact memory changelog migration/redaction and filesystem safety."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from arden.memory.artifacts import (
    ArtifactMemoryStore,
    _redact_changelog,
)
from arden.memory.ledger import LedgerEntry, LedgerMeta, render_ledger_entry
from arden.memory.models import Kind, SourceRef
from arden.memory.page_events import page_revision

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.asyncio


def _symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")


async def test_changelog_redactor_preserves_meaningful_identifiers():
    content = _redact_changelog(
        "Completed final evaluations for stage25_sentence_bridge_300 and stage3_from_bridge_nl_400 "
        "all had exact_match 0.0. For stage25_sentence_bridge_300 at final step 299: correct "
        "normalized_similarity 0.7880833141408634 and avg_loss 1.3262231744255655; no_act "
        "normalized_similarity 0.6998057188013818. Artifacts included "
        "stage1_v3_delta_noact_calib_200_metrics.jsonl and train_tiny_oracle_delta_calib.py. "
        "The stage-3-from-bridge-nl-400 dashed slug is also meaningful. "
        "Sensitive values remain hidden: run_id=run_secret123456 span_id=span_secret123456 "
        "area:proj_secret123456 abcdef1234567890abcdef1234567890 /Users/me/src/private/file.py",
        max_chars=2000,
    )
    for token in (
        "stage25_sentence_bridge_300",
        "stage3_from_bridge_nl_400",
        "exact_match",
        "normalized_similarity",
        "avg_loss",
        "no_act",
        "stage1_v3_delta_noact_calib_200_metrics.jsonl",
        "train_tiny_oracle_delta_calib.py",
        "stage-3-from-bridge-nl-400",
        "0.7880833141408634",
        "1.3262231744255655",
        "0.6998057188013818",
    ):
        assert token in content
    for sensitive in (
        "run_secret123456",
        "span_secret123456",
        "area:proj_secret123456",
        "abcdef1234567890abcdef1234567890",
        "/Users/me",
    ):
        assert sensitive not in content


async def test_changelog_append_uses_monthly_file_after_missing_final_newline(tmp_path: Path):
    root = tmp_path / "artifacts"
    root.mkdir()
    (root / "changelog.md").write_text("# Changelog", encoding="utf-8")

    # A content-bearing event (contentless ones like "added fact memory" are
    # dropped as noise on render).
    ArtifactMemoryStore(root).append_event("Remembered: the user prefers tea")

    month = datetime.now(UTC).strftime("%Y-%m")
    content = (root / "changelog" / month[:4] / f"{month}.md").read_text(encoding="utf-8")
    assert f"# Changelog {month}" in content
    assert "\n- " in content
    assert "Remembered: the user prefers tea" in content
    assert "Changelog- " not in content


def _mkfifo_or_skip(path: Path) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("mkfifo unavailable")
    try:
        os.mkfifo(path)
    except OSError as exc:
        pytest.skip(f"mkfifo unavailable: {exc}")


async def test_existing_fifo_generated_artifact_write_fails_safe(tmp_path: Path):
    # The generated artifact that fails safe is now the changelog rollup written by
    # append_event → _write. A fifo squatting on changelog/index.md must not be
    # followed or clobbered; the safe-write primitive raises instead.
    root = tmp_path / "artifacts"
    (root / "changelog").mkdir(parents=True)
    _mkfifo_or_skip(root / "changelog" / "index.md")

    with pytest.raises(FileNotFoundError):
        ArtifactMemoryStore(root).append_event("remembered fact memory")


# Removed test_failed_record_read_preserves_existing_generated_artifacts: it tested
# that the deleted export_from_records projection tolerated a record-read failure and
# preserved prior generated artifacts. Without export there is no record read, so there
# is no live analog.


async def test_artifact_root_under_symlinked_parent_is_allowed(tmp_path: Path):
    real_parent = tmp_path / "real-parent"
    alias_parent = tmp_path / "alias-parent"
    real_parent.mkdir()
    try:
        alias_parent.symlink_to(real_parent, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")

    artifacts = ArtifactMemoryStore(alias_parent / "memory")
    artifacts.append_event("Arden keeps artifacts under memory")

    index = artifacts.read_artifact("changelog/index.md").content
    assert "# Changelog" in index
    month = datetime.now(UTC).strftime("%Y-%m")
    monthly = artifacts.read_artifact(f"changelog/{month[:4]}/{month}.md").content
    assert "Arden keeps artifacts under memory" in monthly


async def test_broken_symlink_nested_artifact_write_fails_safe(tmp_path: Path):
    # append_event writes nested changelog files; if a path component (here the
    # `changelog/` dir) is a symlink to a missing target, the safe-write primitive
    # refuses to follow it and raises instead of clobbering anything outside root.
    root = tmp_path / "artifacts"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    _symlink_or_skip(root / "changelog", outside / "owned-dir")

    with pytest.raises(FileNotFoundError):
        ArtifactMemoryStore(root).append_event("Regina drives the entity page")

    assert not (outside / "owned-dir").exists()


async def test_read_and_list_skip_symlinked_nested_artifacts(tmp_path: Path):
    root = tmp_path / "artifacts"
    outside = tmp_path / "outside"
    (root / "facts").mkdir(parents=True)
    outside.mkdir()
    (outside / "index.md").write_text("# Outside\n", encoding="utf-8")
    _symlink_or_skip(root / "facts" / "index.md", outside / "index.md")

    artifacts = ArtifactMemoryStore(root)
    assert artifacts.list_artifacts(kind="fact") == []
    with pytest.raises(FileNotFoundError):
        artifacts.read_artifact("facts/index.md")


async def test_read_and_list_open_arbitrary_markdown_and_text_paths(tmp_path: Path):
    root = tmp_path / "artifacts"
    (root / "research" / "models").mkdir(parents=True)
    (root / "research" / "models" / "notes.md").write_text("# Model notes\n\nUseful markdown.\n", encoding="utf-8")
    (root / "research" / "models" / "results.txt").write_text("Useful text results.\n", encoding="utf-8")
    (root / ".research").mkdir()
    (root / ".research" / ".hidden.md").write_text("# Hidden note\n\nUseful hidden text.\n", encoding="utf-8")

    artifacts = ArtifactMemoryStore(root)

    assert artifacts.read_artifact("research/models/notes.md").title == "Model notes"
    assert artifacts.read_artifact("research/models/results.txt").content == "Useful text results.\n"
    assert {a.path for a in artifacts.list_artifacts(q="Useful")} == {
        ".research/.hidden.md",
        "research/models/notes.md",
        "research/models/results.txt",
    }
    assert artifacts.read_artifact(".research/.hidden.md").title == "Hidden note"


async def test_record_list_page_renders_active_schema_v2_entries(tmp_path: Path):
    root = tmp_path / "artifacts"
    (root / "raw").mkdir(parents=True)
    (root / "directives.md").write_text("", encoding="utf-8")
    entry = LedgerEntry(
        id="rule",
        text="Keep answers concise.",
        kind=Kind.DIRECTIVE,
        occurred_at="2026-07-13",
        meta=LedgerMeta(
            recorded_at="2026-07-13T09:00:00+04:00",
            sequence=1,
            time_precision="day",
            scope_kind="user",
            scope_key=None,
            sources=(SourceRef("user", "chat"),),
        ),
    )
    (root / "raw" / "directives.md").write_text(
        f"<!-- arden:records schema=2 page=directives.md -->\n{render_ledger_entry(entry)}\n",
        encoding="utf-8",
    )

    artifact = ArtifactMemoryStore(root).read_artifact("directives.md")

    assert artifact.content == "- Keep answers concise."
    assert artifact.timeline == (entry,)


async def test_artifact_summaries_are_stable_and_revisions_hash_exact_bytes(tmp_path: Path):
    root = tmp_path / "artifacts"
    (root / "research").mkdir(parents=True)
    explicit = b"---\r\nsummary: Explicit durable summary\r\n---\r\n# Notes\r\n\r\nFirst prose line.\r\nSearch needle"
    fallback = b"# Fallback\r\n\r\nStable first line.\r\nSearch needle"
    (root / "research" / "explicit.md").write_bytes(explicit)
    (root / "research" / "fallback.md").write_bytes(fallback)
    store = ArtifactMemoryStore(root)

    normal = {artifact.path: artifact for artifact in store.list_artifacts()}
    searched = {artifact.path: artifact for artifact in store.list_artifacts(q="needle")}

    assert normal["research/explicit.md"].summary == "Explicit durable summary"
    assert searched["research/explicit.md"].summary == "Explicit durable summary"
    assert normal["research/fallback.md"].summary == "Stable first line."
    assert searched["research/fallback.md"].summary == "Stable first line."
    assert searched["research/fallback.md"].snippet == "Search needle"
    assert normal["research/explicit.md"].revision == page_revision(explicit)
    assert normal["research/fallback.md"].revision == page_revision(fallback)

    detail = store.read_artifact("research/explicit.md")
    assert detail.summary == "Explicit durable summary"
    assert detail.revision == page_revision(explicit)
    assert detail.raw_content == explicit.decode("utf-8")
