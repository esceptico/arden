"""Revision integrity checks, storage accounting, and object collection."""

from collections.abc import Mapping
from pathlib import Path

from arden.revisions._codec import parse_commit, parse_tree, validate_commit_transition
from arden.revisions._materialized import MaterializedFiles
from arden.revisions._storage import HistoryStorage, sha256, valid_hash
from arden.revisions._transaction import TransactionManager
from arden.revisions.errors import CorruptRepositoryError, RevisionConflictError, UnsafePathError
from arden.revisions.models import (
    CollectionReport,
    Commit,
    IntegrityIssue,
    IntegrityReport,
    ResourceState,
    ResourceVersion,
    StorageReport,
)


def integrity_report(
    storage: HistoryStorage,
    files: MaterializedFiles,
    transactions: TransactionManager,
    history_root: Path,
) -> IntegrityReport:
    issues: list[IntegrityIssue] = []
    resources: dict[str, ResourceVersion] = {}
    object_count = 0
    head: str | None = None
    reachable_commits: set[str] = set()
    try:
        with storage.locked():
            head = storage.read_ref()
            object_count = validate_all_objects(storage, issues)
            if head is not None:
                reachable_commits, resources = walk_reachable(storage, head)
                for version in resources.values():
                    if version.state is ResourceState.ACTIVE:
                        try:
                            files.assert_state(version.path, version.blob_id)
                        except (RevisionConflictError, UnsafePathError) as exc:
                            issues.append(
                                IntegrityIssue(
                                    code="managed_file_mismatch",
                                    target=version.path,
                                    detail=str(exc),
                                )
                            )
            validate_metadata_indexes(storage, issues, reachable_commits)
            try:
                transactions.load_protected_transactions()
                for conflict in storage.list_metadata(storage.conflicts):
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
                target=str(history_root),
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


def storage_report(storage: HistoryStorage, resources: Mapping[str, ResourceVersion]) -> StorageReport:
    blob_bytes, blob_count = storage.directory_size(storage.blobs)
    tree_bytes, tree_count = storage.directory_size(storage.trees)
    commit_bytes, commit_count = storage.directory_size(storage.commits)
    transaction_bytes, _ = storage.directory_size(storage.transactions, recursive=True)
    conflict_bytes, _ = storage.directory_size(storage.conflicts, recursive=True)
    idempotency_bytes, _ = storage.directory_size(storage.idempotency)
    publication_bytes, _ = storage.directory_size(storage.published)
    ref_bytes, _ = storage.directory_size(storage.refs)
    lock_stat = storage.metadata_stat(storage.lock_path)
    if lock_stat is None:
        raise CorruptRepositoryError("repository lock disappeared during storage reporting")
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


def collection_roots(
    storage: HistoryStorage,
    transactions: TransactionManager,
) -> tuple[set[str], set[str], set[str]]:
    commit_roots: set[str] = set()
    current = read_published_ref(storage)
    if current is not None:
        commit_roots.add(current)
    protected = transactions.load_protected_transactions()
    for transaction in protected:
        commit_roots.add(transaction.new_head)
        if transaction.old_head is not None:
            commit_roots.add(transaction.old_head)
    for record_path in storage.list_metadata(storage.idempotency):
        if not record_path.name.endswith(".jsonl"):
            raise CorruptRepositoryError(f"invalid idempotency filename: {record_path.name}")
        key_hash = record_path.name.removesuffix(".jsonl")
        record = storage.read_idempotency(key_hash)
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
        commit = load_commit(storage, commit_id)
        commits.add(commit_id)
        trees.add(commit.tree_id)
        resources = validate_transition(storage, commit)
        for version in resources.values():
            storage.read_blob(version.blob_id)
            blobs.add(version.blob_id)
        if commit.parent_id is not None:
            pending.append(commit.parent_id)
    for transaction in protected:
        trees.add(transaction.tree_id)
        for row in transaction.rows:
            if row.old_blob is not None:
                blobs.add(row.old_blob)
            if row.new_blob is not None:
                blobs.add(row.new_blob)
    return commits, trees, blobs


def collect(
    storage: HistoryStorage,
    roots: tuple[set[str], set[str], set[str]],
    cutoff: float,
) -> CollectionReport:
    reachable_commits, reachable_trees, reachable_blobs = roots
    scanned = 0
    removed = 0
    retained = 0
    bytes_removed = 0
    specifications = (
        (storage.blobs, reachable_blobs, False),
        (storage.trees, reachable_trees, True),
        (storage.commits, reachable_commits, True),
    )
    for directory, reachable, jsonl in specifications:
        for path in storage.list_metadata(directory):
            object_id = object_id_from_filename(path.name, jsonl=jsonl)
            path_stat = storage.metadata_stat(path)
            if path_stat is None:
                raise CorruptRepositoryError(f"history object disappeared during collection: {path.name}")
            scanned += 1
            if object_id in reachable or path_stat.st_mtime > cutoff:
                retained += 1
                continue
            content = storage.read_metadata(path)
            if sha256(content) != object_id:
                raise CorruptRepositoryError(f"unreachable object is corrupt: {path.name}")
            bytes_removed += path_stat.st_size
            storage.unlink_metadata(path)
            removed += 1
        storage.fsync_dir(directory)
    return CollectionReport(
        scanned=scanned,
        removed=removed,
        retained=retained,
        bytes_removed=bytes_removed,
    )


