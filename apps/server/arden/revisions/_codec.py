"""Canonical records for trees, commits, and recovery transactions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ._materialized import ancestor_collision, normalize_relative
from ._storage import FORMAT_VERSION, canonical_jsonl, sha256, valid_hash
from .errors import CorruptRepositoryError
from .models import Commit, ResourceChange, ResourceState, ResourceVersion

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping


@dataclass(frozen=True)
class TransactionRow:
    path: str
    old_blob: str | None
    new_blob: str | None


@dataclass(frozen=True)
class Transaction:
    transaction_id: str
    old_head: str | None
    new_head: str
    tree_id: str
    key_hash: str
    request_hash: str
    rows: tuple[TransactionRow, ...]


def make_version(
    resource_id: str,
    path: str,
    blob_id: str,
    state: ResourceState,
    *,
    previous_version_id: str | None = None,
) -> ResourceVersion:
    normalized = normalize_relative(path)
    if not resource_id:
        raise ValueError("resource_id must not be empty")
    if not valid_hash(blob_id):
        raise ValueError("blob_id must be a SHA-256 identifier")
    if previous_version_id is not None and not valid_hash(previous_version_id):
        raise ValueError("previous_version_id must be a SHA-256 identifier")
    identity = {
        "blob_id": blob_id,
        "path": normalized,
        "previous_version_id": previous_version_id,
        "resource_id": resource_id,
        "state": state.value,
    }
    return ResourceVersion(
        resource_id=resource_id,
        path=normalized,
        blob_id=blob_id,
        state=state,
        version_id=sha256(canonical_jsonl(identity)),
    )


def version_record(version: ResourceVersion) -> dict[str, object]:
    return {
        "resource_id": version.resource_id,
        "path": version.path,
        "blob_id": version.blob_id,
        "state": version.state.value,
        "version_id": version.version_id,
    }


def parse_version(value: object, *, label: str) -> ResourceVersion:
    if not isinstance(value, dict) or set(value) != {"blob_id", "path", "resource_id", "state", "version_id"}:
        raise CorruptRepositoryError(f"{label} has an invalid resource version")
    try:
        state = ResourceState(value["state"])
        resource_id = value["resource_id"]
        path = normalize_relative(value["path"])
        blob_id = value["blob_id"]
        version_id = value["version_id"]
    except (TypeError, ValueError) as exc:
        raise CorruptRepositoryError(f"{label} has an invalid resource version") from exc
    if not isinstance(resource_id, str) or not resource_id or not valid_hash(blob_id) or not valid_hash(version_id):
        raise CorruptRepositoryError(f"{label} has an invalid resource version")
    return ResourceVersion(
        resource_id=resource_id,
        path=path,
        blob_id=blob_id,
        state=state,
        version_id=version_id,
    )


def tree_record(resources: Mapping[str, ResourceVersion]) -> dict[str, object]:
    return {
        "version": FORMAT_VERSION,
        "kind": "tree",
        "resources": [version_record(resources[key]) for key in sorted(resources)],
    }


def parse_tree(record: Mapping[str, object], *, tree_id: str) -> dict[str, ResourceVersion]:
    if set(record) != {"kind", "resources", "version"}:
        raise CorruptRepositoryError(f"tree {tree_id} has an unsupported shape")
    raw_resources = record.get("resources")
    if not isinstance(raw_resources, list):
        raise CorruptRepositoryError(f"tree {tree_id} has an invalid resource list")
    resources: dict[str, ResourceVersion] = {}
    active_paths: set[str] = set()
    for index, raw in enumerate(raw_resources):
        parsed = parse_version(raw, label=f"tree {tree_id} resource {index}")
        if parsed.resource_id in resources:
            raise CorruptRepositoryError(f"tree {tree_id} repeats resource {parsed.resource_id}")
        if parsed.state is ResourceState.ACTIVE:
            if parsed.path in active_paths:
                raise CorruptRepositoryError(f"tree {tree_id} repeats active path {parsed.path}")
            active_paths.add(parsed.path)
        resources[parsed.resource_id] = parsed
    if list(resources) != sorted(resources):
        raise CorruptRepositoryError(f"tree {tree_id} resources are not ordered")
    collision = ancestor_collision(list(active_paths))
    if collision is not None:
        raise CorruptRepositoryError(f"tree {tree_id} has overlapping active paths {collision[0]} and {collision[1]}")
    return resources


def change_record(change: ResourceChange) -> dict[str, object]:
    return {
        "action": change.action,
        "before": None if change.before is None else version_record(change.before),
        "after": None if change.after is None else version_record(change.after),
    }


def commit_record(
    *,
    parent_id: str | None,
    tree_id: str,
    actor: str,
    origin: str,
    reason: str,
    timestamp: datetime,
    changes: Iterable[ResourceChange],
) -> dict[str, object]:
    return {
        "version": FORMAT_VERSION,
        "kind": "commit",
        "parent_id": parent_id,
        "tree_id": tree_id,
        "actor": actor,
        "origin": origin,
        "reason": reason,
        "timestamp": _format_timestamp(timestamp),
        "changes": [change_record(change) for change in changes],
    }


def parse_commit(record: Mapping[str, object], *, commit_id: str) -> Commit:
    expected = {
        "actor",
        "changes",
        "kind",
        "origin",
        "parent_id",
        "reason",
        "timestamp",
        "tree_id",
        "version",
    }
    if set(record) != expected:
        raise CorruptRepositoryError(f"commit {commit_id} has an unsupported shape")
    parent_id = record.get("parent_id")
    if parent_id is not None and not valid_hash(parent_id):
        raise CorruptRepositoryError(f"commit {commit_id} has an invalid parent")
    tree_id = record.get("tree_id")
    if not valid_hash(tree_id):
        raise CorruptRepositoryError(f"commit {commit_id} has an invalid tree")
    for field in ("actor", "origin", "reason"):
        if not isinstance(record.get(field), str) or not record[field]:
            raise CorruptRepositoryError(f"commit {commit_id} has invalid {field}")
    raw_changes = record.get("changes")
    if not isinstance(raw_changes, list) or not raw_changes:
        raise CorruptRepositoryError(f"commit {commit_id} has no changes")
    changes = tuple(
        _parse_change(raw, label=f"commit {commit_id} change {index}") for index, raw in enumerate(raw_changes)
    )
    resource_ids = [
        change.after.resource_id if change.after is not None else change.before.resource_id  # type: ignore[union-attr]
        for change in changes
    ]
    if resource_ids != sorted(resource_ids) or len(resource_ids) != len(set(resource_ids)):
        raise CorruptRepositoryError(f"commit {commit_id} changes are not uniquely ordered")
    return Commit(
        commit_id=commit_id,
        parent_id=parent_id,
        tree_id=tree_id,
        actor=record["actor"],
        origin=record["origin"],
        reason=record["reason"],
        timestamp=_parse_timestamp(record.get("timestamp"), label=f"commit {commit_id}"),
        changes=changes,
    )


def validate_commit_transition(
    commit: Commit,
    before: Mapping[str, ResourceVersion],
    after: Mapping[str, ResourceVersion],
) -> None:
    changed_ids = sorted(
        resource_id for resource_id in set(before) | set(after) if before.get(resource_id) != after.get(resource_id)
    )
    recorded_ids = [_change_resource_id(change) for change in commit.changes]
    if recorded_ids != changed_ids:
        raise CorruptRepositoryError(f"commit {commit.commit_id} changes do not match its parent and resulting trees")
    for change in commit.changes:
        resource_id = _change_resource_id(change)
        if change.before != before.get(resource_id) or change.after != after.get(resource_id):
            raise CorruptRepositoryError(f"commit {commit.commit_id} records the wrong state for {resource_id}")
        if change.after is not None:
            expected_after = make_version(
                change.after.resource_id,
                change.after.path,
                change.after.blob_id,
                change.after.state,
                previous_version_id=None if change.before is None else change.before.version_id,
            )
            if expected_after.version_id != change.after.version_id:
                raise CorruptRepositoryError(
                    f"commit {commit.commit_id} has an invalid resource version chain for {resource_id}"
                )
        if not _valid_change_action(change):
            raise CorruptRepositoryError(
                f"commit {commit.commit_id} has invalid {change.action} semantics for {resource_id}"
            )


def transaction_record(
    *,
    old_head: str | None,
    new_head: str,
    tree_id: str,
    key_hash: str,
    request_hash: str,
    rows: Iterable[TransactionRow],
) -> dict[str, object]:
    return {
        "version": FORMAT_VERSION,
        "kind": "transaction",
        "old_head": old_head,
        "new_head": new_head,
        "tree_id": tree_id,
        "key_hash": key_hash,
        "request_hash": request_hash,
        "rows": [{"path": row.path, "old_blob": row.old_blob, "new_blob": row.new_blob} for row in rows],
    }


def parse_transaction(record: Mapping[str, object], *, transaction_id: str) -> Transaction:
    if set(record) != {
        "key_hash",
        "kind",
        "new_head",
        "old_head",
        "request_hash",
        "rows",
        "tree_id",
        "version",
    }:
        raise CorruptRepositoryError(f"transaction {transaction_id} has an unsupported shape")
    if record.get("version") != FORMAT_VERSION or record.get("kind") != "transaction":
        raise CorruptRepositoryError(f"transaction {transaction_id} has an unsupported format")
    for field in ("new_head", "tree_id", "key_hash", "request_hash"):
        if not valid_hash(record.get(field)):
            raise CorruptRepositoryError(f"transaction {transaction_id} has an invalid {field}")
    old_head = record.get("old_head")
    if old_head is not None and not valid_hash(old_head):
        raise CorruptRepositoryError(f"transaction {transaction_id} has an invalid old_head")
    raw_rows = record.get("rows")
    if not isinstance(raw_rows, list):
        raise CorruptRepositoryError(f"transaction {transaction_id} has invalid rows")
    rows: list[TransactionRow] = []
    paths: set[str] = set()
    for index, raw in enumerate(raw_rows):
        if not isinstance(raw, dict) or set(raw) != {"new_blob", "old_blob", "path"}:
            raise CorruptRepositoryError(f"transaction {transaction_id} row {index} is invalid")
        try:
            path = normalize_relative(raw["path"])
        except (TypeError, ValueError) as exc:
            raise CorruptRepositoryError(f"transaction {transaction_id} row {index} has an invalid path") from exc
        if path in paths:
            raise CorruptRepositoryError(f"transaction {transaction_id} repeats path {path}")
        old_blob = raw.get("old_blob")
        new_blob = raw.get("new_blob")
        if old_blob is not None and not valid_hash(old_blob):
            raise CorruptRepositoryError(f"transaction {transaction_id} row {index} has an invalid old blob")
        if new_blob is not None and not valid_hash(new_blob):
            raise CorruptRepositoryError(f"transaction {transaction_id} row {index} has an invalid new blob")
        if old_blob == new_blob:
            raise CorruptRepositoryError(f"transaction {transaction_id} row {index} makes no change")
        paths.add(path)
        rows.append(TransactionRow(path=path, old_blob=old_blob, new_blob=new_blob))
    if [row.path for row in rows] != sorted(row.path for row in rows):
        raise CorruptRepositoryError(f"transaction {transaction_id} rows are not ordered")
    return Transaction(
        transaction_id=transaction_id,
        old_head=old_head,
        new_head=record["new_head"],
        tree_id=record["tree_id"],
        key_hash=record["key_hash"],
        request_hash=record["request_hash"],
        rows=tuple(rows),
    )


def _parse_change(value: object, *, label: str) -> ResourceChange:
    if not isinstance(value, dict) or set(value) != {"action", "after", "before"}:
        raise CorruptRepositoryError(f"{label} has an invalid shape")
    action = value.get("action")
    if not isinstance(action, str) or not action:
        raise CorruptRepositoryError(f"{label} has an invalid action")
    before = None if value.get("before") is None else parse_version(value["before"], label=f"{label} before")
    after = None if value.get("after") is None else parse_version(value["after"], label=f"{label} after")
    if before is None and after is None:
        raise CorruptRepositoryError(f"{label} has no resource state")
    before_id = None if before is None else before.resource_id
    after_id = None if after is None else after.resource_id
    if before_id is not None and after_id is not None and before_id != after_id:
        raise CorruptRepositoryError(f"{label} changes resource identity")
    return ResourceChange(action=action, before=before, after=after)


def _change_resource_id(change: ResourceChange) -> str:
    if change.after is not None:
        return change.after.resource_id
    if change.before is not None:
        return change.before.resource_id
    raise CorruptRepositoryError("resource change has no identity")


def _valid_change_action(change: ResourceChange) -> bool:
    before = change.before
    after = change.after
    if change.action == "create":
        return before is None and after is not None and after.state is ResourceState.ACTIVE
    if before is None or after is None or before.resource_id != after.resource_id or before == after:
        return False
    if change.action == "update":
        return (
            before.state is ResourceState.ACTIVE
            and after.state is ResourceState.ACTIVE
            and before.path == after.path
            and before.blob_id != after.blob_id
        )
    if change.action == "move":
        return (
            before.state is ResourceState.ACTIVE
            and after.state is ResourceState.ACTIVE
            and (before.path != after.path or before.blob_id != after.blob_id)
        )
    if change.action == "archive":
        return (
            before.state is ResourceState.ACTIVE
            and after.state is ResourceState.ARCHIVED
            and before.path == after.path
            and before.blob_id == after.blob_id
        )
    if change.action == "restore":
        return after.state is ResourceState.ACTIVE
    return False


def _format_timestamp(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("commit timestamp must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object, *, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise CorruptRepositoryError(f"{label} has an invalid timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise CorruptRepositoryError(f"{label} has an invalid timestamp") from exc
    if _format_timestamp(parsed) != value:
        raise CorruptRepositoryError(f"{label} timestamp is not canonical")
    return parsed
