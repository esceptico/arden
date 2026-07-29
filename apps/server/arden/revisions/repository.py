"""Generic content-addressed history for a materialized managed-file tree."""

import os
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

from arden.revisions import maintenance, query
from arden.revisions._codec import (
    TransactionRow,
    commit_record,
    make_version,
    parse_commit,
    parse_tree,
    tree_record,
    validate_commit_transition,
)
from arden.revisions._materialized import MaterializedFiles, ancestor_collision, normalize_relative
from arden.revisions._storage import HistoryStorage, canonical_jsonl, sha256, valid_hash
from arden.revisions._transaction import TransactionManager
from arden.revisions.errors import (
    CorruptRepositoryError,
    IdempotencyConflictError,
    NoChangesError,
    RevisionConflictError,
)
from arden.revisions.models import (
    Archive,
    ChangeSet,
    CollectionReport,
    Commit,
    Create,
    IntegrityReport,
    Move,
    ResourceChange,
    ResourceDiff,
    ResourceDiffPage,
    ResourceState,
    ResourceVersion,
    Restore,
    StorageReport,
    Update,
)


class ManagedFileRepository:
    """A single-ref revision repository over ordinary visible files."""

    def __init__(
        self,
        root: Path,
        *,
        history_root: Path | None = None,
        gc_grace_period: timedelta = timedelta(days=30),
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if gc_grace_period < timedelta(0):
            raise ValueError("gc_grace_period must not be negative")
        working_root = Path(os.path.abspath(os.fspath(root)))
        resolved_history = working_root / ".managed-history" if history_root is None else history_root
        self._storage = HistoryStorage(working_root, Path(resolved_history))
        self._files = MaterializedFiles(self._storage.working_root, self._storage.root)
        self._gc_grace_period = gc_grace_period
        self._clock = clock or (lambda: datetime.now(UTC))
        self._transactions = TransactionManager(
            self._storage,
            self._files,
            lambda point: self._checkpoint(point),
        )
        self._storage.initialize()
        self.recover()

    @property
    def root(self) -> Path:
        return self._storage.working_root

    @property
    def history_root(self) -> Path:
        return self._storage.root

    @property
    def head(self) -> str | None:
        self._ensure_recovered()
        commit_id = self._read_published_ref()
        if commit_id is not None:
            commit = self._load_commit(commit_id)
            self._validate_commit_transition(commit)
        return commit_id

    @property
    def current_revision(self) -> str | None:
        """Return the published ref without loading its commit or tree."""

        self._ensure_recovered()
        return self._read_published_ref()

    def recover(self) -> tuple[str, ...]:
        with self._storage.locked():
            return self._transactions.recover()

    def commit(self, change_set: ChangeSet) -> Commit:
        _validate_change_set(change_set)
        request_hash, key_hash = self._request_identity(change_set)
        with self._storage.locked():
            self._transactions.recover()
            prior = self._storage.read_idempotency(key_hash)
            if prior is not None:
                if prior["request_hash"] != request_hash:
                    raise IdempotencyConflictError(
                        f"idempotency key {change_set.idempotency_key!r} was reused for a different change set"
                    )
                return self._load_commit(prior["commit_id"])

            old_head, current = self._load_current_tree()
            if (
                change_set.expected_head is not None or change_set.enforce_expected_head
            ) and old_head != change_set.expected_head:
                raise RevisionConflictError(
                    f"current head changed: expected {change_set.expected_head!r}, found {old_head!r}"
                )
            self._assert_current_files(current)
            resulting, changes, payloads = self._apply(change_set.operations, current)
            rows = self._materialization_rows(current, resulting)
            for row in rows:
                self._files.assert_state(row.path, row.old_blob)

            for blob_id, content in payloads.items():
                if self._storage.write_blob(content) != blob_id:
                    raise CorruptRepositoryError(f"blob identity changed while writing {blob_id}")
            tree_id = self._storage.write_tree(tree_record(resulting))
            timestamp = self._now()
            commit_id = self._storage.write_commit(
                commit_record(
                    parent_id=old_head,
                    tree_id=tree_id,
                    actor=change_set.actor,
                    origin=change_set.origin,
                    reason=change_set.reason,
                    timestamp=timestamp,
                    changes=changes,
                )
            )
            self._checkpoint("after_objects")
            transaction = self._transactions.prepare(
                old_head=old_head,
                new_head=commit_id,
                tree_id=tree_id,
                key_hash=key_hash,
                request_hash=request_hash,
                rows=rows,
            )
            self._transactions.finish(transaction)
            return self._load_commit(commit_id)

    def get(self, resource_id: str, *, at: str | None = None) -> ResourceVersion:
        resources = self._tree_at(at)
        try:
            return resources[resource_id]
        except KeyError as exc:
            raise KeyError(f"unknown resource: {resource_id}") from exc

    def find_by_path(
        self,
        path: str,
        *,
        at: str | None = None,
        include_archived: bool = False,
    ) -> ResourceVersion | None:
        normalized = normalize_relative(path)
        matches = [
            version
            for version in self._tree_at(at).values()
            if version.path == normalized and (include_archived or version.state is ResourceState.ACTIVE)
        ]
        if len(matches) > 1:
            raise CorruptRepositoryError(f"multiple resources use path {normalized}")
        return None if not matches else matches[0]

    def list_resources(
        self,
        *,
        at: str | None = None,
        include_archived: bool = False,
    ) -> tuple[ResourceVersion, ...]:
        return tuple(
            version
            for version in self._tree_at(at).values()
            if include_archived or version.state is ResourceState.ACTIVE
        )

    def read(self, resource_id: str, *, at: str | None = None) -> bytes:
        return self._storage.read_blob(self.get(resource_id, at=at).blob_id)

    def read_version(self, resource: ResourceVersion) -> bytes:
        """Read one exact immutable resource version without tree traversal."""

        if not isinstance(resource, ResourceVersion):
            raise TypeError("resource must be a ResourceVersion")
        return self._storage.read_blob(resource.blob_id)

    def content_size(
        self,
        resource: ResourceVersion | str,
        *,
        at: str | None = None,
    ) -> int:
        """Return a resource version's immutable content length without reading it.

        ``ResourceVersion`` callers already hold an immutable blob reference. A
        resource ID is resolved at the optional pinned commit before its blob is
        statted.
        """
        if isinstance(resource, ResourceVersion):
            if at is not None:
                raise ValueError("at is only supported when content_size receives a resource ID")
            version = resource
        elif isinstance(resource, str):
            version = self.get(resource, at=at)
        else:
            raise TypeError("resource must be a ResourceVersion or resource ID")
        return self._storage.blob_size(version.blob_id)

    def commit_size(self, commit_id: str) -> int:
        """Return an exact immutable commit record's size without walking history."""

        self._ensure_recovered()
        if not valid_hash(commit_id) or not self._storage.is_published(commit_id):
            raise KeyError(f"unknown commit: {commit_id!r}")
        return self._storage.commit_size(commit_id)

    def inspect_commit(self, commit_id: str) -> Commit:
        """Read one exact immutable commit without traversing from the current ref.

        This validates the content-addressed commit record itself. Callers must
        already hold a trusted commit reference when current reachability is
        required.
        """

        self.commit_size(commit_id)
        return self._load_commit(commit_id)

    def history(
        self,
        *,
        resource_id: str | None = None,
        start: str | None = None,
        stop_before: str | None = None,
        limit: int | None = None,
    ) -> tuple[Commit, ...]:
        """Return newest-first reachable commits.

        ``stop_before`` is an exclusive reachable boundary. It lets consumers
        replay only commits after a known watermark: the boundary itself and all
        older commits are never traversed. Every returned commit retains normal
        transition validation; validating the oldest returned transition may
        read the boundary commit and tree once.
        """
        self._ensure_recovered()
        return query.history(
            head=self._read_published_ref(),
            resource_id=resource_id,
            start=start,
            stop_before=stop_before,
            limit=limit,
            valid_hash=valid_hash,
            require_reachable=self._require_reachable,
            load_commit=self._load_commit,
            validate_commit_transition=self._validate_commit_transition,
        )

    def diff(
        self,
        base: str | None,
        target: str | None,
        *,
        resource_ids: Iterable[str] | None = None,
    ) -> tuple[ResourceDiff, ...]:
        before = self._tree_at(base, none_means_empty=True)
        after = self._tree_at(target, none_means_empty=True)
        return query.diff(
            before=before,
            after=after,
            resource_ids=resource_ids,
            read_blob=self._storage.read_blob,
        )

    def diff_page(
        self,
        base: str | None,
        target: str | None,
        resource_id: str,
        *,
        offset: int = 0,
        limit: int = 16_384,
        tail: bool = False,
    ) -> ResourceDiffPage:
        query.validate_page_arguments(resource_id=resource_id, offset=offset, limit=limit, tail=tail)
        before = self._tree_at(base, none_means_empty=True)
        after = self._tree_at(target, none_means_empty=True)
        old = before.get(resource_id)
        new = after.get(resource_id)
        return query.diff_page(
            before=old,
            after=new,
            resource_id=resource_id,
            offset=offset,
            limit=limit,
            tail=tail,
            read_blob=self._storage.read_blob,
        )

    def diff_versions_page(
        self,
        before: ResourceVersion | None,
        after: ResourceVersion | None,
        *,
        offset: int = 0,
        limit: int = 16_384,
        tail: bool = False,
        source_byte_limit: int | None = None,
    ) -> ResourceDiffPage:
        """Page a diff between exact immutable versions without tree traversal."""

        return query.diff_versions_page(
            before=before,
            after=after,
            offset=offset,
            limit=limit,
            tail=tail,
            source_byte_limit=source_byte_limit,
            blob_size=self._storage.blob_size,
            read_blob=self._storage.read_blob,
        )

    def restore_from_commit(
        self,
        resource_id: str,
        source_commit: str,
        *,
        expected_version: str,
        actor: str,
        origin: str,
        reason: str,
        idempotency_key: str,
        path: str | None = None,
    ) -> Commit:
        source = self.get(resource_id, at=source_commit)
        return self.commit(
            ChangeSet(
                operations=(
                    Restore(
                        resource_id=resource_id,
                        expected_version=expected_version,
                        path=source.path if path is None else path,
                        content=self._storage.read_blob(source.blob_id),
                    ),
                ),
                actor=actor,
                origin=origin,
                reason=reason,
                idempotency_key=idempotency_key,
            )
        )

    def integrity_report(self) -> IntegrityReport:
        return maintenance.integrity_report(
            self._storage,
            self._files,
            self._transactions,
            self.history_root,
        )

    def storage_report(self) -> StorageReport:
        self._ensure_recovered()
        with self._storage.locked():
            _, resources = self._load_current_tree()
            return maintenance.storage_report(self._storage, resources)

    def collect(
        self,
        *,
        now: datetime | None = None,
        grace_period: timedelta | None = None,
    ) -> CollectionReport:
        effective_now = self._now() if now is None else _aware_utc(now)
        effective_grace = self._gc_grace_period if grace_period is None else grace_period
        if effective_grace < timedelta(0):
            raise ValueError("grace_period must not be negative")
        cutoff = effective_now.timestamp() - effective_grace.total_seconds()
        with self._storage.locked():
            self._transactions.recover()
            return maintenance.collect(self._storage, self._collection_roots(), cutoff)

    def _apply(
        self,
        operations: Iterable[Create | Update | Move | Archive | Restore],
        current: Mapping[str, ResourceVersion],
    ) -> tuple[dict[str, ResourceVersion], tuple[ResourceChange, ...], dict[str, bytes]]:
        ordered = sorted(tuple(operations), key=lambda operation: operation.resource_id)
        resource_ids = [operation.resource_id for operation in ordered]
        if len(resource_ids) != len(set(resource_ids)):
            raise ValueError("a change set may touch each resource only once")
        resulting = dict(current)
        changes: list[ResourceChange] = []
        payloads: dict[str, bytes] = {}
        for operation in ordered:
            before = current.get(operation.resource_id)
            action: str
            if isinstance(operation, Create):
                if before is not None:
                    raise RevisionConflictError(f"resource already exists: {operation.resource_id}")
                blob_id = _record_payload(payloads, operation.content)
                after = make_version(
                    operation.resource_id,
                    normalize_relative(operation.path),
                    blob_id,
                    ResourceState.ACTIVE,
                    previous_version_id=None,
                )
                action = "create"
            else:
                if before is None:
                    raise RevisionConflictError(f"resource does not exist: {operation.resource_id}")
                if before.version_id != operation.expected_version:
                    raise RevisionConflictError(
                        f"resource {operation.resource_id} changed: "
                        f"expected {operation.expected_version}, found {before.version_id}"
                    )
                if isinstance(operation, Update):
                    _require_active(before, "update")
                    blob_id = _record_payload(payloads, operation.content)
                    after = make_version(
                        before.resource_id,
                        before.path,
                        blob_id,
                        ResourceState.ACTIVE,
                        previous_version_id=before.version_id,
                    )
                    action = "update"
                elif isinstance(operation, Move):
                    _require_active(before, "move")
                    blob_id = (
                        before.blob_id if operation.content is None else _record_payload(payloads, operation.content)
                    )
                    after = make_version(
                        before.resource_id,
                        normalize_relative(operation.path),
                        blob_id,
                        ResourceState.ACTIVE,
                        previous_version_id=before.version_id,
                    )
                    action = "move"
                elif isinstance(operation, Archive):
                    _require_active(before, "archive")
                    after = make_version(
                        before.resource_id,
                        before.path,
                        before.blob_id,
                        ResourceState.ARCHIVED,
                        previous_version_id=before.version_id,
                    )
                    action = "archive"
                elif isinstance(operation, Restore):
                    if operation.content is None:
                        blob_id = before.blob_id
                    else:
                        blob_id = _record_payload(payloads, operation.content)
                    after = make_version(
                        before.resource_id,
                        before.path if operation.path is None else normalize_relative(operation.path),
                        blob_id,
                        ResourceState.ACTIVE,
                        previous_version_id=before.version_id,
                    )
                    action = "restore"
                else:
                    raise TypeError(f"unsupported revision operation: {type(operation).__name__}")
            if before == after:
                raise NoChangesError(f"{action} produces no change for {operation.resource_id}")
            resulting[operation.resource_id] = after
            changes.append(ResourceChange(action=action, before=before, after=after))
        self._validate_active_paths(resulting)
        return resulting, tuple(changes), payloads

    def _materialization_rows(
        self,
        before: Mapping[str, ResourceVersion],
        after: Mapping[str, ResourceVersion],
    ) -> tuple[TransactionRow, ...]:
        old_paths = {
            version.path: version.blob_id for version in before.values() if version.state is ResourceState.ACTIVE
        }
        new_paths = {
            version.path: version.blob_id for version in after.values() if version.state is ResourceState.ACTIVE
        }
        all_paths = sorted(set(old_paths) | set(new_paths))
        _reject_ancestor_collisions(all_paths)
        return tuple(
            TransactionRow(path=path, old_blob=old_paths.get(path), new_blob=new_paths.get(path))
            for path in all_paths
            if old_paths.get(path) != new_paths.get(path)
        )

    def _assert_current_files(self, resources: Mapping[str, ResourceVersion]) -> None:
        for resource in resources.values():
            if resource.state is ResourceState.ACTIVE:
                self._files.assert_state(resource.path, resource.blob_id)

    def _tree_at(
        self,
        commit_id: str | None,
        *,
        none_means_empty: bool = False,
    ) -> dict[str, ResourceVersion]:
        self._ensure_recovered()
        if commit_id is None:
            if none_means_empty:
                return {}
            _, resources = self._load_current_tree()
            return resources
        self._require_reachable(commit_id)
        commit = self._load_commit(commit_id)
        return self._validate_commit_transition(commit)

    def _load_current_tree(self) -> tuple[str | None, dict[str, ResourceVersion]]:
        head = self._read_published_ref()
        if head is None:
            return None, {}
        commit = self._load_commit(head)
        return head, self._validate_commit_transition(commit)

    def _load_tree(self, tree_id: str) -> dict[str, ResourceVersion]:
        return parse_tree(self._storage.read_tree(tree_id), tree_id=tree_id)

    def _load_commit(self, commit_id: object) -> Commit:
        if not valid_hash(commit_id):
            raise CorruptRepositoryError("commit ID is invalid")
        return parse_commit(self._storage.read_commit(commit_id), commit_id=commit_id)

    def _validate_commit_transition(self, commit: Commit) -> dict[str, ResourceVersion]:
        after = self._load_tree(commit.tree_id)
        if commit.parent_id is None:
            before: dict[str, ResourceVersion] = {}
        else:
            parent = self._load_commit(commit.parent_id)
            before = self._load_tree(parent.tree_id)
        validate_commit_transition(commit, before, after)
        return after

    def _require_reachable(self, commit_id: str) -> None:
        if not valid_hash(commit_id):
            raise KeyError(f"unknown published commit: {commit_id!r}")
        if not self._storage.is_published(commit_id):
            raise KeyError(f"commit is not reachable from the current ref: {commit_id}")

    def _read_published_ref(self) -> str | None:
        commit_id = self._storage.read_ref()
        if commit_id is not None and not self._storage.is_published(commit_id):
            raise CorruptRepositoryError(f"current ref points to unpublished commit: {commit_id}")
        return commit_id

    def _collection_roots(self) -> tuple[set[str], set[str], set[str]]:
        return maintenance.collection_roots(self._storage, self._transactions)

    def _request_identity(self, change_set: ChangeSet) -> tuple[str, str]:
        operations: list[dict[str, object]] = []
        for operation in sorted(change_set.operations, key=lambda item: item.resource_id):
            record: dict[str, object] = {
                "resource_id": operation.resource_id,
                "operation": type(operation).__name__.lower(),
            }
            if isinstance(operation, Create):
                record["path"] = normalize_relative(operation.path)
                record["content_hash"] = sha256(operation.content)
            else:
                record["expected_version"] = operation.expected_version
                if isinstance(operation, Update):
                    record["content_hash"] = sha256(operation.content)
                elif isinstance(operation, Move):
                    record["path"] = normalize_relative(operation.path)
                    record["content_hash"] = None if operation.content is None else sha256(operation.content)
                elif isinstance(operation, Restore):
                    record["path"] = None if operation.path is None else normalize_relative(operation.path)
                    record["content_hash"] = None if operation.content is None else sha256(operation.content)
            operations.append(record)
        request = {
            "version": 1,
            "actor": change_set.actor,
            "origin": change_set.origin,
            "reason": change_set.reason,
            "expected_head": change_set.expected_head,
            "operations": operations,
        }
        if change_set.enforce_expected_head:
            request["enforce_expected_head"] = True
        return sha256(canonical_jsonl(request)), sha256(change_set.idempotency_key.encode())

    @staticmethod
    def _validate_active_paths(resources: Mapping[str, ResourceVersion]) -> None:
        active_paths = sorted(version.path for version in resources.values() if version.state is ResourceState.ACTIVE)
        if len(active_paths) != len(set(active_paths)):
            raise RevisionConflictError("two active resources cannot share one path")
        _reject_ancestor_collisions(active_paths)

    def _now(self) -> datetime:
        return _aware_utc(self._clock())

    def _ensure_recovered(self) -> None:
        if self._storage.has_pending_transactions():
            self.recover()

    def _checkpoint(self, point: str) -> None:
        """Test-only interruption boundary."""


def _record_payload(payloads: dict[str, bytes], content: bytes) -> str:
    blob_id = sha256(content)
    existing = payloads.get(blob_id)
    if existing is not None and existing != content:
        raise CorruptRepositoryError(f"SHA-256 collision for blob {blob_id}")
    payloads[blob_id] = content
    return blob_id


def _validate_change_set(change_set: ChangeSet) -> None:
    """Validate caller-provided revision input at the repository boundary."""

    if not isinstance(change_set, ChangeSet):
        raise TypeError("change_set must be a ChangeSet")
    if not isinstance(change_set.operations, tuple):
        raise TypeError("operations must be a tuple")
    if not change_set.operations:
        raise ValueError("operations must not be empty")
    _require_text(change_set.actor, "actor")
    _require_text(change_set.origin, "origin")
    _require_text(change_set.reason, "reason")
    _require_text(change_set.idempotency_key, "idempotency_key")
    if change_set.expected_head is not None:
        _require_text(change_set.expected_head, "expected_head")
    if not isinstance(change_set.enforce_expected_head, bool):
        raise TypeError("enforce_expected_head must be a bool")
    if change_set.expected_head is not None and change_set.enforce_expected_head:
        raise ValueError("enforce_expected_head is only needed for an empty expected head")
    for operation in change_set.operations:
        _validate_operation(operation)


def _validate_operation(operation: Create | Update | Move | Archive | Restore) -> None:
    if isinstance(operation, Create):
        _require_text(operation.resource_id, "resource_id")
        _require_text(operation.path, "path")
        _require_bytes(operation.content, "content")
        return
    if isinstance(operation, Update):
        _require_existing_operation(operation)
        _require_bytes(operation.content, "content")
        return
    if isinstance(operation, Move):
        _require_existing_operation(operation)
        _require_text(operation.path, "path")
        if operation.content is not None:
            _require_bytes(operation.content, "content")
        return
    if isinstance(operation, Archive):
        _require_existing_operation(operation)
        return
    if isinstance(operation, Restore):
        _require_existing_operation(operation)
        if operation.path is not None:
            _require_text(operation.path, "path")
        if operation.content is not None:
            _require_bytes(operation.content, "content")
        return
    raise TypeError("operations must contain revision operations")


def _require_existing_operation(operation: Update | Move | Archive | Restore) -> None:
    _require_text(operation.resource_id, "resource_id")
    _require_text(operation.expected_version, "expected_version")


def _require_text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")


def _require_bytes(value: object, name: str) -> None:
    if not isinstance(value, bytes):
        raise TypeError(f"{name} must be bytes")


def _require_active(version: ResourceVersion, operation: str) -> None:
    if version.state is not ResourceState.ACTIVE:
        raise RevisionConflictError(f"cannot {operation} archived resource {version.resource_id}")


def _reject_ancestor_collisions(paths: Iterable[str]) -> None:
    collision = ancestor_collision(list(paths))
    if collision is not None:
        raise RevisionConflictError(f"managed file paths overlap: {collision[0]} and {collision[1]}")


def _aware_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)
