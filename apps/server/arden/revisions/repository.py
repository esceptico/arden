"""Generic content-addressed history for a materialized managed-file tree."""

import difflib
import os
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ._codec import (
    TransactionRow,
    commit_record,
    make_version,
    parse_commit,
    parse_tree,
    tree_record,
    validate_commit_transition,
)
from ._materialized import MaterializedFiles, ancestor_collision, normalize_relative
from ._storage import HistoryStorage, canonical_jsonl, sha256, valid_hash
from ._transaction import TransactionManager
from .errors import (
    CorruptRepositoryError,
    IdempotencyConflictError,
    NoChangesError,
    RevisionConflictError,
    RevisionContentLimitError,
    UnsafePathError,
)
from .models import (
    Archive,
    ChangeSet,
    CollectionReport,
    Commit,
    Create,
    IntegrityIssue,
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
        if limit is not None and limit < 1:
            raise ValueError("history limit must be positive")
        if stop_before is not None and not valid_hash(stop_before):
            raise KeyError(f"unknown history stop boundary: {stop_before!r}")
        cursor = self._read_published_ref() if start is None else start
        if start is not None:
            self._require_reachable(start)
        commits: list[Commit] = []
        seen: set[str] = set()
        while cursor is not None:
            if cursor == stop_before:
                return tuple(commits)
            if cursor in seen:
                raise CorruptRepositoryError(f"commit history contains a cycle at {cursor}")
            seen.add(cursor)
            commit = self._load_commit(cursor)
            self._validate_commit_transition(commit)
            if resource_id is None or any(_change_resource_id(change) == resource_id for change in commit.changes):
                if limit is None or len(commits) < limit:
                    commits.append(commit)
                if stop_before is None and limit is not None and len(commits) >= limit:
                    break
            cursor = commit.parent_id
        if stop_before is not None:
            raise KeyError(f"history stop boundary is not reachable from the selected start: {stop_before}")
        return tuple(commits)

    def diff(
        self,
        base: str | None,
        target: str | None,
        *,
        resource_ids: Iterable[str] | None = None,
    ) -> tuple[ResourceDiff, ...]:
        before = self._tree_at(base, none_means_empty=True)
        after = self._tree_at(target, none_means_empty=True)
        selected: set[str] | None = None
        if resource_ids is not None:
            if isinstance(resource_ids, (str, bytes)):
                raise TypeError("resource_ids must be an iterable of resource IDs")
            selected = set(resource_ids)
            if not all(isinstance(resource_id, str) and resource_id for resource_id in selected):
                raise ValueError("resource_ids must contain only nonempty strings")
        result: list[ResourceDiff] = []
        changed_ids = set(before) | set(after)
        if selected is not None:
            changed_ids &= selected
        for resource_id in sorted(changed_ids):
            old = before.get(resource_id)
            new = after.get(resource_id)
            if old == new:
                continue
            old_content = b"" if old is None else self._storage.read_blob(old.blob_id)
            new_content = b"" if new is None else self._storage.read_blob(new.blob_id)
            old_label = "/dev/null" if old is None else old.path
            new_label = "/dev/null" if new is None else new.path
            result.append(
                ResourceDiff(
                    resource_id=resource_id,
                    before=old,
                    after=new,
                    unified_diff=_unified_diff(old_content, new_content, old_label, new_label),
                )
            )
        return tuple(result)

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
        if not isinstance(resource_id, str) or not resource_id:
            raise ValueError("resource_id must be a nonempty string")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a nonnegative integer")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        if not isinstance(tail, bool):
            raise TypeError("tail must be a bool")
        if tail and offset != 0:
            raise ValueError("offset must be zero when tail is requested")

        before = self._tree_at(base, none_means_empty=True)
        after = self._tree_at(target, none_means_empty=True)
        old = before.get(resource_id)
        new = after.get(resource_id)
        if old == new:
            raise ValueError(f"resource did not change between revisions: {resource_id}")
        old_content = b"" if old is None else self._storage.read_blob(old.blob_id)
        new_content = b"" if new is None else self._storage.read_blob(new.blob_id)
        old_label = "/dev/null" if old is None else old.path
        new_label = "/dev/null" if new is None else new.path
        page_offset, page_end, has_more, text = _unified_diff_page(
            old_content,
            new_content,
            old_label,
            new_label,
            offset=offset,
            limit=limit,
            tail=tail,
        )
        return ResourceDiffPage(
            resource_id=resource_id,
            before=old,
            after=new,
            offset=page_offset,
            end_offset=page_end,
            has_more=has_more,
            unified_diff=text,
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

        if before is None and after is None:
            raise ValueError("a resource diff requires before or after")
        for name, version in (("before", before), ("after", after)):
            if version is not None and not isinstance(version, ResourceVersion):
                raise TypeError(f"{name} must be a ResourceVersion or None")
        if before is not None and after is not None and before.resource_id != after.resource_id:
            raise ValueError("resource versions must have the same resource ID")
        if before == after:
            resource_id = before.resource_id if before is not None else after.resource_id
            raise ValueError(f"resource did not change between versions: {resource_id}")
        if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be a nonnegative integer")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError("limit must be a positive integer")
        if not isinstance(tail, bool):
            raise TypeError("tail must be a bool")
        if tail and offset != 0:
            raise ValueError("offset must be zero when tail is requested")
        if source_byte_limit is not None and (
            isinstance(source_byte_limit, bool) or not isinstance(source_byte_limit, int) or source_byte_limit <= 0
        ):
            raise ValueError("source_byte_limit must be a positive integer or None")

        versions = tuple(version for version in (before, after) if version is not None)
        unique = {version.blob_id: version for version in versions}
        actual_bytes = sum(self.content_size(version) for version in unique.values())
        if source_byte_limit is not None and actual_bytes > source_byte_limit:
            raise RevisionContentLimitError(actual_bytes=actual_bytes, limit_bytes=source_byte_limit)
        contents = {blob_id: self._storage.read_blob(blob_id) for blob_id in unique}
        old_content = b"" if before is None else contents[before.blob_id]
        new_content = b"" if after is None else contents[after.blob_id]
        old_label = "/dev/null" if before is None else before.path
        new_label = "/dev/null" if after is None else after.path
        page_offset, page_end, has_more, text = _unified_diff_page(
            old_content,
            new_content,
            old_label,
            new_label,
            offset=offset,
            limit=limit,
            tail=tail,
        )
        resource = after or before
        assert resource is not None
        return ResourceDiffPage(
            resource_id=resource.resource_id,
            before=before,
            after=after,
            offset=page_offset,
            end_offset=page_end,
            has_more=has_more,
            unified_diff=text,
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
        issues: list[IntegrityIssue] = []
        resources: dict[str, ResourceVersion] = {}
        object_count = 0
        head: str | None = None
        reachable_commits: set[str] = set()
        try:
            with self._storage.locked():
                head = self._storage.read_ref()
                object_count = self._validate_all_objects(issues)
                if head is not None:
                    reachable_commits, resources = self._walk_reachable(head)
                    for version in resources.values():
                        if version.state is ResourceState.ACTIVE:
                            try:
                                self._files.assert_state(version.path, version.blob_id)
                            except (RevisionConflictError, UnsafePathError) as exc:
                                issues.append(
                                    IntegrityIssue(
                                        code="managed_file_mismatch",
                                        target=version.path,
                                        detail=str(exc),
                                    )
                                )
                self._validate_metadata_indexes(issues, reachable_commits)
                try:
                    self._transactions.load_protected_transactions()
                    for conflict in self._storage.list_metadata(self._storage.conflicts):
                        issues.append(
                            IntegrityIssue(
                                code="recovery_conflict",
                                target=conflict.name,
                                detail="an external write prevented automatic rollback",
                            )
                        )
                except (CorruptRepositoryError, UnsafePathError) as exc:
                    issues.append(
                        IntegrityIssue(
                            code="recovery_corrupt",
                            target="transactions",
                            detail=str(exc),
                        )
                    )
        except (CorruptRepositoryError, UnsafePathError, OSError) as exc:
            issues.append(
                IntegrityIssue(
                    code="repository_corrupt",
                    target=str(self.history_root),
                    detail=str(exc),
                )
            )
        return IntegrityReport(
            ok=not issues,
            head_commit=head,
            resources=len(resources),
            objects=object_count,
            issues=tuple(issues),
        )

    def storage_report(self) -> StorageReport:
        self._ensure_recovered()
        with self._storage.locked():
            _, resources = self._load_current_tree()
            blob_bytes, blob_count = self._directory_size(self._storage.blobs)
            tree_bytes, tree_count = self._directory_size(self._storage.trees)
            commit_bytes, commit_count = self._directory_size(self._storage.commits)
            transaction_bytes, _ = self._directory_size(self._storage.transactions, recursive=True)
            conflict_bytes, _ = self._directory_size(self._storage.conflicts, recursive=True)
            idempotency_bytes, _ = self._directory_size(self._storage.idempotency)
            publication_bytes, _ = self._directory_size(self._storage.published)
            ref_bytes, _ = self._directory_size(self._storage.refs)
            lock_stat = self._storage.metadata_stat(self._storage.lock_path)
            assert lock_stat is not None
            lock_bytes = lock_stat.st_size
            recovery_bytes = transaction_bytes + conflict_bytes
            metadata_bytes = idempotency_bytes + publication_bytes + ref_bytes + lock_bytes
            total = blob_bytes + tree_bytes + commit_bytes + recovery_bytes + metadata_bytes
            return StorageReport(
                total_bytes=total,
                blob_bytes=blob_bytes,
                tree_bytes=tree_bytes,
                commit_bytes=commit_bytes,
                recovery_bytes=recovery_bytes,
                metadata_bytes=metadata_bytes,
                resource_count=len(resources),
                commit_count=commit_count,
                object_count=blob_count + tree_count + commit_count,
            )

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
            reachable_commits, reachable_trees, reachable_blobs = self._collection_roots()
            scanned = 0
            removed = 0
            retained = 0
            bytes_removed = 0
            specifications = (
                (self._storage.blobs, reachable_blobs, False),
                (self._storage.trees, reachable_trees, True),
                (self._storage.commits, reachable_commits, True),
            )
            for directory, reachable, jsonl in specifications:
                for path in self._storage.list_metadata(directory):
                    object_id = _object_id(path.name, jsonl=jsonl)
                    path_stat = self._storage.metadata_stat(path)
                    assert path_stat is not None
                    scanned += 1
                    if object_id in reachable or path_stat.st_mtime > cutoff:
                        retained += 1
                        continue
                    content = self._storage.read_metadata(path)
                    if jsonl:
                        if sha256(content) != object_id:
                            raise CorruptRepositoryError(f"unreachable object is corrupt: {path.name}")
                    elif sha256(content) != object_id:
                        raise CorruptRepositoryError(f"unreachable blob is corrupt: {path.name}")
                    bytes_removed += path_stat.st_size
                    self._storage.unlink_metadata(path)
                    removed += 1
                self._storage.fsync_dir(directory)
            return CollectionReport(
                scanned=scanned,
                removed=removed,
                retained=retained,
                bytes_removed=bytes_removed,
            )

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

    def _walk_reachable(self, head: str) -> tuple[set[str], dict[str, ResourceVersion]]:
        commits: set[str] = set()
        cursor: str | None = head
        head_resources: dict[str, ResourceVersion] | None = None
        while cursor is not None:
            if cursor in commits:
                raise CorruptRepositoryError(f"commit history contains a cycle at {cursor}")
            commits.add(cursor)
            commit = self._load_commit(cursor)
            resources = self._validate_commit_transition(commit)
            for version in resources.values():
                self._storage.read_blob(version.blob_id)
            if head_resources is None:
                head_resources = resources
            cursor = commit.parent_id
        return commits, head_resources or {}

    def _validate_all_objects(self, issues: list[IntegrityIssue]) -> int:
        count = 0
        for path in self._storage.list_metadata(self._storage.blobs):
            count += 1
            try:
                object_id = _object_id(path.name, jsonl=False)
                self._storage.read_blob(object_id)
            except (CorruptRepositoryError, UnsafePathError) as exc:
                issues.append(IntegrityIssue(code="blob_corrupt", target=path.name, detail=str(exc)))
        for path in self._storage.list_metadata(self._storage.trees):
            count += 1
            try:
                object_id = _object_id(path.name, jsonl=True)
                self._load_tree(object_id)
            except (CorruptRepositoryError, UnsafePathError) as exc:
                issues.append(IntegrityIssue(code="tree_corrupt", target=path.name, detail=str(exc)))
        for path in self._storage.list_metadata(self._storage.commits):
            count += 1
            try:
                object_id = _object_id(path.name, jsonl=True)
                self._load_commit(object_id)
            except (CorruptRepositoryError, UnsafePathError) as exc:
                issues.append(IntegrityIssue(code="commit_corrupt", target=path.name, detail=str(exc)))
        return count

    def _validate_metadata_indexes(
        self,
        issues: list[IntegrityIssue],
        reachable_commits: set[str],
    ) -> None:
        for path in self._storage.list_metadata(self._storage.refs):
            if path != self._storage.current_ref:
                issues.append(
                    IntegrityIssue(
                        code="unsupported_ref",
                        target=path.name,
                        detail="the single-ref repository contains an unknown ref",
                    )
                )
        for path in self._storage.list_metadata(self._storage.idempotency):
            try:
                if not path.name.endswith(".jsonl"):
                    raise CorruptRepositoryError(f"invalid idempotency filename: {path.name}")
                key_hash = path.name.removesuffix(".jsonl")
                record = self._storage.read_idempotency(key_hash)
                if record is None or record["commit_id"] not in reachable_commits:
                    raise CorruptRepositoryError(f"idempotency record {path.name} points outside published history")
            except (CorruptRepositoryError, UnsafePathError) as exc:
                issues.append(
                    IntegrityIssue(
                        code="idempotency_corrupt",
                        target=path.name,
                        detail=str(exc),
                    )
                )
        published: set[str] = set()
        for path in self._storage.list_metadata(self._storage.published):
            try:
                if not valid_hash(path.name) or not self._storage.is_published(path.name):
                    raise CorruptRepositoryError(f"invalid published commit marker: {path.name}")
                if path.name not in reachable_commits:
                    raise CorruptRepositoryError(f"published commit marker points outside current history: {path.name}")
                published.add(path.name)
            except (CorruptRepositoryError, UnsafePathError) as exc:
                issues.append(
                    IntegrityIssue(
                        code="publication_corrupt",
                        target=path.name,
                        detail=str(exc),
                    )
                )
        for commit_id in sorted(reachable_commits - published):
            issues.append(
                IntegrityIssue(
                    code="publication_missing",
                    target=commit_id,
                    detail="reachable commit lacks its publication certificate",
                )
            )

    def _collection_roots(self) -> tuple[set[str], set[str], set[str]]:
        commit_roots: set[str] = set()
        current = self._read_published_ref()
        if current is not None:
            commit_roots.add(current)
        transactions = self._transactions.load_protected_transactions()
        for transaction in transactions:
            commit_roots.add(transaction.new_head)
            if transaction.old_head is not None:
                commit_roots.add(transaction.old_head)
        for record_path in self._storage.list_metadata(self._storage.idempotency):
            if not record_path.name.endswith(".jsonl"):
                raise CorruptRepositoryError(f"invalid idempotency filename: {record_path.name}")
            key_hash = record_path.name.removesuffix(".jsonl")
            record = self._storage.read_idempotency(key_hash)
            if record is not None:
                commit_roots.add(record["commit_id"])

        commits: set[str] = set()
        trees: set[str] = set()
        blobs: set[str] = set()
        pending = list(commit_roots)
        while pending:
            commit_id = pending.pop()
            if commit_id in commits:
                continue
            commit = self._load_commit(commit_id)
            commits.add(commit_id)
            trees.add(commit.tree_id)
            resources = self._validate_commit_transition(commit)
            for version in resources.values():
                self._storage.read_blob(version.blob_id)
                blobs.add(version.blob_id)
            if commit.parent_id is not None:
                pending.append(commit.parent_id)
        for transaction in transactions:
            trees.add(transaction.tree_id)
            for row in transaction.rows:
                if row.old_blob is not None:
                    blobs.add(row.old_blob)
                if row.new_blob is not None:
                    blobs.add(row.new_blob)
        return commits, trees, blobs

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

    def _directory_size(self, directory: Path, *, recursive: bool = False) -> tuple[int, int]:
        return self._storage.directory_size(directory, recursive=recursive)

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


def _require_active(version: ResourceVersion, operation: str) -> None:
    if version.state is not ResourceState.ACTIVE:
        raise RevisionConflictError(f"cannot {operation} archived resource {version.resource_id}")


def _change_resource_id(change: ResourceChange) -> str:
    if change.after is not None:
        return change.after.resource_id
    if change.before is not None:
        return change.before.resource_id
    raise CorruptRepositoryError("resource change has no identity")


def _unified_diff(before: bytes, after: bytes, old_label: str, new_label: str) -> str:
    return "".join(_unified_diff_chunks(before, after, old_label, new_label))


def _unified_diff_chunks(before: bytes, after: bytes, old_label: str, new_label: str):
    before_text = before.decode("utf-8", errors="surrogateescape").splitlines(keepends=True)
    after_text = after.decode("utf-8", errors="surrogateescape").splitlines(keepends=True)
    return difflib.unified_diff(
        before_text,
        after_text,
        fromfile=old_label,
        tofile=new_label,
        lineterm="\n",
    )


def _unified_diff_page(
    before: bytes,
    after: bytes,
    old_label: str,
    new_label: str,
    *,
    offset: int,
    limit: int,
    tail: bool,
) -> tuple[int, int, bool, str]:
    chunks = _unified_diff_chunks(before, after, old_label, new_label)
    if tail:
        text = ""
        total = 0
        for chunk in chunks:
            total += len(chunk)
            text = chunk[-limit:] if len(chunk) >= limit else (text + chunk)[-limit:]
        page_offset = 0 if total == 0 else ((total - 1) // limit) * limit
        page_length = total - page_offset
        return page_offset, total, False, text[-page_length:] if page_length else ""

    cursor = 0
    parts: list[str] = []
    length = 0
    has_more = False
    iterator = iter(chunks)
    for chunk in iterator:
        next_cursor = cursor + len(chunk)
        if next_cursor <= offset:
            cursor = next_cursor
            continue
        start = max(0, offset - cursor)
        available_length = len(chunk) - start
        remaining = limit - length
        taken = min(available_length, remaining)
        parts.append(chunk[start : start + taken])
        length += taken
        cursor = next_cursor
        if available_length > remaining:
            has_more = True
            break
        if length == limit:
            has_more = any(bool(next_chunk) for next_chunk in iterator)
            break
    else:
        if offset > cursor or (offset == cursor and cursor > 0 and length == 0):
            raise IndexError("offset is outside the resource diff")

    text = "".join(parts)
    return offset, offset + len(text), has_more, text


def _reject_ancestor_collisions(paths: Iterable[str]) -> None:
    collision = ancestor_collision(list(paths))
    if collision is not None:
        raise RevisionConflictError(f"managed file paths overlap: {collision[0]} and {collision[1]}")


def _object_id(filename: str, *, jsonl: bool) -> str:
    name = filename.removesuffix(".jsonl") if jsonl else filename
    if jsonl and not filename.endswith(".jsonl"):
        raise CorruptRepositoryError(f"object filename is invalid: {filename}")
    if not valid_hash(name):
        raise CorruptRepositoryError(f"object filename is invalid: {filename}")
    return name


def _aware_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)
