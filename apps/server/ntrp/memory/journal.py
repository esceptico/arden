"""Crash-consistent commits for canonical vault file sets."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

_VERSION = 1
_HEX = frozenset("0123456789abcdef")


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
        self._revision_path = self._meta_root / "canonical-revision"

    @property
    def canonical_revision(self) -> str:
        self._validate_metadata_paths()
        try:
            revision = self._revision_path.read_text(encoding="ascii").strip()
        except OSError:
            return ""
        return revision if self._valid_hash(revision) else ""

    def prepare(self, files: Mapping[Path, bytes], *, _allow_migration_meta: bool = False) -> PreparedCommit:
        self._validate_metadata_paths()
        rows: list[dict[str, object]] = []
        payloads: list[tuple[bytes, bytes]] = []
        seen: set[str] = set()
        for index, (target, content) in enumerate(sorted(files.items(), key=lambda item: item[0].as_posix())):
            rel = self._validate_relative(target, allow_migration_meta=_allow_migration_meta)
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
            rows.append(
                {
                    "target": rel,
                    "staged": staged_name,
                    "backup": backup_name,
                    "sha256": self._sha256(content),
                    "backup_sha256": self._sha256(backup),
                    "existed": existed,
                }
            )
            payloads.append((content, backup))
        if not rows:
            raise ValueError("journal commit requires at least one file")

        manifest_bytes = (
            json.dumps(
                {"version": _VERSION, "previous_revision": self.canonical_revision, "files": rows},
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
        self._checkpoint("before_prepare_complete")
        self._fsync_dir(commit_path / "staged")
        self._fsync_dir(commit_path / "backups")
        self._fsync_dir(commit_path)
        self._write_fsynced(commit_path / "manifest.json", manifest_bytes)
        self._fsync_dir(commit_path)
        self._write_fsynced(commit_path / "PREPARED", b"")
        self._fsync_dir(commit_path)
        self._fsync_dir(self._journal_root)
        self._checkpoint("after_prepared")
        return PreparedCommit(commit_id=commit_id, manifest_hash=commit_id, path=commit_path)

    def commit(self, files: Mapping[Path, bytes]) -> str:
        self.recover()
        prepared = self.prepare(files)
        manifest = self._load_manifest(prepared.path)
        self._finish(prepared.path, manifest, prepared.commit_id)
        return prepared.manifest_hash

    def commit_migration(self, files: Mapping[Path, bytes]) -> str:
        self.recover()
        prepared = self.prepare(files, _allow_migration_meta=True)
        manifest = self._load_manifest(prepared.path)
        self._finish(prepared.path, manifest, prepared.commit_id)
        return prepared.manifest_hash

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
        self._validate_metadata_paths()
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
            rolled_back_marker = commit_path / "ROLLED_BACK"
            self._validate_internal_file(rolled_back_marker)
            if rolled_back_marker.is_file():
                if not self._backup_targets_match(manifest):
                    if not self._artifacts_match(commit_path, manifest, "backup", "backup_sha256"):
                        raise RuntimeError(f"journal {commit_id} cannot resume rollback")
                    self._restore(commit_path, manifest)
                self._remove_commit(commit_path)
            elif prefer_rollback and self._artifacts_match(commit_path, manifest, "backup", "backup_sha256"):
                self._restore(commit_path, manifest)
                self._remove_commit(commit_path)
            elif self._targets_match(manifest):
                self._publish_revision(commit_id)
                self._remove_commit(commit_path)
            elif self._artifacts_match(commit_path, manifest, "staged", "sha256"):
                self._finish(commit_path, manifest, commit_id)
            elif self._artifacts_match(commit_path, manifest, "backup", "backup_sha256"):
                self._restore(commit_path, manifest)
                self._remove_commit(commit_path)
            else:
                raise RuntimeError(f"journal {commit_id} has no valid staged set or backup set")
            recovered.append(commit_id)
        self._remove_empty_journal_root()
        return tuple(recovered)

    def _finish(self, commit_path: Path, manifest: dict, commit_id: str) -> None:
        for index, row in enumerate(manifest["files"]):
            content = self._read_artifact(commit_path, row["staged"])
            self._replace_target(row["target"], content, commit_id, index)
            self._checkpoint(f"after_replace:{index}")
        if not self._targets_match(manifest):
            raise RuntimeError(f"journal {commit_id} target hash validation failed")
        self._write_fsynced(commit_path / "COMMITTED", b"")
        self._fsync_dir(commit_path)
        self._checkpoint("after_committed")
        self._publish_revision(commit_id)
        self._remove_commit(commit_path)
        self._remove_empty_journal_root()

    def _restore(self, commit_path: Path, manifest: dict) -> None:
        for index, row in enumerate(manifest["files"]):
            target = self._target_path(row["target"])
            if row["existed"]:
                self._replace_target(row["target"], self._read_artifact(commit_path, row["backup"]), commit_path.name, index)
            else:
                target.unlink(missing_ok=True)
                self._fsync_dir(target.parent)
        if not self._backup_targets_match(manifest):
            raise RuntimeError(f"journal {commit_path.name} backup hash validation failed")
        self._restore_revision(manifest["previous_revision"])
        self._write_fsynced(commit_path / "ROLLED_BACK", b"")
        self._fsync_dir(commit_path)

    def _load_manifest(self, commit_path: Path) -> dict:
        self._validate_commit_path(commit_path)
        manifest_path = commit_path / "manifest.json"
        self._validate_internal_file(manifest_path)
        try:
            raw = manifest_path.read_bytes()
            manifest = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"invalid journal manifest: {commit_path.name}") from exc
        if self._sha256(raw) != commit_path.name or manifest.get("version") != _VERSION:
            raise RuntimeError(f"invalid journal manifest identity: {commit_path.name}")
        rows = manifest.get("files")
        previous_revision = manifest.get("previous_revision", "")
        if not isinstance(previous_revision, str) or (previous_revision and not self._valid_hash(previous_revision)):
            raise RuntimeError(f"invalid previous canonical revision: {commit_path.name}")
        manifest["previous_revision"] = previous_revision
        if not isinstance(rows, list) or not rows:
            raise RuntimeError(f"invalid journal file set: {commit_path.name}")
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                raise RuntimeError(f"invalid journal file entry: {commit_path.name}")
            try:
                target = self._validate_relative(Path(row["target"]), allow_migration_meta=True)
                staged = self._artifact_path(commit_path, row["staged"], "staged")
                backup = self._artifact_path(commit_path, row["backup"], "backups")
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

    def _artifacts_match(self, commit_path: Path, manifest: dict, field: str, hash_field: str) -> bool:
        for row in manifest["files"]:
            try:
                content = self._read_artifact(commit_path, row[field])
            except FileNotFoundError:
                return False
            if self._sha256(content) != row[hash_field]:
                return False
        return True

    def _replace_target(self, rel: str, content: bytes, commit_id: str, index: int) -> None:
        target = self._target_path(rel)
        self._mkdir_durable(target.parent)
        self._assert_safe_parents(target)
        if target.is_symlink():
            raise ValueError(f"journal target is a symlink: {rel}")
        temp = target.parent / f".ntrp-{commit_id[:12]}-{index}.tmp"
        try:
            self._write_fsynced(temp, content)
            os.replace(temp, target)
            self._fsync_dir(target.parent)
        finally:
            temp.unlink(missing_ok=True)

    def _publish_revision(self, commit_id: str) -> None:
        self._validate_metadata_paths()
        self._mkdir_durable(self._meta_root)
        temp = self._meta_root / ".canonical-revision.tmp"
        self._write_fsynced(temp, (commit_id + "\n").encode("ascii"))
        os.replace(temp, self._revision_path)
        self._fsync_dir(self._meta_root)

    def _restore_revision(self, revision: str) -> None:
        if revision:
            self._publish_revision(revision)
            return
        self._validate_metadata_paths()
        if self._revision_path.exists():
            self._revision_path.unlink()
            self._fsync_dir(self._meta_root)

    def _remove_commit(self, commit_path: Path) -> None:
        self._validate_metadata_paths()
        self._validate_commit_path(commit_path)
        self._validate_artifact_directories(commit_path)
        shutil.rmtree(commit_path)
        self._fsync_dir(self._journal_root)

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

    def _validate_metadata_paths(self) -> None:
        for path, expected_directory in (
            (self._meta_root, True),
            (self._journal_root, True),
            (self._revision_path, False),
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
        for name in ("staged", "backups"):
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
                child.mkdir()
                self._fsync_dir(current)
                child_st = child.lstat()
            if stat.S_ISLNK(child_st.st_mode):
                raise ValueError(f"journal directory is a symlink: {child}")
            if not stat.S_ISDIR(child_st.st_mode):
                raise ValueError(f"journal directory path is not a directory: {child}")
            current = child

    @staticmethod
    def _validate_relative(path: Path, *, allow_migration_meta: bool = False) -> str:
        if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
            raise ValueError(f"journal target must be vault-relative: {path}")
        if path.parts[0] == ".ntrp" and not (
            allow_migration_meta and path == Path(".ntrp/maintenance/migration-v2.json")
        ):
            raise ValueError("journal cannot target its metadata directory")
        return path.as_posix()

    @staticmethod
    def _artifact_path(commit_path: Path, raw: object, expected_dir: str) -> Path:
        if not isinstance(raw, str):
            raise TypeError("journal artifact path must be text")
        path = Path(raw)
        if path.is_absolute() or len(path.parts) != 2 or path.parts[0] != expected_dir or path.parts[1] in {"", ".", ".."}:
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
