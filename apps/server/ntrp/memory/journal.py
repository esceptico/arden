"""Crash-consistent commits for canonical vault file sets."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import stat
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping, Sequence

_VERSION = 2
_SUPPORTED_VERSIONS = frozenset({1, _VERSION})
_HEX = frozenset("0123456789abcdef")


class JournalConflictError(ValueError):
    """An expected canonical input changed before journal replacement began."""


@dataclass(frozen=True)
class PreparedCommit:
    commit_id: str
    manifest_hash: str
    path: Path


class VaultJournal:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self._meta_root = self.root / ".ntrp"
        self._journal_root = self._meta_root / "journal"
        self._versions_root = self._meta_root / "versions"
        self._revision_path = self._meta_root / "canonical-revision"
        self._lock_path = self._meta_root / "canonical.lock"
        self._lock_depth = 0
        self._thread_lock = threading.RLock()

    @property
    def canonical_revision(self) -> str:
        self._validate_metadata_paths()
        return self._canonical_revision_locked()

    def _canonical_revision_locked(self) -> str:
        try:
            revision = self._revision_path.read_text(encoding="ascii").strip()
        except OSError:
            return ""
        return revision if self._valid_hash(revision) else ""

    def prepare(
        self,
        files: Mapping[Path, bytes],
        *,
        _publish_revision: bool = True,
    ) -> PreparedCommit:
        with self._locked():
            return self._prepare_locked(
                files,
                publish_revision=_publish_revision,
            )

    def _prepare_locked(
        self,
        files: Mapping[Path, bytes],
        *,
        publish_revision: bool = True,
    ) -> PreparedCommit:
        rows: list[dict[str, object]] = []
        payloads: list[tuple[bytes, bytes]] = []
        seen: set[str] = set()
        for index, (target, content) in enumerate(sorted(files.items(), key=lambda item: item[0].as_posix())):
            rel = self._validate_relative(target)
            if rel in seen:
                raise ValueError(f"duplicate journal target: {rel}")
            if not isinstance(content, bytes):
                raise TypeError(f"journal content for {rel} must be bytes")
            seen.add(rel)
            target_path = self._target_path(rel)
            existed = target_path.exists()
            if target_path.is_symlink() or (existed and not target_path.is_file()):
                raise ValueError(f"journal target is not a regular file: {rel}")
            backup = target_path.read_bytes() if existed else b""
            staged_name = f"staged/{index:04d}"
            backup_name = f"backups/{index:04d}"
            publish_name = f"publish/{index:04d}"
            rows.append(
                {
                    "target": rel,
                    "staged": staged_name,
                    "backup": backup_name,
                    "publish": publish_name,
                    "sha256": self._sha256(content),
                    "backup_sha256": self._sha256(backup),
                    "existed": existed,
                }
            )
            payloads.append((content, backup))
        if not rows:
            raise ValueError("journal commit requires at least one file")

        manifest = {"version": _VERSION, "previous_revision": self._canonical_revision_locked(), "files": rows}
        if not publish_revision:
            manifest["publish_revision"] = False
        manifest_bytes = (
            json.dumps(
                manifest,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
        commit_id = self._sha256(manifest_bytes)
        commit_path = self._journal_root / commit_id
        self._mkdir_durable(self._journal_root)
        commit_path.mkdir()
        self._fsync_dir(self._journal_root)
        for row, (content, backup) in zip(rows, payloads, strict=True):
            self._write_fsynced(commit_path / str(row["staged"]), content)
            self._write_fsynced(commit_path / str(row["backup"]), backup)
            self._write_fsynced(commit_path / str(row["publish"]), content)
        for name in ("displaced", "rejected", "markers"):
            self._mkdir_durable(commit_path / name)
        self._checkpoint("before_prepare_complete")
        self._fsync_dir(commit_path / "staged")
        self._fsync_dir(commit_path / "backups")
        self._fsync_dir(commit_path / "publish")
        self._fsync_dir(commit_path / "displaced")
        self._fsync_dir(commit_path / "rejected")
        self._fsync_dir(commit_path / "markers")
        self._fsync_dir(commit_path)
        self._write_fsynced(commit_path / "manifest.json", manifest_bytes)
        self._fsync_dir(commit_path)
        self._write_fsynced(commit_path / "PREPARED", b"")
        self._checkpoint("after_prepared_marker_write")
        self._fsync_dir(commit_path)
        self._checkpoint("after_prepared_marker_commit_fsync")
        self._fsync_dir(self._journal_root)
        self._checkpoint("after_prepared_marker_journal_fsync")
        self._checkpoint("after_prepared")
        return PreparedCommit(commit_id=commit_id, manifest_hash=commit_id, path=commit_path)

    def commit(
        self,
        files: Mapping[Path, bytes],
        *,
        expected_files: Mapping[Path, bytes | None] | None = None,
        expected_revision: str | None = None,
    ) -> str:
        """Commit without clobbering cooperating or external filesystem writers."""
        with self._locked():
            self._recover_locked()
            self._assert_expected_state(expected_files, expected_revision)
            prepared = self.prepare(files)
            try:
                self._assert_expected_state(expected_files, expected_revision)
            except JournalConflictError:
                self._remove_commit(prepared.path)
                self._remove_empty_journal_root()
                raise
            manifest = self._load_manifest(prepared.path)
            self._finish(prepared.path, manifest, prepared.commit_id)
            return prepared.manifest_hash

    def commit_projection(
        self,
        files: Mapping[Path, bytes],
        *,
        expected_files: Mapping[Path, bytes | None] | None = None,
        expected_revision: str | None = None,
    ) -> None:
        """Atomically replace a derived file set without advancing canonical revision."""
        with self._locked():
            self._recover_locked()
            self._assert_expected_state(expected_files, expected_revision)
            prepared = self.prepare(files, _publish_revision=False)
            try:
                self._assert_expected_state(expected_files, expected_revision)
            except JournalConflictError:
                self._remove_commit(prepared.path)
                self._remove_empty_journal_root()
                raise
            manifest = self._load_manifest(prepared.path)
            self._finish(prepared.path, manifest, prepared.commit_id)

    def replace_file_safely(self, target: Path, content: bytes) -> None:
        """Replace one non-journal file without following vault symlinks.

        Used by rebuildable projections. It does not publish a canonical revision.
        """
        if not isinstance(content, bytes):
            raise TypeError("safe file content must be bytes")
        path = self._safe_external_target(target)
        self._mkdir_durable(path.parent)
        self._validate_regular_leaf(path, allow_missing=True)
        temp = path.parent / f".ntrp-projection-{uuid4().hex}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        descriptor = -1
        try:
            descriptor = os.open(temp, flags, 0o600)
            opened = os.fstat(descriptor)
            if not stat.S_ISREG(opened.st_mode):
                raise ValueError(f"safe temp is not a regular file: {temp}")
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            self._assert_safe_parents(path)
            self._validate_regular_leaf(path, allow_missing=True)
            os.replace(temp, path)
            self._fsync_dir(path.parent)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temp.unlink(missing_ok=True)

    def unlink_files_safely(self, targets: Sequence[Path]) -> None:
        """Prevalidate a file set, then unlink only regular vault-local leaves."""
        checked: list[tuple[Path, bool]] = []
        for target in targets:
            path = self._safe_external_target(target)
            exists = self._validate_regular_leaf(path, allow_missing=True)
            checked.append((path, exists))
        synced: set[Path] = set()
        for path, exists in checked:
            if not exists:
                continue
            self._assert_safe_parents(path)
            self._validate_regular_leaf(path, allow_missing=False)
            path.unlink()
            synced.add(path.parent)
        for directory in sorted(synced):
            self._fsync_dir(directory)

    def recover(self, *, prefer_rollback: bool = False) -> tuple[str, ...]:
        if not self.root.exists():
            return ()
        with self._locked():
            return self._recover_locked(prefer_rollback=prefer_rollback)

    def _recover_locked(self, *, prefer_rollback: bool = False) -> tuple[str, ...]:
        if not self._journal_root.is_dir():
            return ()
        recovered: list[str] = []
        commit_paths = sorted(self._journal_root.iterdir())
        for commit_path in commit_paths:
            self._validate_commit_path(commit_path)
            commit_id = commit_path.name
            prepared_marker = commit_path / "PREPARED"
            self._validate_internal_file(prepared_marker)
            if not prepared_marker.is_file():
                self._remove_commit(commit_path)
                recovered.append(commit_id)
                continue
            manifest = self._load_manifest(commit_path)
            if manifest["version"] == 1:
                self._recover_v1(commit_path, manifest, commit_id, prefer_rollback=prefer_rollback)
                recovered.append(commit_id)
                continue
            self._ensure_publish_state(commit_path, manifest)
            if self._has_marker(commit_path, "CONFLICT") or self._has_marker(commit_path, "ROLLED_BACK"):
                self._archive_commit(commit_path)
            elif self._has_marker(commit_path, "DECIDED_COMMIT") or self._has_marker(commit_path, "COMMITTED"):
                self._complete_decided_commit(commit_path, manifest, commit_id)
            elif not prefer_rollback and self._all_rows_provably_installed(commit_path, manifest):
                self._write_marker(commit_path, "DECIDED_COMMIT")
                self._complete_decided_commit(commit_path, manifest, commit_id)
            else:
                self._restore(commit_path, manifest)
            recovered.append(commit_id)
        self._remove_empty_journal_root()
        return tuple(recovered)

    def _recover_v1(
        self,
        commit_path: Path,
        manifest: dict,
        commit_id: str,
        *,
        prefer_rollback: bool,
    ) -> None:
        committed = commit_path / "COMMITTED"
        rolled_back = commit_path / "ROLLED_BACK"
        self._validate_internal_file(committed)
        self._validate_internal_file(rolled_back)
        if rolled_back.is_file():
            if manifest.get("publish_revision", True):
                self._restore_previous_revision_locked(manifest["previous_revision"], commit_id)
            self._archive_commit(commit_path)
            return
        if committed.is_file() or self._targets_match(manifest):
            if manifest.get("publish_revision", True):
                self._publish_revision_locked(commit_id, expected_previous=manifest["previous_revision"])
            self._archive_commit(commit_path)
            return

        self._ensure_publish_state(commit_path, manifest)
        if prefer_rollback or not self._backup_targets_match(manifest):
            self._restore(commit_path, manifest)
            return
        self._finish(commit_path, manifest, commit_id)

    def _finish(self, commit_path: Path, manifest: dict, commit_id: str) -> None:
        self._ensure_publish_state(commit_path, manifest)
        self._preflight_install(commit_path, manifest)
        for index, row in enumerate(manifest["files"]):
            self._install_row(commit_path, row, index)
        if not self._targets_match(manifest):
            self._mark_conflict(commit_path, f"journal {commit_id} target changed before commit decision")
        self._checkpoint("before_decided_commit")
        self._write_marker(commit_path, "DECIDED_COMMIT")
        self._checkpoint("after_decided_commit")
        self._complete_decided_commit(commit_path, manifest, commit_id)

    def _restore(self, commit_path: Path, manifest: dict) -> None:
        for index, row in enumerate(manifest["files"]):
            target = self._target_path(row["target"])
            rejected_candidate = self._classify_rejected(commit_path, row, index, target)
            actual = self._leaf_hash(target)
            if row["existed"]:
                if rejected_candidate:
                    source = self._expected_restore_source(commit_path, row, index)
                    self._link_no_clobber(source, target, commit_path)
                    continue
                if actual == row["backup_sha256"]:
                    continue
                if actual is not None and actual != row["sha256"]:
                    self._mark_conflict(commit_path, f"journal {commit_path.name} rollback found external target")
                if actual == row["sha256"]:
                    self._preserve_rejected_target(commit_path, target, index)
                    self._classify_rejected(commit_path, row, index, target)
                source = self._expected_restore_source(commit_path, row, index)
                self._link_no_clobber(source, target, commit_path)
            else:
                if rejected_candidate:
                    continue
                if actual is None:
                    continue
                if actual != row["sha256"]:
                    self._mark_conflict(commit_path, f"journal {commit_path.name} rollback found external target")
                self._preserve_rejected_target(commit_path, target, index)
                self._classify_rejected(commit_path, row, index, target)
                if target.exists() or target.is_symlink():
                    self._mark_conflict(commit_path, f"journal {commit_path.name} rollback raced an external target")
        if not self._backup_targets_match(manifest):
            self._mark_conflict(commit_path, f"journal {commit_path.name} rollback validation failed")
        self._write_marker(commit_path, "ROLLED_BACK")
        self._checkpoint("after_rolled_back")
        self._archive_commit(commit_path)

    def _load_manifest(self, commit_path: Path) -> dict:
        self._validate_commit_path(commit_path)
        manifest_path = commit_path / "manifest.json"
        self._validate_internal_file(manifest_path)
        try:
            raw = manifest_path.read_bytes()
            manifest = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"invalid journal manifest: {commit_path.name}") from exc
        if self._sha256(raw) != commit_path.name or manifest.get("version") not in _SUPPORTED_VERSIONS:
            raise RuntimeError(f"invalid journal manifest identity: {commit_path.name}")
        rows = manifest.get("files")
        previous_revision = manifest.get("previous_revision", "")
        if not isinstance(previous_revision, str) or (previous_revision and not self._valid_hash(previous_revision)):
            raise RuntimeError(f"invalid previous canonical revision: {commit_path.name}")
        if not isinstance(manifest.get("publish_revision", True), bool):
            raise RuntimeError(f"invalid revision publication mode: {commit_path.name}")
        manifest["previous_revision"] = previous_revision
        if not isinstance(rows, list) or not rows:
            raise RuntimeError(f"invalid journal file set: {commit_path.name}")
        seen: set[str] = set()
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                raise RuntimeError(f"invalid journal file entry: {commit_path.name}")
            try:
                target = self._validate_relative(Path(row["target"]))
                staged = self._artifact_path(commit_path, row["staged"], "staged")
                backup = self._artifact_path(commit_path, row["backup"], "backups")
                publish = self._artifact_path(commit_path, row.get("publish", f"publish/{index:04d}"), "publish")
            except (KeyError, TypeError, ValueError) as exc:
                raise RuntimeError(f"invalid journal file entry: {commit_path.name}") from exc
            if target in seen or not isinstance(row.get("existed"), bool):
                raise RuntimeError(f"invalid journal file entry: {commit_path.name}")
            if not self._valid_hash(row.get("sha256")) or not self._valid_hash(row.get("backup_sha256")):
                raise RuntimeError(f"invalid journal file hash: {commit_path.name}")
            seen.add(target)
            row["target"] = target
            row["staged"] = staged.relative_to(commit_path).as_posix()
            row["backup"] = backup.relative_to(commit_path).as_posix()
            row["publish"] = publish.relative_to(commit_path).as_posix()
        self._validate_artifact_paths(commit_path, manifest)
        return manifest

    def _targets_match(self, manifest: dict) -> bool:
        for row in manifest["files"]:
            target = self._target_path(row["target"])
            if target.is_symlink() or not target.is_file() or self._sha256(target.read_bytes()) != row["sha256"]:
                return False
        return True

    def _backup_targets_match(self, manifest: dict) -> bool:
        for row in manifest["files"]:
            target = self._target_path(row["target"])
            if not row["existed"]:
                if target.exists() or target.is_symlink():
                    return False
                continue
            if target.is_symlink() or not target.is_file() or self._sha256(target.read_bytes()) != row["backup_sha256"]:
                return False
        return True

    def _ensure_publish_state(self, commit_path: Path, manifest: dict) -> None:
        for name in ("publish", "displaced", "rejected", "markers"):
            self._mkdir_durable(commit_path / name)
        for row in manifest["files"]:
            publish = commit_path / row["publish"]
            self._validate_artifact_file(commit_path, publish, require_file=False)
            if not publish.exists():
                self._write_fsynced(publish, self._read_artifact(commit_path, row["staged"]))
        self._fsync_dir(commit_path / "publish")
        self._fsync_dir(commit_path)

    def _preflight_install(self, commit_path: Path, manifest: dict) -> None:
        commit_device = commit_path.stat().st_dev
        for index, row in enumerate(manifest["files"]):
            target = self._target_path(row["target"])
            self._mkdir_durable(target.parent)
            try:
                target_st = target.lstat()
            except FileNotFoundError:
                target_st = None
            if target_st is not None and (stat.S_ISLNK(target_st.st_mode) or not stat.S_ISREG(target_st.st_mode)):
                self._mark_conflict(commit_path, f"journal target is not a regular file: {row['target']}")
            if target.parent.stat().st_dev != commit_device:
                self._mark_conflict(commit_path, f"journal target is on an unsupported filesystem: {row['target']}")
            publish = commit_path / row["publish"]
            if self._leaf_hash(publish) != row["sha256"]:
                self._mark_conflict(commit_path, f"journal publish artifact is invalid: {row['target']}")
            probe = commit_path / "rejected" / f"probe-{index:04d}"
            self._validate_artifact_file(commit_path, probe, require_file=False)
            try:
                os.link(publish, probe, follow_symlinks=False)
            except OSError as exc:
                self._mark_conflict(commit_path, f"journal hard links are unsupported: {row['target']}: {exc}")
            else:
                probe.unlink()
                self._fsync_dir(probe.parent)

    def _install_row(self, commit_path: Path, row: dict, index: int) -> None:
        target = self._target_path(row["target"])
        displaced = commit_path / "displaced" / f"{index:04d}"
        self._validate_artifact_file(commit_path, displaced, require_file=False)
        if displaced.exists() or displaced.is_symlink():
            self._mark_conflict(commit_path, f"journal displaced path already exists: {row['target']}")

        self._checkpoint(f"before_move:{index}")
        if row["existed"]:
            try:
                target_st = target.lstat()
            except FileNotFoundError:
                self._mark_conflict(commit_path, f"journal target disappeared before displacement: {row['target']}")
            if stat.S_ISLNK(target_st.st_mode) or not stat.S_ISREG(target_st.st_mode):
                self._mark_conflict(commit_path, f"journal target changed type: {row['target']}")
            try:
                os.rename(target, displaced)
            except OSError as exc:
                self._mark_conflict(commit_path, f"journal target displacement failed: {row['target']}: {exc}")
        elif target.exists() or target.is_symlink():
            self._mark_conflict(commit_path, f"journal target appeared before install: {row['target']}")
        self._checkpoint(f"after_move:{index}")
        self._fsync_dir(target.parent)
        self._checkpoint(f"after_move_target_fsync:{index}")
        self._fsync_dir(displaced.parent)
        self._checkpoint(f"after_move_displaced_fsync:{index}")

        if row["existed"] and self._leaf_hash(displaced) != row["backup_sha256"]:
            self._restore_preserved_no_clobber(displaced, target)
            self._mark_conflict(commit_path, f"journal displaced target changed: {row['target']}")
        self._write_marker(commit_path, f"DISPLACED.{index:04d}")
        self._checkpoint(f"after_displaced:{index}")

        publish = commit_path / row["publish"]
        self._checkpoint(f"before_link:{index}")
        try:
            os.link(publish, target, follow_symlinks=False)
        except OSError as exc:
            if row["existed"]:
                self._restore_preserved_no_clobber(displaced, target)
            self._mark_conflict(commit_path, f"journal target install conflicted: {row['target']}: {exc}")
        self._checkpoint(f"after_link:{index}")
        self._fsync_dir(target.parent)
        self._checkpoint(f"after_link_fsync:{index}")
        if self._leaf_hash(target) != row["sha256"] or self._leaf_hash(publish) != row["sha256"]:
            self._mark_conflict(commit_path, f"journal installed target changed: {row['target']}")
        self._write_marker(commit_path, f"INSTALLED.{index:04d}")
        self._checkpoint(f"after_installed:{index}")
        self._checkpoint(f"after_replace:{index}")

    def _complete_decided_commit(self, commit_path: Path, manifest: dict, commit_id: str) -> None:
        if manifest.get("publish_revision", True):
            self._checkpoint("before_revision_publish")
            self._publish_revision_locked(commit_id, expected_previous=manifest["previous_revision"])
            self._checkpoint("after_revision_published")
        if not self._has_marker(commit_path, "COMMITTED"):
            self._write_marker(commit_path, "COMMITTED")
        self._checkpoint("after_committed")
        self._archive_commit(commit_path)

    def _all_rows_provably_installed(self, commit_path: Path, manifest: dict) -> bool:
        for index, row in enumerate(manifest["files"]):
            if self._leaf_hash(self._target_path(row["target"])) != row["sha256"]:
                return False
            if self._leaf_hash(commit_path / row["publish"]) != row["sha256"]:
                return False
            displaced = commit_path / "displaced" / f"{index:04d}"
            if row["existed"] and self._leaf_hash(displaced) != row["backup_sha256"]:
                return False
            if not row["existed"] and (displaced.exists() or displaced.is_symlink()):
                return False
        return True

    def _expected_restore_source(self, commit_path: Path, row: dict, index: int) -> Path:
        displaced = commit_path / "displaced" / f"{index:04d}"
        if self._leaf_hash(displaced) == row["backup_sha256"]:
            return displaced
        backup = commit_path / row["backup"]
        if self._leaf_hash(backup) != row["backup_sha256"]:
            self._mark_conflict(commit_path, f"journal backup artifact is invalid: {row['target']}")
        return backup

    def _preserve_rejected_target(self, commit_path: Path, target: Path, index: int) -> Path:
        rejected = commit_path / "rejected" / f"{index:04d}"
        self._validate_artifact_file(commit_path, rejected, require_file=False)
        if rejected.exists() or rejected.is_symlink():
            self._mark_conflict(commit_path, f"journal rejected path already exists: {target}")
        os.rename(target, rejected)
        self._checkpoint(f"after_rejected_rename:{index}")
        self._fsync_dir(target.parent)
        self._checkpoint(f"after_rejected_target_fsync:{index}")
        self._fsync_dir(rejected.parent)
        self._checkpoint(f"after_rejected_dir_fsync:{index}")
        return rejected

    def _classify_rejected(self, commit_path: Path, row: dict, index: int, target: Path) -> bool:
        rejected = commit_path / "rejected" / f"{index:04d}"
        self._validate_artifact_file(commit_path, rejected, require_file=False)
        if not rejected.exists():
            return False
        rejected_hash = self._leaf_hash(rejected)
        self._checkpoint(f"after_rejected_validation:{index}")
        if target.exists() or target.is_symlink():
            self._mark_conflict(
                commit_path,
                f"journal {commit_path.name} rollback found target and rejected versions",
            )
        if rejected_hash != row["sha256"]:
            self._restore_preserved_no_clobber(rejected, target, rejected_index=index)
            self._mark_conflict(
                commit_path,
                f"journal {commit_path.name} rollback retained an external rejected version",
            )
        return True

    def _restore_preserved_no_clobber(
        self,
        preserved: Path,
        target: Path,
        *,
        rejected_index: int | None = None,
    ) -> None:
        if rejected_index is not None:
            self._checkpoint(f"before_rejected_relink:{rejected_index}")
        if target.exists() or target.is_symlink():
            return
        try:
            os.link(preserved, target, follow_symlinks=False)
        except FileExistsError:
            return
        except OSError as exc:
            if rejected_index is not None:
                raise RuntimeError(f"journal could not restore rejected version: {target}") from exc
            return
        if rejected_index is not None:
            self._checkpoint(f"after_rejected_relink:{rejected_index}")
        self._fsync_dir(target.parent)
        if rejected_index is not None:
            self._checkpoint(f"after_rejected_relink_fsync:{rejected_index}")

    def _link_no_clobber(self, source: Path, target: Path, commit_path: Path) -> None:
        try:
            os.link(source, target, follow_symlinks=False)
        except OSError as exc:
            self._mark_conflict(commit_path, f"journal rollback conflicted: {target}: {exc}")
        self._fsync_dir(target.parent)

    def _mark_conflict(self, commit_path: Path, message: str) -> None:
        self._write_marker(commit_path, "CONFLICT")
        self._checkpoint("after_conflict")
        self._archive_commit(commit_path)
        raise JournalConflictError(f"journal conflict: {message}")

    def _write_marker(self, commit_path: Path, name: str) -> None:
        marker = commit_path / "markers" / name
        self._write_fsynced(marker, b"")
        self._checkpoint(f"after_marker_write:{name}")
        self._fsync_dir(marker.parent)
        self._checkpoint(f"after_marker_dir_fsync:{name}")
        self._fsync_dir(commit_path)
        self._checkpoint(f"after_marker_commit_fsync:{name}")

    def _has_marker(self, commit_path: Path, name: str) -> bool:
        marker = commit_path / "markers" / name
        self._validate_internal_file(marker)
        return marker.is_file()

    @staticmethod
    def _leaf_hash(path: Path) -> str | None:
        try:
            path_st = path.lstat()
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(path_st.st_mode) or not stat.S_ISREG(path_st.st_mode):
            return None
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def _publish_revision(self, commit_id: str) -> None:
        with self._locked():
            self._publish_revision_locked(commit_id)

    def _publish_revision_locked(self, commit_id: str, *, expected_previous: str | None = None) -> None:
        current = self._canonical_revision_locked()
        if current == commit_id:
            return
        if expected_previous is not None and current != expected_previous:
            raise JournalConflictError("journal canonical revision advanced after commit decision")
        self._mkdir_durable(self._meta_root)
        temp = self._meta_root / ".canonical-revision.tmp"
        self._write_fsynced(temp, (commit_id + "\n").encode("ascii"))
        os.replace(temp, self._revision_path)
        self._fsync_dir(self._meta_root)

    def _restore_previous_revision_locked(self, previous_revision: str, commit_id: str) -> None:
        current = self._canonical_revision_locked()
        if current == previous_revision:
            return
        if current != commit_id:
            raise JournalConflictError("journal canonical revision advanced before rollback")
        if previous_revision:
            self._publish_revision_locked(previous_revision, expected_previous=commit_id)
            return
        if self._revision_path.exists():
            self._revision_path.unlink()
            self._fsync_dir(self._meta_root)

    def _remove_commit(self, commit_path: Path) -> None:
        self._validate_metadata_paths()
        self._validate_commit_path(commit_path)
        self._validate_artifact_directories(commit_path)
        shutil.rmtree(commit_path)
        self._fsync_dir(self._journal_root)

    def _archive_commit(self, commit_path: Path) -> Path:
        self._validate_commit_path(commit_path)
        self._validate_artifact_directories(commit_path)
        self._mkdir_durable(self._versions_root)
        archive = self._versions_root / commit_path.name
        if archive.is_symlink():
            raise ValueError(f"journal version path already exists: {archive}")
        if archive.exists():
            if not archive.is_dir():
                raise ValueError(f"journal version path already exists: {archive}")
            archive = self._versions_root / f"{commit_path.name}-{uuid4().hex}"
        self._checkpoint("before_archive")
        os.rename(commit_path, archive)
        self._checkpoint("after_archive_rename")
        self._fsync_dir(self._journal_root)
        self._checkpoint("after_archive_journal_fsync")
        self._fsync_dir(self._versions_root)
        self._checkpoint("after_archive_versions_fsync")
        self._remove_empty_journal_root()
        self._checkpoint("after_archive_cleanup")
        return archive

    def _remove_empty_journal_root(self) -> None:
        self._validate_metadata_paths()
        try:
            self._journal_root.rmdir()
        except OSError:
            return
        self._fsync_dir(self._meta_root)

    def _target_path(self, rel: str) -> Path:
        target = self.root.joinpath(*Path(rel).parts)
        self._assert_safe_parents(target)
        return target

    def _safe_external_target(self, target: Path) -> Path:
        candidate = Path(target)
        if candidate.is_absolute():
            try:
                relative = candidate.relative_to(self.root)
            except ValueError as exc:
                raise ValueError(f"safe target escapes vault root: {target}") from exc
        else:
            relative = candidate
        rel = self._validate_relative(relative)
        path = self.root.joinpath(*Path(rel).parts)
        self._assert_safe_parents(path)
        return path

    def _assert_expected_state(
        self,
        expected_files: Mapping[Path, bytes | None] | None,
        expected_revision: str | None,
    ) -> None:
        if expected_revision is not None and self.canonical_revision != expected_revision:
            raise JournalConflictError("journal expected state changed: canonical revision")
        for target, expected in (expected_files or {}).items():
            rel = self._validate_relative(target)
            path = self._target_path(rel)
            try:
                target_st = path.lstat()
            except FileNotFoundError:
                actual = None
            else:
                if stat.S_ISLNK(target_st.st_mode) or not stat.S_ISREG(target_st.st_mode):
                    raise JournalConflictError(f"journal expected state changed: {rel}")
                actual = path.read_bytes()
            if actual != expected:
                raise JournalConflictError(f"journal expected state changed: {rel}")

    def _validate_regular_leaf(self, path: Path, *, allow_missing: bool) -> bool:
        self._assert_safe_parents(path)
        try:
            leaf = path.lstat()
        except FileNotFoundError:
            if allow_missing:
                return False
            raise
        if stat.S_ISLNK(leaf.st_mode):
            raise ValueError(f"safe target is a symlink: {path}")
        if not stat.S_ISREG(leaf.st_mode):
            raise ValueError(f"safe target is not a regular file: {path}")
        return True

    def _assert_safe_parents(self, target: Path) -> None:
        try:
            root_st = self.root.lstat()
        except OSError as exc:
            raise ValueError(f"vault root is unavailable: {self.root}") from exc
        if stat.S_ISLNK(root_st.st_mode) or not stat.S_ISDIR(root_st.st_mode):
            raise ValueError(f"vault root must be an existing real directory: {self.root}")
        current = self.root
        for part in target.relative_to(self.root).parts[:-1]:
            current /= part
            try:
                current_st = current.lstat()
            except FileNotFoundError:
                return
            if stat.S_ISLNK(current_st.st_mode):
                raise ValueError(f"journal target traverses a symlink: {target}")
            if not stat.S_ISDIR(current_st.st_mode):
                raise ValueError(f"journal target parent is not a directory: {current}")

    @contextmanager
    def _locked(self) -> Iterator[None]:
        with self._thread_lock, self._process_locked():
            yield

    @contextmanager
    def _process_locked(self) -> Iterator[None]:
        if self._lock_depth:
            self._lock_depth += 1
            try:
                yield
            finally:
                self._lock_depth -= 1
            return

        self._validate_metadata_paths()
        self._mkdir_durable(self._meta_root)
        self._validate_metadata_paths()
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self._lock_path, flags, 0o600)
        try:
            opened = os.fstat(descriptor)
            linked = self._lock_path.lstat()
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_dev != linked.st_dev
                or opened.st_ino != linked.st_ino
                or opened.st_nlink != 1
            ):
                raise ValueError(f"journal lock path is not an anchored regular file: {self._lock_path}")
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            linked = self._lock_path.lstat()
            if opened.st_dev != linked.st_dev or opened.st_ino != linked.st_ino:
                raise ValueError(f"journal lock path changed while locking: {self._lock_path}")
            self._lock_depth = 1
            self._validate_metadata_paths()
            yield
        finally:
            self._lock_depth = 0
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _validate_metadata_paths(self) -> None:
        for path, expected_directory in (
            (self._meta_root, True),
            (self._journal_root, True),
            (self._versions_root, True),
            (self._revision_path, False),
            (self._lock_path, False),
        ):
            if path.is_symlink():
                raise ValueError(f"journal metadata path is a symlink: {path}")
            resolved = path.resolve(strict=False)
            if resolved != self.root and self.root not in resolved.parents:
                raise ValueError(f"journal metadata escapes vault root: {path}")
            if path.exists() and expected_directory != path.is_dir():
                expected = "directory" if expected_directory else "file"
                raise ValueError(f"journal metadata path is not a {expected}: {path}")

    def _validate_commit_path(self, commit_path: Path) -> None:
        if commit_path.parent != self._journal_root or commit_path.is_symlink():
            raise ValueError(f"journal commit path is a symlink or outside journal root: {commit_path}")
        resolved = commit_path.resolve(strict=False)
        if self.root not in resolved.parents or not commit_path.is_dir():
            raise ValueError(f"journal commit path is invalid: {commit_path}")

    def _validate_internal_file(self, path: Path) -> None:
        if path.is_symlink():
            raise ValueError(f"journal metadata file is a symlink: {path}")
        resolved = path.resolve(strict=False)
        if self.root not in resolved.parents:
            raise ValueError(f"journal metadata file escapes vault root: {path}")

    def _validate_artifact_directories(self, commit_path: Path) -> None:
        self._validate_commit_path(commit_path)
        for name in ("staged", "backups", "publish", "displaced", "rejected", "markers"):
            directory = commit_path / name
            if directory.is_symlink():
                raise ValueError(f"journal artifact directory is a symlink: {directory}")
            resolved = directory.resolve(strict=False)
            if resolved != commit_path and commit_path not in resolved.parents:
                raise ValueError(f"journal artifact directory escapes commit: {directory}")
            if directory.exists() and not directory.is_dir():
                raise ValueError(f"journal artifact path is not a directory: {directory}")

    def _validate_artifact_paths(self, commit_path: Path, manifest: dict) -> None:
        self._validate_artifact_directories(commit_path)
        for row in manifest["files"]:
            self._validate_artifact_file(commit_path, commit_path / row["staged"], require_file=False)
            self._validate_artifact_file(commit_path, commit_path / row["backup"], require_file=False)
            self._validate_artifact_file(commit_path, commit_path / row["publish"], require_file=False)

    def _validate_artifact_file(self, commit_path: Path, artifact: Path, *, require_file: bool) -> None:
        try:
            relative = artifact.relative_to(commit_path)
        except ValueError as exc:
            raise ValueError(f"journal artifact escapes commit: {artifact}") from exc
        current = commit_path
        for part in relative.parts[:-1]:
            current /= part
            if current.is_symlink():
                raise ValueError(f"journal artifact parent is a symlink: {current}")
            if current.exists() and not current.is_dir():
                raise ValueError(f"journal artifact parent is not a directory: {current}")
        if artifact.is_symlink():
            raise ValueError(f"journal artifact file is a symlink: {artifact}")
        resolved = artifact.resolve(strict=False)
        if commit_path not in resolved.parents:
            raise ValueError(f"journal artifact escapes commit: {artifact}")
        if artifact.exists() and not artifact.is_file():
            raise ValueError(f"journal artifact is not a file: {artifact}")
        if require_file and not artifact.is_file():
            raise FileNotFoundError(artifact)

    def _read_artifact(self, commit_path: Path, relative: str) -> bytes:
        artifact = commit_path / relative
        self._validate_artifact_file(commit_path, artifact, require_file=True)
        return artifact.read_bytes()

    def _mkdir_durable(self, path: Path) -> None:
        try:
            relative = path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError(f"journal directory escapes vault root: {path}") from exc
        if not self.root.is_dir() or self.root.is_symlink():
            raise ValueError(f"vault root must be an existing real directory: {self.root}")
        current = self.root
        for part in relative.parts:
            child = current / part
            try:
                child_st = child.lstat()
            except FileNotFoundError:
                try:
                    child.mkdir()
                except FileExistsError:
                    pass
                else:
                    self._fsync_dir(current)
                child_st = child.lstat()
            if stat.S_ISLNK(child_st.st_mode):
                raise ValueError(f"journal directory is a symlink: {child}")
            if not stat.S_ISDIR(child_st.st_mode):
                raise ValueError(f"journal directory path is not a directory: {child}")
            current = child

    @staticmethod
    def _validate_relative(path: Path) -> str:
        if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError(f"journal target must be vault-relative: {path}")
        if path.parts[0] == ".ntrp":
            raise ValueError("journal cannot target its metadata directory")
        return path.as_posix()

    @staticmethod
    def _artifact_path(commit_path: Path, raw: object, expected_dir: str) -> Path:
        if not isinstance(raw, str):
            raise TypeError("journal artifact path must be text")
        path = Path(raw)
        if (
            path.is_absolute()
            or len(path.parts) != 2
            or path.parts[0] != expected_dir
            or path.parts[1] in {"", ".", ".."}
        ):
            raise ValueError("invalid journal artifact path")
        return commit_path / path

    def _write_fsynced(self, path: Path, content: bytes) -> None:
        self._mkdir_durable(path.parent)
        self._validate_internal_file(path)
        with path.open("wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())

    @staticmethod
    def _fsync_dir(path: Path) -> None:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    @staticmethod
    def _sha256(content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    @staticmethod
    def _valid_hash(value: object) -> bool:
        return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX

    def _checkpoint(self, point: str) -> None:
        """Test-only failure boundary; production commits leave this as a no-op."""
