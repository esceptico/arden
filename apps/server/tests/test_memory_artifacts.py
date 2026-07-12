"""Artifact memory changelog migration/redaction and filesystem safety."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from ntrp.memory.artifacts import (
    ArtifactMemoryStore,
    _redact_changelog,
)

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
        "Legacy unquoted [technical id] placeholder damage should read as a technical identifier. "
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
        "Legacy unquoted technical identifier placeholder damage",
    ):
        assert token in content
    assert "[technical id]" not in content
    for sensitive in (
        "run_secret123456",
        "span_secret123456",
        "area:proj_secret123456",
        "abcdef1234567890abcdef1234567890",
        "/Users/me",
    ):
        assert sensitive not in content


async def test_source_ref_repr_debug_fragment_is_migrated_and_stripped_from_changelog(tmp_path: Path):
    root = tmp_path / "artifacts"
    root.mkdir()
    raw = (
        "# Changelog\n\n"
        "- 2025-01-01T00:00:00+00:00 — remembered summary "
        "source_ref=SourceRef(kind='chat_turn', ref='session-secret:toolu_secret123456', "
        "captured_at='2025-01-01T00:00:00Z', scope_kind='session') trailing prose\n"
    )
    (root / "changelog.md").write_text(raw, encoding="utf-8")

    store = ArtifactMemoryStore(root)
    store.append_event("test event")  # triggers legacy-changelog migration + sanitize
    artifact = store.read_artifact("changelog/2025/2025-01.md")

    assert not (root / "changelog.md").exists()
    # The legacy line is contentless audit noise → dropped on render; either way
    # no raw provenance reprs/secrets survive into the changelog.
    for raw_token in ("SourceRef", "session-secret", "toolu_secret123456", "captured_at", "scope_kind", "source_ref"):
        assert raw_token not in artifact.content


async def test_changelog_migration_sanitizes_existing_content(tmp_path: Path):
    root = tmp_path / "artifacts"
    root.mkdir()
    (root / "changelog.md").write_text(
        "# Changelog\n\n"
        "- 2025-01-01T00:00:00+00:00 — pinned record abcdef1234567890abcdef1234567890 "
        "scope=area:proj_readsecret123 /Users/me/src/ntrp run_id=run_readsecret123456\n",
        encoding="utf-8",
    )

    store = ArtifactMemoryStore(root)
    store.append_event("test event")  # triggers legacy-changelog migration + sanitize
    artifact = store.read_artifact("changelog/2025/2025-01.md")

    # Contentless secret-laden legacy line → dropped on render; no secrets survive.
    for raw_token in (
        "abcdef1234567890abcdef1234567890",
        "scope=",
        "area:proj_readsecret123",
        "/Users/me",
        "run_readsecret",
    ):
        assert raw_token not in artifact.content
    assert (root / "changelog" / "2025" / "2025-01.md").read_text(encoding="utf-8") == artifact.content


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


async def test_existing_fifo_changelog_append_fails_safe(tmp_path: Path):
    root = tmp_path / "artifacts"
    root.mkdir()
    _mkfifo_or_skip(root / "changelog.md")

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
    artifacts.append_event("ntrp keeps artifacts under memory")

    index = artifacts.read_artifact("changelog/index.md").content
    assert "# Changelog" in index
    month = datetime.now(UTC).strftime("%Y-%m")
    monthly = artifacts.read_artifact(f"changelog/{month[:4]}/{month}.md").content
    assert "ntrp keeps artifacts under memory" in monthly


async def test_existing_changelog_is_sanitized_on_rebuild_without_reconstruction(tmp_path: Path):
    root = tmp_path / "artifacts"
    root.mkdir()
    raw_event = (
        "- 2024-01-02T03:04:05+00:00 — added fact record "
        "abcdef1234567890abcdef1234567890 scope=area:proj_forbidden123456 "
        "source_ref=chat_turn:session path=/Users/escept1co/src/ntrp "
        "run_id=run_forbidden123456 span_id=span_forbidden123456 tool_id=toolu_forbidden123456 "
        "hash=abcdef1234567890abcdef1234567890\n"
    )
    (root / "changelog.md").write_text("# Changelog\n\n" + raw_event, encoding="utf-8")

    ArtifactMemoryStore(root).append_event("test event")  # triggers migration + sanitize

    content = (root / "changelog" / "2024" / "2024-01.md").read_text(encoding="utf-8")
    # Contentless secret-laden legacy line → dropped on render; no secrets survive.
    for raw in (
        "abcdef1234567890abcdef1234567890",
        "scope=",
        "area:proj_forbidden123456",
        "source_ref",
        "/Users/escept1co/src/ntrp",
        "run_forbidden123456",
        "span_forbidden123456",
        "toolu_forbidden123456",
    ):
        assert raw not in content
    assert not (root / "changelog.md").exists()


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


async def test_broken_symlink_changelog_append_fails_safe(tmp_path: Path):
    root = tmp_path / "artifacts"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    _symlink_or_skip(root / "changelog.md", outside / "owned.md")

    with pytest.raises(FileNotFoundError):
        ArtifactMemoryStore(root).append_event("remembered fact memory")

    assert not (outside / "owned.md").exists()


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

    artifacts = ArtifactMemoryStore(root)

    assert artifacts.read_artifact("research/models/notes.md").title == "Model notes"
    assert artifacts.read_artifact("research/models/results.txt").content == "Useful text results.\n"
    assert {a.path for a in artifacts.list_artifacts(q="Useful")} == {
        "research/models/notes.md",
        "research/models/results.txt",
    }

