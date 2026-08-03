import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from arden.core import raw_tool_results
from arden.storage_budget import enforce_storage_budget


def _blob(root: Path, digest: str, content: bytes, *, age_days: int = 8) -> Path:
    path = root / "blobs" / "tool-results" / digest[:2] / f"{digest}.txt.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    timestamp = (datetime.now(UTC) - timedelta(days=age_days)).timestamp()
    os.utime(path, (timestamp, timestamp))
    return path


def test_budget_deletes_only_old_unreferenced_tool_result_blobs(tmp_path: Path) -> None:
    referenced_hash = "a" * 64
    orphan_hash = "b" * 64
    referenced = _blob(tmp_path, referenced_hash, b"r" * 80)
    orphan = _blob(tmp_path, orphan_hash, b"o" * 80)
    protected = tmp_path / "archives" / "backup.tar"
    protected.parent.mkdir()
    protected.write_bytes(b"p" * 80)

    report = enforce_storage_budget(
        tmp_path,
        max_space_gb=0.0000001,
        referenced_tool_result_hashes={referenced_hash},
    )

    assert report.status == "quota_blocked"
    assert report.reclaimed_bytes == 80
    assert orphan.exists() is False
    assert referenced.exists() is True
    assert protected.exists() is True


def test_budget_never_follows_symlinks_or_deletes_young_orphans(tmp_path: Path) -> None:
    young = _blob(tmp_path, "c" * 64, b"young", age_days=1)
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    outside_file = outside / f"{'d' * 64}.txt.gz"
    outside_file.write_bytes(b"outside")
    (tmp_path / "blobs" / "tool-results" / "linked").symlink_to(outside, target_is_directory=True)

    report = enforce_storage_budget(
        tmp_path,
        max_space_gb=0.000000001,
        referenced_tool_result_hashes=set(),
    )

    assert report.status == "quota_blocked"
    assert young.exists() is True
    assert outside_file.exists() is True


def test_budget_reports_without_enforcement_when_disabled(tmp_path: Path) -> None:
    orphan = _blob(tmp_path, "e" * 64, b"orphan")

    report = enforce_storage_budget(
        tmp_path,
        max_space_gb=None,
        referenced_tool_result_hashes=set(),
    )

    assert report.status == "disabled"
    assert report.total_bytes == 6
    assert report.reclaimable_bytes == 6
    assert orphan.exists() is True


def test_reusing_deduplicated_blob_renews_gc_grace(monkeypatch, tmp_path: Path) -> None:
    blob_root = tmp_path / "blobs" / "tool-results"
    monkeypatch.setattr(raw_tool_results, "RAW_TOOL_RESULTS_BASE", blob_root)
    content = "shared content"
    blob = raw_tool_results.persist_raw_tool_result(content)
    path = Path(blob.blob_path)
    old = (datetime.now(UTC) - timedelta(days=8)).timestamp()
    os.utime(path, (old, old))

    same_blob = raw_tool_results.persist_raw_tool_result(content)
    report = enforce_storage_budget(
        tmp_path,
        max_space_gb=0.000000001,
        referenced_tool_result_hashes=set(),
    )

    assert same_blob.blob_path == blob.blob_path
    assert path.exists() is True
    assert report.reclaimed_bytes == 0
