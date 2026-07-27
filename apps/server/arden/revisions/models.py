"""Domain-neutral value objects for managed-file revisions.

Repository implementations own path normalization, object hashing, persistence,
and conflict handling.  These models only express immutable revision intent and
results.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ResourceState(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


def _require_text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")


def _require_bytes(value: object, name: str) -> None:
    if not isinstance(value, bytes):
        raise TypeError(f"{name} must be bytes")


def _require_nonnegative(value: object, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


@dataclass(frozen=True)
class ResourceVersion:
    resource_id: str
    path: str
    blob_id: str
    state: ResourceState
    version_id: str

    def __post_init__(self) -> None:
        _require_text(self.resource_id, "resource_id")
        _require_text(self.path, "path")
        _require_text(self.blob_id, "blob_id")
        _require_text(self.version_id, "version_id")


@dataclass(frozen=True)
class Create:
    resource_id: str
    path: str
    content: bytes

    def __post_init__(self) -> None:
        _require_text(self.resource_id, "resource_id")
        _require_text(self.path, "path")
        _require_bytes(self.content, "content")


@dataclass(frozen=True)
class Update:
    resource_id: str
    expected_version: str
    content: bytes

    def __post_init__(self) -> None:
        _require_text(self.resource_id, "resource_id")
        _require_text(self.expected_version, "expected_version")
        _require_bytes(self.content, "content")


@dataclass(frozen=True)
class Move:
    resource_id: str
    expected_version: str
    path: str
    content: bytes | None = None

    def __post_init__(self) -> None:
        _require_text(self.resource_id, "resource_id")
        _require_text(self.expected_version, "expected_version")
        _require_text(self.path, "path")
        if self.content is not None:
            _require_bytes(self.content, "content")


@dataclass(frozen=True)
class Archive:
    resource_id: str
    expected_version: str

    def __post_init__(self) -> None:
        _require_text(self.resource_id, "resource_id")
        _require_text(self.expected_version, "expected_version")


@dataclass(frozen=True)
class Restore:
    resource_id: str
    expected_version: str
    path: str | None = None
    content: bytes | None = None

    def __post_init__(self) -> None:
        _require_text(self.resource_id, "resource_id")
        _require_text(self.expected_version, "expected_version")
        if self.path is not None:
            _require_text(self.path, "path")
        if self.content is not None:
            _require_bytes(self.content, "content")


type Operation = Create | Update | Move | Archive | Restore


def _tuple(values: Iterable[object], name: str) -> tuple[object, ...]:
    try:
        return tuple(values)
    except TypeError as exc:
        raise TypeError(f"{name} must be iterable") from exc


@dataclass(frozen=True)
class ChangeSet:
    operations: tuple[Operation, ...]
    actor: str
    origin: str
    reason: str
    idempotency_key: str
    expected_head: str | None = None

    def __post_init__(self) -> None:
        operations = _tuple(self.operations, "operations")
        if not operations:
            raise ValueError("operations must not be empty")
        if not all(isinstance(operation, (Create, Update, Move, Archive, Restore)) for operation in operations):
            raise TypeError("operations must contain revision operations")
        object.__setattr__(self, "operations", operations)
        _require_text(self.actor, "actor")
        _require_text(self.origin, "origin")
        _require_text(self.reason, "reason")
        _require_text(self.idempotency_key, "idempotency_key")
        if self.expected_head is not None:
            _require_text(self.expected_head, "expected_head")


@dataclass(frozen=True)
class ResourceChange:
    action: str
    before: ResourceVersion | None
    after: ResourceVersion | None

    def __post_init__(self) -> None:
        _require_text(self.action, "action")


@dataclass(frozen=True)
class Commit:
    commit_id: str
    parent_id: str | None
    tree_id: str
    actor: str
    origin: str
    reason: str
    timestamp: datetime
    changes: tuple[ResourceChange, ...]

    def __post_init__(self) -> None:
        _require_text(self.commit_id, "commit_id")
        if self.parent_id is not None:
            _require_text(self.parent_id, "parent_id")
        _require_text(self.tree_id, "tree_id")
        _require_text(self.actor, "actor")
        _require_text(self.origin, "origin")
        _require_text(self.reason, "reason")
        if not isinstance(self.timestamp, datetime):
            raise TypeError("timestamp must be a datetime")
        changes = _tuple(self.changes, "changes")
        if not all(isinstance(change, ResourceChange) for change in changes):
            raise TypeError("changes must contain ResourceChange values")
        object.__setattr__(self, "changes", changes)


@dataclass(frozen=True)
class ResourceDiff:
    resource_id: str
    before: ResourceVersion | None
    after: ResourceVersion | None
    unified_diff: str

    def __post_init__(self) -> None:
        _require_text(self.resource_id, "resource_id")
        if not isinstance(self.unified_diff, str):
            raise TypeError("unified_diff must be a string")


@dataclass(frozen=True)
class IntegrityIssue:
    code: str
    target: str
    detail: str

    def __post_init__(self) -> None:
        _require_text(self.code, "code")
        _require_text(self.target, "target")
        _require_text(self.detail, "detail")


@dataclass(frozen=True)
class IntegrityReport:
    ok: bool
    head_commit: str | None
    resources: int
    objects: int
    issues: tuple[IntegrityIssue, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.ok, bool):
            raise TypeError("ok must be a bool")
        if self.head_commit is not None:
            _require_text(self.head_commit, "head_commit")
        _require_nonnegative(self.resources, "resources")
        _require_nonnegative(self.objects, "objects")
        issues = _tuple(self.issues, "issues")
        if not all(isinstance(issue, IntegrityIssue) for issue in issues):
            raise TypeError("issues must contain IntegrityIssue values")
        object.__setattr__(self, "issues", issues)


@dataclass(frozen=True)
class StorageReport:
    total_bytes: int
    blob_bytes: int
    tree_bytes: int
    commit_bytes: int
    recovery_bytes: int
    metadata_bytes: int
    resource_count: int
    commit_count: int
    object_count: int

    def __post_init__(self) -> None:
        for name in (
            "total_bytes",
            "blob_bytes",
            "tree_bytes",
            "commit_bytes",
            "recovery_bytes",
            "metadata_bytes",
            "resource_count",
            "commit_count",
            "object_count",
        ):
            _require_nonnegative(getattr(self, name), name)


@dataclass(frozen=True)
class CollectionReport:
    scanned: int
    removed: int
    retained: int
    bytes_removed: int

    def __post_init__(self) -> None:
        for name in ("scanned", "removed", "retained", "bytes_removed"):
            _require_nonnegative(getattr(self, name), name)
