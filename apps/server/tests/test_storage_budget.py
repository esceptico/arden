import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from arden.core import raw_tool_results
from arden.storage_budget import (
    StorageSessionCandidate,
    build_storage_cleanup_plan,
    enforce_storage_budget,
    inspect_storage,
    list_storage_backups,
    set_backup_keep,
)


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


def test_storage_inventory_breaks_total_into_physical_categories(tmp_path: Path) -> None:
    (tmp_path / "sessions.db").write_bytes(b"s" * 11)
    (tmp_path / "memory.db").write_bytes(b"m" * 13)
    backup = tmp_path / "archive" / "old.db"
    backup.parent.mkdir()
    backup.write_bytes(b"b" * 17)
    old = (datetime.now(UTC) - timedelta(days=15)).timestamp()
    os.utime(backup, (old, old))

    inventory = inspect_storage(
        tmp_path,
        max_space_gb=None,
        referenced_tool_result_hashes=set(),
        backup_retention_days=14,
    )

    categories = {category.id: category for category in inventory.report.categories}
    assert inventory.report.total_bytes == 41
    assert categories["chat_history"].total_bytes == 11
    assert categories["memory_and_artifacts"].total_bytes == 13
    assert categories["backup_archives"].reclaimable_bytes == 17
    assert sum(category.total_bytes for category in categories.values()) == inventory.report.total_bytes


def test_backup_keep_prevents_expiry_and_can_be_removed(tmp_path: Path) -> None:
    backup = tmp_path / "archive" / "old.db"
    backup.parent.mkdir()
    backup.write_bytes(b"backup")
    old = (datetime.now(UTC) - timedelta(days=15)).timestamp()
    os.utime(backup, (old, old))

    set_backup_keep(tmp_path, "archive/old.db", keep=True)
    kept = list_storage_backups(tmp_path, backup_retention_days=14)
    report = enforce_storage_budget(
        tmp_path,
        max_space_gb=None,
        referenced_tool_result_hashes=set(),
        backup_retention_days=14,
        expire_backups=True,
    )

    assert kept[0].kept is True
    assert kept[0].expired is True
    assert backup.exists()
    assert report.reclaimed_bytes == 0

    set_backup_keep(tmp_path, "archive/old.db", keep=False)
    enforce_storage_budget(
        tmp_path,
        max_space_gb=None,
        referenced_tool_result_hashes=set(),
        backup_retention_days=14,
        expire_backups=True,
    )
    assert not backup.exists()


def test_backup_keep_rejects_paths_outside_archive(tmp_path: Path) -> None:
    outside = tmp_path / "sessions.db"
    outside.write_bytes(b"database")

    with pytest.raises(ValueError, match="inside archive"):
        set_backup_keep(tmp_path, "sessions.db", keep=True)


def test_cleanup_plan_is_deterministic_and_orders_retention_tiers(tmp_path: Path) -> None:
    backup = tmp_path / "archive" / "old.db"
    backup.parent.mkdir()
    backup.write_bytes(b"b" * 20)
    old = datetime.now(UTC) - timedelta(days=20)
    os.utime(backup, (old.timestamp(), old.timestamp()))
    (tmp_path / "sessions.db").write_bytes(b"s" * 100)
    inventory = inspect_storage(
        tmp_path,
        max_space_gb=None,
        referenced_tool_result_hashes=set(),
        backup_retention_days=14,
    )
    sessions = [
        StorageSessionCandidate("archived", True, "hot", old.isoformat(), 50, 2),
        StorageSessionCandidate("current", False, "hot", old.isoformat(), 50, 2),
    ]

    first = build_storage_cleanup_plan(
        inventory,
        target_bytes=0,
        sessions=sessions,
        allow_archived_chats=True,
        allow_delete_cold_chats=True,
        allow_current_chats=True,
        current_chat_minimum=0,
    )
    second = build_storage_cleanup_plan(
        inventory,
        target_bytes=0,
        sessions=sessions,
        allow_archived_chats=True,
        allow_delete_cold_chats=True,
        allow_current_chats=True,
        current_chat_minimum=0,
    )

    assert first.plan_id == second.plan_id
    assert [action.tier for action in first.actions] == sorted(action.tier for action in first.actions)
    assert [action.kind for action in first.actions] == [
        "expired_backup",
        "cold_convert_session",
        "delete_current_session",
    ]
