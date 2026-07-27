"""Anchored, no-symlink access to current visible managed files."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath

from ._anchored import AnchoredDirectory, canonical_absolute, ensure_absolute_directory
from ._storage import sha256
from .errors import CorruptRepositoryError, RevisionConflictError, UnsafePathError


def normalize_relative(value: str) -> str:
    if not isinstance(value, str) or not value:
        raise UnsafePathError("managed path must be a non-empty string")
    if "\x00" in value or "\\" in value:
        raise UnsafePathError(f"managed path contains an unsafe character: {value!r}")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or value != parsed.as_posix():
        raise UnsafePathError(f"managed path must be normalized and relative: {value!r}")
    if any(part in {"", ".", ".."} for part in parsed.parts):
        raise UnsafePathError(f"managed path escapes its root: {value!r}")
    return parsed.as_posix()


def ancestor_collision(paths: list[str]) -> tuple[str, str] | None:
    ordered = sorted(paths)
    for index, path in enumerate(ordered[:-1]):
        candidate = ordered[index + 1]
        if candidate.startswith(path + "/"):
            return path, candidate
    return None


class MaterializedFiles:
    def __init__(self, root: Path, history_root: Path) -> None:
        self.root = canonical_absolute(root)
        self.history_root = canonical_absolute(history_root)
        ensure_absolute_directory(self.root)
        ensure_absolute_directory(self.history_root)
        self._current = AnchoredDirectory(self.root)
        self._history = AnchoredDirectory(self.history_root)

    def target(self, relative: str) -> Path:
        normalized = normalize_relative(relative)
        target = self.root.joinpath(*PurePosixPath(normalized).parts)
        if self.history_root == target or self.history_root in target.parents:
            raise UnsafePathError(f"managed path enters repository history: {normalized}")
        if self.root not in target.parents:
            raise UnsafePathError(f"managed path escapes its root: {normalized}")
        return target

    def read(
        self,
        relative: str,
        *,
        allow_missing: bool = False,
        require_standalone: bool = True,
    ) -> bytes | None:
        normalized = normalize_relative(relative)
        self.target(normalized)
        try:
            content = self._current.read_file(
                normalized,
                allow_missing=allow_missing,
                require_standalone=require_standalone,
            )
        except CorruptRepositoryError as exc:
            if allow_missing:
                return None
            raise RevisionConflictError(f"managed file is missing: {normalized}") from exc
        return content

    def hash(self, relative: str, *, require_standalone: bool = True) -> str | None:
        content = self.read(
            relative,
            allow_missing=True,
            require_standalone=require_standalone,
        )
        return None if content is None else sha256(content)

    def assert_state(self, relative: str, expected_blob: str | None) -> None:
        actual = self.hash(relative)
        if actual != expected_blob:
            raise RevisionConflictError(
                f"working file changed: {relative} expected {expected_blob!r}, found {actual!r}"
            )

    def prepare_parent(self, relative: str) -> Path:
        normalized = normalize_relative(relative)
        target = self.target(normalized)
        parent = PurePosixPath(normalized).parent.as_posix()
        self._current.ensure_directory(parent)
        if self._current.device(parent) != self._history.device():
            raise UnsafePathError(f"managed path is on an unsupported filesystem: {normalized}")
        return target

    def displace(self, relative: str, destination: Path, expected_blob: str) -> None:
        if not self.quarantine_if_matches(relative, destination, expected_blob):
            raise RevisionConflictError(f"managed file changed during displacement: {relative}")

    def install(self, source: Path, relative: str, expected_blob: str) -> None:
        normalized = normalize_relative(relative)
        self.prepare_parent(normalized)
        if self._current.exists(normalized):
            raise RevisionConflictError(f"managed target appeared before install: {relative}")
        source_relative = self._history_relative(source)
        source_content = self._history.read_file(source_relative)
        if source_content is None:
            raise RevisionConflictError(f"required transaction artifact is missing: {source.name}")
        if sha256(source_content) != expected_blob:
            raise RevisionConflictError(f"publish artifact is invalid: {relative}")
        try:
            self._current.atomic_write(
                normalized,
                source_content,
                no_replace=True,
            )
        except (OSError, RevisionConflictError) as exc:
            raise RevisionConflictError(f"could not install managed file: {relative}") from exc
        if self.hash(normalized, require_standalone=False) != expected_blob:
            raise RevisionConflictError(f"managed file changed during install: {relative}")

    def quarantine_if_matches(
        self,
        relative: str,
        destination: Path,
        expected_blob: str,
        *,
        missing_ok: bool = False,
    ) -> bool:
        normalized = normalize_relative(relative)
        self.prepare_parent(normalized)
        destination_relative = self._history_relative(destination)
        try:
            self._current.rename_to(
                self._history,
                normalized,
                destination_relative,
            )
        except FileNotFoundError:
            return missing_ok
        except CorruptRepositoryError:
            if missing_ok:
                return True
            raise RevisionConflictError(f"managed file is missing: {normalized}") from None
        except (OSError, RevisionConflictError) as exc:
            raise RevisionConflictError(f"could not quarantine managed file: {relative}") from exc
        try:
            displaced = self._history.read_file(destination_relative)
            matches = displaced is not None and sha256(displaced) == expected_blob
        except (CorruptRepositoryError, RevisionConflictError, UnsafePathError):
            matches = False
        if not matches:
            self._restore_entry_no_clobber(destination_relative, normalized)
            return False
        return True

    def restore_if_absent(self, source: Path, relative: str) -> bool:
        normalized = normalize_relative(relative)
        self.prepare_parent(normalized)
        if self._current.exists(normalized):
            return False
        content = self._history.read_file(self._history_relative(source))
        if content is None:
            raise RevisionConflictError(f"required transaction artifact is missing: {source.name}")
        try:
            self._current.atomic_write(
                normalized,
                content,
                no_replace=True,
            )
        except RevisionConflictError:
            return False
        except OSError as exc:
            raise RevisionConflictError(f"could not restore managed file: {relative}") from exc
        return True

    def _restore_entry_no_clobber(self, source: str, target: str) -> bool:
        if self._current.exists(target):
            return False
        try:
            self._history.rename_to(self._current, source, target)
        except (CorruptRepositoryError, OSError, RevisionConflictError, UnsafePathError):
            return False
        return True

    def _history_relative(self, path: Path) -> str:
        candidate = Path(os.path.abspath(os.fspath(path)))
        try:
            relative = candidate.relative_to(self.history_root)
        except ValueError as exc:
            raise UnsafePathError(f"transaction path escapes repository history: {path}") from exc
        if relative == Path("."):
            raise UnsafePathError("transaction artifact must be below repository history")
        return relative.as_posix()
