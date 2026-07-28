"""Durable immutable-object and ref storage for managed-file revisions."""

import fcntl
import hashlib
import json
import os
import re
import stat
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from arden.revisions._anchored import AnchoredDirectory, canonical_absolute, ensure_absolute_directory
from arden.revisions.errors import CorruptRepositoryError, RevisionConflictError, UnsafePathError

FORMAT_VERSION = 1
_HEX = frozenset("0123456789abcdef")
_TEMP_FILE = re.compile(r"^\.(?P<target>.+)\.(?P<nonce>[0-9a-f]{32})\.tmp$")


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def valid_hash(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(character in _HEX for character in value)


def canonical_jsonl(value: Mapping[str, object]) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def decode_jsonl(content: bytes, *, label: str) -> dict[str, object]:
    if not content.endswith(b"\n") or content.count(b"\n") != 1:
        raise CorruptRepositoryError(f"{label} is not one canonical JSONL record")
    try:
        decoded = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CorruptRepositoryError(f"{label} is invalid JSONL") from exc
    if not isinstance(decoded, dict):
        raise CorruptRepositoryError(f"{label} must contain one JSON object")
    if canonical_jsonl(decoded) != content:
        raise CorruptRepositoryError(f"{label} is not canonical JSONL")
    return decoded


class HistoryStorage:
    """Owns low-level immutable objects, the current ref, and repository locking."""

    def __init__(self, working_root: Path, history_root: Path) -> None:
        self.working_root = canonical_absolute(working_root)
        self.root = canonical_absolute(history_root)
        if self.working_root == self.root:
            raise UnsafePathError("working root and history root must differ")
        if self.root in self.working_root.parents:
            raise UnsafePathError("history root must not contain the working root")
        self.objects = self.root / "objects"
        self.blobs = self.objects / "blobs"
        self.trees = self.objects / "trees"
        self.commits = self.root / "commits"
        self.published = self.root / "published"
        self.refs = self.root / "refs"
        self.transactions = self.root / "transactions"
        self.conflicts = self.root / "conflicts"
        self.idempotency = self.root / "idempotency"
        self.current_ref = self.refs / "current.jsonl"
        self.lock_path = self.root / "repository.lock"
        self._thread_lock = threading.RLock()
        self._lock_depth = 0
        ensure_absolute_directory(self.working_root)
        ensure_absolute_directory(self.root)
        self._working_anchor = AnchoredDirectory(self.working_root)
        self._anchor = AnchoredDirectory(self.root)

    @property
    def object_directories(self) -> tuple[Path, Path, Path]:
        return self.blobs, self.trees, self.commits

    def initialize(self) -> None:
        self._working_anchor.validate_binding()
        self._anchor.validate_binding()
        for path in (
            self.objects,
            self.blobs,
            self.trees,
            self.commits,
            self.published,
            self.refs,
            self.transactions,
            self.conflicts,
            self.idempotency,
        ):
            self._anchor.ensure_directory(self._relative(path))
        self._validate_layout()
        if self._working_anchor.device() != self._anchor.device():
            raise UnsafePathError("working root and history root must be on the same filesystem")

    @contextmanager
    def locked(self) -> Iterator[None]:
        with self._thread_lock:
            self.initialize()
            self._validate_layout()
            descriptor = self._anchor.open_lock(self._relative(self.lock_path))
            try:
                opened = os.fstat(descriptor)
                if not stat.S_ISREG(opened.st_mode):
                    raise UnsafePathError("repository lock is not a regular file")
                if self._lock_depth == 0:
                    fcntl.flock(descriptor, fcntl.LOCK_EX)
                self._lock_depth += 1
                self._validate_layout()
                yield
            finally:
                self._lock_depth -= 1
                if self._lock_depth == 0:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    def write_blob(self, content: bytes) -> str:
        if not isinstance(content, bytes):
            raise TypeError("blob content must be bytes")
        object_id = sha256(content)
        self._write_immutable(self.blobs / object_id, content, object_id)
        return object_id

    def read_blob(self, object_id: str) -> bytes:
        self._require_hash(object_id, "blob")
        return self._read_immutable(self.blobs / object_id, object_id, "blob")

    def blob_size(self, object_id: str) -> int:
        """Return an immutable blob's byte length without reading its content."""
        return self._immutable_size(self.blobs / object_id, object_id, "blob")

    def commit_size(self, object_id: str) -> int:
        """Return an immutable commit record's byte length without reading it."""
        return self._immutable_size(self.commits / f"{object_id}.jsonl", object_id, "commit")

    def publish_commit(self, object_id: str) -> None:
        """Write the durable O(1) certificate for a single-ref published commit."""

        self._require_hash(object_id, "published commit")
        path = self.published / object_id
        if self.metadata_exists(path):
            if self.read_metadata(path) != b"":
                raise CorruptRepositoryError(f"published commit marker is invalid: {object_id}")
            return
        self.atomic_write(path, b"", no_replace=True)
        self.fsync_dir(self.published)

    def is_published(self, object_id: str) -> bool:
        self._require_hash(object_id, "published commit")
        path = self.published / object_id
        if not self.metadata_exists(path):
            return False
        if self.read_metadata(path) != b"":
            raise CorruptRepositoryError(f"published commit marker is invalid: {object_id}")
        return True

    def _immutable_size(self, path: Path, object_id: str, label: str) -> int:
        self._require_hash(object_id, label)
        try:
            metadata = self._anchor.stat(self._relative(path))
        except OSError as exc:
            raise CorruptRepositoryError(f"cannot stat {label} {object_id}") from exc
        if metadata is None:
            raise CorruptRepositoryError(f"required {label} {object_id} is missing")
        return metadata.st_size

    def write_tree(self, record: Mapping[str, object]) -> str:
        return self._write_json_object(self.trees, "tree", record)

    def read_tree(self, object_id: str) -> dict[str, object]:
        return self._read_json_object(self.trees, "tree", object_id)

    def write_commit(self, record: Mapping[str, object]) -> str:
        return self._write_json_object(self.commits, "commit", record)

    def read_commit(self, object_id: str) -> dict[str, object]:
        return self._read_json_object(self.commits, "commit", object_id)

    def read_ref(self) -> str | None:
        content = self._anchor.read_file(
            self._relative(self.current_ref),
            allow_missing=True,
        )
        if content is None:
            return None
        record = decode_jsonl(content, label="current ref")
        if set(record) != {"commit_id", "version"} or record.get("version") != FORMAT_VERSION:
            raise CorruptRepositoryError("current ref has an unsupported shape")
        commit_id = record.get("commit_id")
        self._require_hash(commit_id, "current ref commit")
        return commit_id

    def compare_and_swap_ref(self, expected: str | None, commit_id: str | None) -> None:
        if commit_id is not None:
            self._require_hash(commit_id, "commit")
        current = self.read_ref()
        if current != expected:
            raise RevisionConflictError(f"current ref changed: expected {expected!r}, found {current!r}")
        if commit_id is None:
            self.unlink_metadata(self.current_ref)
            return
        self.atomic_write_jsonl(
            self.current_ref,
            {"version": FORMAT_VERSION, "commit_id": commit_id},
        )

    def compensate_ref(self, expected: str, commit_id: str | None) -> None:
        """Restore a just-written ref through the pinned history root."""
        self._require_hash(expected, "expected commit")
        if commit_id is not None:
            self._require_hash(commit_id, "commit")
        expected_content = canonical_jsonl(
            {"version": FORMAT_VERSION, "commit_id": expected},
        )
        replacement = (
            None
            if commit_id is None
            else canonical_jsonl(
                {"version": FORMAT_VERSION, "commit_id": commit_id},
            )
        )
        self._anchor.compare_and_swap_file_pinned(
            self._relative(self.current_ref),
            expected=expected_content,
            replacement=replacement,
        )

    def idempotency_path(self, key_hash: str) -> Path:
        self._require_hash(key_hash, "idempotency key")
        return self.idempotency / f"{key_hash}.jsonl"

    def read_idempotency(self, key_hash: str) -> dict[str, object] | None:
        path = self.idempotency_path(key_hash)
        content = self._anchor.read_file(self._relative(path), allow_missing=True)
        if content is None:
            return None
        record = decode_jsonl(content, label=f"idempotency record {key_hash}")
        if (
            set(record) != {"commit_id", "key_hash", "request_hash", "version"}
            or record.get("version") != FORMAT_VERSION
            or record.get("key_hash") != key_hash
        ):
            raise CorruptRepositoryError(f"idempotency record {key_hash} has an unsupported shape")
        self._require_hash(record.get("request_hash"), "idempotency request")
        self._require_hash(record.get("commit_id"), "idempotency commit")
        return record

    def write_idempotency(self, key_hash: str, request_hash: str, commit_id: str) -> None:
        existing = self.read_idempotency(key_hash)
        expected = {
            "version": FORMAT_VERSION,
            "key_hash": key_hash,
            "request_hash": request_hash,
            "commit_id": commit_id,
        }
        if existing is not None:
            if existing != expected:
                raise CorruptRepositoryError(f"idempotency record {key_hash} changed")
            return
        self.atomic_write_jsonl(self.idempotency_path(key_hash), expected, no_replace=True)

    def atomic_write_jsonl(
        self,
        path: Path,
        record: Mapping[str, object],
        *,
        no_replace: bool = False,
    ) -> None:
        self.atomic_write(path, canonical_jsonl(record), no_replace=no_replace)

    def atomic_write(self, path: Path, content: bytes, *, no_replace: bool = False) -> None:
        try:
            self._anchor.atomic_write(
                self._relative(path),
                content,
                no_replace=no_replace,
            )
        except RevisionConflictError as exc:
            raise RevisionConflictError(f"metadata target already exists: {path.name}") from exc

    def fsync_dir(self, path: Path) -> None:
        self._anchor.fsync_directory(self._relative(path, allow_root=True))

    def cleanup_temporary_files(self) -> tuple[str, ...]:
        """Remove only private atomic-write leftovers with fully validated names."""
        removed: list[str] = []
        specifications = (
            (self.blobs, lambda target: valid_hash(target)),
            (
                self.trees,
                lambda target: target.endswith(".jsonl") and valid_hash(target.removesuffix(".jsonl")),
            ),
            (
                self.commits,
                lambda target: target.endswith(".jsonl") and valid_hash(target.removesuffix(".jsonl")),
            ),
            (self.published, valid_hash),
            (self.refs, lambda target: target == self.current_ref.name),
            (
                self.idempotency,
                lambda target: target.endswith(".jsonl") and valid_hash(target.removesuffix(".jsonl")),
            ),
        )
        for directory, valid_target in specifications:
            changed = False
            for name in self._anchor.list(self._relative(directory)):
                match = _TEMP_FILE.fullmatch(name)
                if match is None:
                    continue
                if not valid_target(match.group("target")):
                    raise CorruptRepositoryError(f"invalid private temporary file: {name}")
                path = directory / name
                self._anchor.stat(self._relative(path))
                self._anchor.unlink(self._relative(path))
                removed.append(str(path.relative_to(self.root)))
                changed = True
            if changed:
                self.fsync_dir(directory)
        return tuple(removed)

    def has_pending_transactions(self) -> bool:
        return bool(self._anchor.list(self._relative(self.transactions)))

    def validate_working_binding(self) -> None:
        self._working_anchor.validate_binding()

    def validate_metadata_file(self, path: Path, *, allow_missing: bool = False) -> bool:
        result = self._anchor.stat(
            self._relative(path),
            allow_missing=allow_missing,
        )
        return result is not None

    def ensure_metadata_directory(self, path: Path) -> None:
        self._anchor.ensure_directory(self._relative(path))

    def validate_metadata_directory(self, path: Path) -> None:
        self._anchor.validate_directory(self._relative(path, allow_root=True))

    def remove_tree(self, path: Path) -> None:
        """Remove a validated metadata subtree without following links."""
        try:
            entry = self._anchor.entry_stat(
                self._relative(path),
                allow_missing=True,
            )
        except CorruptRepositoryError:
            return
        if entry is None:
            return
        if not stat.S_ISDIR(entry.st_mode):
            raise UnsafePathError(f"metadata path is not a directory: {path}")
        self._anchor.remove_tree(self._relative(path))

    def read_metadata(self, path: Path) -> bytes:
        content = self._anchor.read_file(self._relative(path))
        if content is None:
            raise CorruptRepositoryError(f"required metadata file is missing: {path.name}")
        return content

    def metadata_exists(self, path: Path) -> bool:
        return (
            self._anchor.entry_stat(
                self._relative(path),
                allow_missing=True,
            )
            is not None
        )

    def metadata_stat(self, path: Path, *, allow_missing: bool = False) -> os.stat_result | None:
        return self._anchor.entry_stat(
            self._relative(path),
            allow_missing=allow_missing,
        )

    def list_metadata(self, directory: Path) -> tuple[Path, ...]:
        relative = self._relative(directory, allow_root=True)
        return tuple(directory / name for name in self._anchor.list(relative))

    def unlink_metadata(self, path: Path, *, missing_ok: bool = False) -> None:
        self._anchor.unlink(self._relative(path), missing_ok=missing_ok)

    def rename_metadata_directory(self, source: Path, target: Path) -> None:
        self._anchor.rename_directory_to(
            self._anchor,
            self._relative(source),
            self._relative(target),
        )

    def _write_json_object(self, directory: Path, kind: str, record: Mapping[str, object]) -> str:
        if record.get("version") != FORMAT_VERSION or record.get("kind") != kind:
            raise ValueError(f"{kind} record must declare version {FORMAT_VERSION}")
        content = canonical_jsonl(record)
        object_id = sha256(content)
        self._write_immutable(directory / f"{object_id}.jsonl", content, object_id)
        return object_id

    def _read_json_object(self, directory: Path, kind: str, object_id: str) -> dict[str, object]:
        self._require_hash(object_id, kind)
        content = self._read_immutable(directory / f"{object_id}.jsonl", object_id, kind)
        record = decode_jsonl(content, label=f"{kind} {object_id}")
        if record.get("version") != FORMAT_VERSION or record.get("kind") != kind:
            raise CorruptRepositoryError(f"{kind} {object_id} has an unsupported format")
        return record

    def _write_immutable(self, path: Path, content: bytes, object_id: str) -> None:
        self._anchor.write_immutable(
            self._relative(path),
            content,
            object_id,
        )

    def _read_immutable(self, path: Path, object_id: str, label: str) -> bytes:
        try:
            content = self._anchor.read_file(self._relative(path))
        except OSError as exc:
            raise CorruptRepositoryError(f"cannot read {label} {object_id}") from exc
        if content is None:
            raise CorruptRepositoryError(f"required {label} {object_id} is missing")
        if sha256(content) != object_id:
            raise CorruptRepositoryError(f"{label} {object_id} failed its content hash")
        return content

    def _validate_layout(self) -> None:
        self._working_anchor.validate_binding()
        self._anchor.validate_binding()
        self._working_anchor.validate_directory()
        self._anchor.validate_directory()
        for path in (
            self.objects,
            self.blobs,
            self.trees,
            self.commits,
            self.published,
            self.refs,
            self.transactions,
            self.conflicts,
            self.idempotency,
        ):
            self._anchor.validate_directory(self._relative(path))
        self._anchor.stat(self._relative(self.lock_path), allow_missing=True)
        self._anchor.stat(self._relative(self.current_ref), allow_missing=True)

    def directory_size(self, directory: Path, *, recursive: bool = False) -> tuple[int, int]:
        relative = self._relative(directory, allow_root=True)
        total = 0
        count = 0
        pending = [relative]
        while pending:
            parent = pending.pop()
            parent_path = self.root if parent == "." else self.root / parent
            for child in self.list_metadata(parent_path):
                child_relative = self._relative(child)
                child_stat = self._anchor.entry_stat(child_relative)
                assert child_stat is not None
                if stat.S_ISREG(child_stat.st_mode):
                    total += child_stat.st_size
                    count += 1
                elif stat.S_ISDIR(child_stat.st_mode):
                    if recursive:
                        pending.append(child_relative)
                else:
                    raise UnsafePathError(f"storage path is not regular: {child}")
        return total, count

    def _relative(self, path: Path, *, allow_root: bool = False) -> str:
        candidate = _absolute(path)
        try:
            relative = candidate.relative_to(self.root)
        except ValueError as exc:
            raise UnsafePathError(f"metadata path escapes repository history: {path}") from exc
        if relative == Path("."):
            if allow_root:
                return "."
            raise UnsafePathError(f"metadata path must be below repository history: {path}")
        return relative.as_posix()

    @staticmethod
    def _require_hash(value: object, label: str) -> None:
        if not valid_hash(value):
            raise CorruptRepositoryError(f"{label} is not a valid SHA-256 identifier")


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))
