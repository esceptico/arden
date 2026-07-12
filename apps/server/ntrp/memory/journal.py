"""Crash-consistent commits for canonical vault file sets."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

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
        try:
            revision = self._revision_path.read_text(encoding="ascii").strip()
        except OSError:
            return ""
        return revision if self._valid_hash(revision) else ""

    def prepare(self, files: Mapping[Path, bytes]) -> PreparedCommit:
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

        manifest_bytes = (json.dumps({"version": _VERSION, "files": rows}, sort_keys=True, separators=(",", ":")) + "\n").encode()
        commit_id = self._sha256(manifest_bytes)
        commit_path = self._journal_root / commit_id
        self._journal_root.mkdir(parents=True, exist_ok=True)
        commit_path.mkdir()
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

    def recover(self) -> tuple[str, ...]:
        if not self._journal_root.is_dir():
            return ()
        recovered: list[str] = []
        for commit_path in sorted(path for path in self._journal_root.iterdir() if path.is_dir()):
            commit_id = commit_path.name
            if not (commit_path / "PREPARED").is_file():
                self._remove_commit(commit_path)
                recovered.append(commit_id)
                continue
            manifest = self._load_manifest(commit_path)
            if (commit_path / "ROLLED_BACK").is_file():
                if not self._backup_targets_match(manifest):
                    if not self._artifacts_match(commit_path, manifest, "backup", "backup_sha256"):
                        raise RuntimeError(f"journal {commit_id} cannot resume rollback")
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
            content = (commit_path / row["staged"]).read_bytes()
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
                self._replace_target(row["target"], (commit_path / row["backup"]).read_bytes(), commit_path.name, index)
            else:
                target.unlink(missing_ok=True)
                self._fsync_dir(target.parent)
        if not self._backup_targets_match(manifest):
            raise RuntimeError(f"journal {commit_path.name} backup hash validation failed")
        self._write_fsynced(commit_path / "ROLLED_BACK", b"")
        self._fsync_dir(commit_path)

    def _load_manifest(self, commit_path: Path) -> dict:
        manifest_path = commit_path / "manifest.json"
        try:
            raw = manifest_path.read_bytes()
            manifest = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"invalid journal manifest: {commit_path.name}") from exc
        if self._sha256(raw) != commit_path.name or manifest.get("version") != _VERSION:
            raise RuntimeError(f"invalid journal manifest identity: {commit_path.name}")
        rows = manifest.get("files")
        if not isinstance(rows, list) or not rows:
            raise RuntimeError(f"invalid journal file set: {commit_path.name}")
        seen: set[str] = set()
        for row in rows:
            if not isinstance(row, dict):
                raise RuntimeError(f"invalid journal file entry: {commit_path.name}")
            try:
                target = self._validate_relative(Path(row["target"]))
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

    @staticmethod
    def _artifacts_match(commit_path: Path, manifest: dict, field: str, hash_field: str) -> bool:
        for row in manifest["files"]:
            artifact = commit_path / row[field]
            if artifact.is_symlink() or not artifact.is_file() or VaultJournal._sha256(artifact.read_bytes()) != row[hash_field]:
                return False
        return True

    def _replace_target(self, rel: str, content: bytes, commit_id: str, index: int) -> None:
        target = self._target_path(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
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
        self._meta_root.mkdir(parents=True, exist_ok=True)
        temp = self._meta_root / ".canonical-revision.tmp"
        self._write_fsynced(temp, (commit_id + "\n").encode("ascii"))
        os.replace(temp, self._revision_path)
        self._fsync_dir(self._meta_root)

    def _remove_commit(self, commit_path: Path) -> None:
        shutil.rmtree(commit_path)
        self._fsync_dir(self._journal_root)

    def _remove_empty_journal_root(self) -> None:
        try:
            self._journal_root.rmdir()
        except OSError:
            return
        self._fsync_dir(self._meta_root)

    def _target_path(self, rel: str) -> Path:
        target = self.root.joinpath(*Path(rel).parts)
        self._assert_safe_parents(target)
        return target

    def _assert_safe_parents(self, target: Path) -> None:
        current = self.root
        for part in target.relative_to(self.root).parts[:-1]:
            current /= part
            if current.exists() and current.is_symlink():
                raise ValueError(f"journal target traverses a symlink: {target}")

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
        if path.is_absolute() or len(path.parts) != 2 or path.parts[0] != expected_dir or path.parts[1] in {"", ".", ".."}:
            raise ValueError("invalid journal artifact path")
        return commit_path / path

    @staticmethod
    def _write_fsynced(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
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
