"""Domain-neutral value objects for managed-file revisions.

Repository implementations own path normalization, object hashing, persistence,
and conflict handling.  These models only express immutable revision intent and
results.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class ResourceState(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


@dataclass(frozen=True)
class ResourceVersion:
    resource_id: str
    path: str
    blob_id: str
    state: ResourceState
    version_id: str


@dataclass(frozen=True)
class Create:
    resource_id: str
    path: str
    content: bytes


@dataclass(frozen=True)
class Update:
    resource_id: str
    expected_version: str
    content: bytes


@dataclass(frozen=True)
class Move:
    resource_id: str
    expected_version: str
    path: str
    content: bytes | None = None


@dataclass(frozen=True)
class Archive:
    resource_id: str
    expected_version: str


@dataclass(frozen=True)
class Restore:
    resource_id: str
    expected_version: str
    path: str | None = None
    content: bytes | None = None


type Operation = Create | Update | Move | Archive | Restore


@dataclass(frozen=True)
class ChangeSet:
    operations: tuple[Operation, ...]
    actor: str
    origin: str
    reason: str
    idempotency_key: str
    expected_head: str | None = None
    enforce_expected_head: bool = False


@dataclass(frozen=True)
class ResourceChange:
    action: str
    before: ResourceVersion | None
    after: ResourceVersion | None


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


@dataclass(frozen=True)
class ResourceDiff:
    resource_id: str
    before: ResourceVersion | None
    after: ResourceVersion | None
    unified_diff: str


@dataclass(frozen=True)
class ResourceDiffPage:
    resource_id: str
    before: ResourceVersion | None
    after: ResourceVersion | None
    offset: int
    end_offset: int
    has_more: bool
    unified_diff: str


@dataclass(frozen=True)
class IntegrityIssue:
    code: str
    target: str
    detail: str


@dataclass(frozen=True)
class IntegrityReport:
    ok: bool
    head_commit: str | None
    resources: int
    objects: int
    issues: tuple[IntegrityIssue, ...]


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


@dataclass(frozen=True)
class CollectionReport:
    scanned: int
    removed: int
    retained: int
    bytes_removed: int