def walk_reachable(storage: HistoryStorage, head: str) -> tuple[set[str], dict[str, ResourceVersion]]:
    commits: set[str] = set()
    cursor: str | None = head
    head_resources: dict[str, ResourceVersion] | None = None
    while cursor is not None:
        if cursor in commits:
            raise CorruptRepositoryError(f"commit history contains a cycle at {cursor}")
        commits.add(cursor)
        commit = load_commit(storage, cursor)
        resources = validate_transition(storage, commit)
        for version in resources.values():
            storage.read_blob(version.blob_id)
        if head_resources is None:
            head_resources = resources
        cursor = commit.parent_id
    return commits, head_resources or {}


def validate_all_objects(storage: HistoryStorage, issues: list[IntegrityIssue]) -> int:
    count = 0
    for path in storage.list_metadata(storage.blobs):
        count += 1
        try:
            storage.read_blob(object_id_from_filename(path.name, jsonl=False))
        except (CorruptRepositoryError, UnsafePathError) as exc:
            issues.append(IntegrityIssue(code="blob_corrupt", target=path.name, detail=str(exc)))
    for path in storage.list_metadata(storage.trees):
        count += 1
        try:
            load_tree(storage, object_id_from_filename(path.name, jsonl=True))
        except (CorruptRepositoryError, UnsafePathError) as exc:
            issues.append(IntegrityIssue(code="tree_corrupt", target=path.name, detail=str(exc)))
    for path in storage.list_metadata(storage.commits):
        count += 1
        try:
            load_commit(storage, object_id_from_filename(path.name, jsonl=True))
        except (CorruptRepositoryError, UnsafePathError) as exc:
            issues.append(IntegrityIssue(code="commit_corrupt", target=path.name, detail=str(exc)))
    return count


def validate_metadata_indexes(
    storage: HistoryStorage,
    issues: list[IntegrityIssue],
    reachable_commits: set[str],
) -> None:
    for path in storage.list_metadata(storage.refs):
        if path != storage.current_ref:
            issues.append(
                IntegrityIssue(
                    code="unsupported_ref",
                    target=path.name,
                    detail="the single-ref repository contains an unknown ref",
                )
            )
    for path in storage.list_metadata(storage.idempotency):
        try:
            if not path.name.endswith(".jsonl"):
                raise CorruptRepositoryError(f"invalid idempotency filename: {path.name}")
            key_hash = path.name.removesuffix(".jsonl")
            record = storage.read_idempotency(key_hash)
            if record is None or record["commit_id"] not in reachable_commits:
                raise CorruptRepositoryError(f"idempotency record {path.name} points outside published history")
        except (CorruptRepositoryError, UnsafePathError) as exc:
            issues.append(IntegrityIssue(code="idempotency_corrupt", target=path.name, detail=str(exc)))
    published: set[str] = set()
    for path in storage.list_metadata(storage.published):
        try:
            if not valid_hash(path.name) or not storage.is_published(path.name):
                raise CorruptRepositoryError(f"invalid published commit marker: {path.name}")
            if path.name not in reachable_commits:
                raise CorruptRepositoryError(f"published commit marker points outside current history: {path.name}")
            published.add(path.name)
        except (CorruptRepositoryError, UnsafePathError) as exc:
            issues.append(IntegrityIssue(code="publication_corrupt", target=path.name, detail=str(exc)))
    for commit_id in sorted(reachable_commits - published):
        issues.append(
            IntegrityIssue(
                code="publication_missing",
                target=commit_id,
                detail="reachable commit lacks its publication certificate",
            )
        )


def read_published_ref(storage: HistoryStorage) -> str | None:
    commit_id = storage.read_ref()
    if commit_id is not None and not storage.is_published(commit_id):
        raise CorruptRepositoryError(f"current ref points to unpublished commit: {commit_id}")
    return commit_id


def load_tree(storage: HistoryStorage, tree_id: str) -> dict[str, ResourceVersion]:
    return parse_tree(storage.read_tree(tree_id), tree_id=tree_id)


def load_commit(storage: HistoryStorage, commit_id: object) -> Commit:
    if not valid_hash(commit_id):
        raise CorruptRepositoryError("commit ID is invalid")
    return parse_commit(storage.read_commit(commit_id), commit_id=commit_id)


def validate_transition(storage: HistoryStorage, commit: Commit) -> dict[str, ResourceVersion]:
    after = load_tree(storage, commit.tree_id)
    if commit.parent_id is None:
        before: dict[str, ResourceVersion] = {}
    else:
        parent = load_commit(storage, commit.parent_id)
        before = load_tree(storage, parent.tree_id)
    validate_commit_transition(commit, before, after)
    return after


def object_id_from_filename(filename: str, *, jsonl: bool) -> str:
    name = filename.removesuffix(".jsonl") if jsonl else filename
    if jsonl and not filename.endswith(".jsonl"):
        raise CorruptRepositoryError(f"object filename is invalid: {filename}")
    if not valid_hash(name):
        raise CorruptRepositoryError(f"object filename is invalid: {filename}")
    return name
