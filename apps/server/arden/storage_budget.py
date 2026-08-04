"""Typed storage inventory, planning, and safe filesystem maintenance."""

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from arden.core.raw_tool_results import delete_stale_raw_tool_result

_GIB = 1024**3
_CLEAN_TARGET_RATIO = 0.85
_ORPHAN_GRACE = timedelta(days=7)
_KEEP_MANIFEST = "storage-kept-backups.json"

MeasurementKind = Literal["physical", "logical_estimate"]
CleanupKind = Literal[
    "stale_tool_result",
    "expired_backup",
    "cold_convert_session",
    "delete_cold_session",
    "delete_current_session",
]


@dataclass(frozen=True)
class StorageCategory:
    id: str
    label: str
    total_bytes: int
    reclaimable_bytes: int
    item_count: int
    policy_tier: int | None
    protection_reason: str | None
    description: str
    measurement_kind: MeasurementKind = "physical"


@dataclass(frozen=True)
class StorageBudgetReport:
    status: str
    total_bytes: int
    reclaimable_bytes: int
    protected_bytes: int
    reclaimed_bytes: int
    max_bytes: int | None
    target_bytes: int | None
    checked_at: str
    categories: tuple[StorageCategory, ...] = ()
    reclaimed_by_category: dict[str, int] | None = None
    database_reclaim_mode: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class StorageSessionCandidate:
    session_id: str
    archived: bool
    storage_state: Literal["hot", "cold"]
    last_activity: str
    logical_bytes: int
    message_count: int
    protected_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class StoragePlanAction:
    tier: int
    kind: CleanupKind
    category_id: str
    resource_id: str
    estimated_reclaimable_bytes: int
    destructive: bool
    description: str


@dataclass(frozen=True)
class StorageCleanupPlan:
    plan_id: str
    before_bytes: int
    target_bytes: int
    estimated_after_bytes: int
    estimated_reclaimable_bytes: int
    attainable: bool
    actions: tuple[StoragePlanAction, ...]
    blockers: tuple[str, ...]
    created_at: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class _Candidate:
    kind: Literal["stale_tool_result", "expired_backup"]
    path: Path
    relative_path: str
    category_id: str
    size: int
    modified_at: float


@dataclass(frozen=True)
class StorageInventory:
    report: StorageBudgetReport
    candidates: tuple[_Candidate, ...]


@dataclass(frozen=True)
class StorageBackup:
    relative_path: str
    size_bytes: int
    modified_at: str
    kept: bool
    expired: bool


@dataclass(frozen=True)
class _CategoryDefinition:
    label: str
    tier: int | None
    protection_reason: str | None
    description: str


_CATEGORIES: dict[str, _CategoryDefinition] = {
    "backup_archives": _CategoryDefinition(
        "Backup archives",
        1,
        None,
        "Harness, migration, and rollback files. Auto-created backups expire after the configured retention window.",
    ),
    "chat_history": _CategoryDefinition(
        "Chat history",
        2,
        "Chats require an explicit cleanup plan.",
        "The live session database, including current and archived conversations.",
    ),
    "cold_chats": _CategoryDefinition(
        "Cold chats",
        2,
        "Cold chats remain restorable until explicitly deleted.",
        "Compressed, restorable archived conversations that are not kept in the live database.",
    ),
    "tool_results": _CategoryDefinition(
        "Tool results",
        0,
        None,
        "Large tool outputs and blobs. Only expired or unreferenced data is automatically reclaimed.",
    ),
    "search_indexes": _CategoryDefinition(
        "Search indexes",
        0,
        "Live indexes are retained until a rebuild-safe owner is available.",
        "Derived full-text and vector indexes used to search chats and knowledge.",
    ),
    "memory_and_artifacts": _CategoryDefinition(
        "Memory and artifacts",
        None,
        "Canonical knowledge is never deleted by storage pressure alone.",
        "Wiki memory, generated artifacts, notes, and their durable metadata.",
    ),
    "logs": _CategoryDefinition(
        "Logs",
        0,
        "Active logs follow their own rotation policy.",
        "Diagnostic logs and rotated log files.",
    ),
    "configuration": _CategoryDefinition(
        "Configuration and accounts",
        None,
        "Configuration and credentials are always retained.",
        "Settings, account metadata, credentials, and small runtime state.",
    ),
    "other": _CategoryDefinition(
        "Other Arden data",
        None,
        "Unclassified data is retained fail-closed.",
        "Arden-owned files that do not yet have a dedicated retention policy.",
    ),
}


def _walk_regular_files(root: Path):
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = list(os.scandir(directory))
        except (FileNotFoundError, NotADirectoryError, PermissionError):
            continue
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    yield Path(entry.path), entry.stat(follow_symlinks=False)
            except (FileNotFoundError, PermissionError):
                continue


def _blob_hash(path: Path) -> str | None:
    name = path.name
    suffix = ".txt.gz"
    if not name.endswith(suffix):
        return None
    digest = name[: -len(suffix)]
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        return None
    return digest


def _category_id(relative: Path) -> str:
    parts = relative.parts
    first = parts[0] if parts else relative.name
    lowered = first.lower()
    if lowered == "archive":
        return "backup_archives"
    if lowered == "cold-sessions":
        return "cold_chats"
    if lowered.startswith("sessions.db") or lowered == "sessions.db.compaction-manifest.json":
        return "chat_history"
    if lowered in {"blobs", "tool-results"}:
        return "tool_results"
    if lowered.startswith("search.db") or lowered in {"indexes", "index"}:
        return "search_indexes"
    if lowered.startswith("memory.db") or lowered in {"memory", "artifacts", "notes", "wiki"}:
        return "memory_and_artifacts"
    if lowered == "logs" or lowered.endswith(".log"):
        return "logs"
    if (
        lowered.endswith(".json")
        or lowered.endswith(".toml")
        or lowered.endswith(".p8")
        or lowered in {"google_tokens", "mcp_oauth", "skills", "tools", "plans"}
    ):
        return "configuration"
    return "other"


def _load_kept_backups(root: Path) -> set[str]:
    path = root / _KEEP_MANIFEST
    try:
        raw = json.loads(path.read_text())
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return set()
    values = raw.get("paths", []) if isinstance(raw, dict) else []
    return {value for value in values if isinstance(value, str) and value.startswith("archive/")}


def list_kept_backups(root: Path) -> set[str]:
    return _load_kept_backups(root.resolve())


def set_backup_keep(root: Path, relative_path: str, *, keep: bool) -> set[str]:
    """Persist a manual Keep exception for one regular file under archive/."""

    root = root.resolve()
    archive_root = (root / "archive").resolve()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(archive_root)
    except ValueError as error:
        raise ValueError("backup path must be inside archive/") from error
    if candidate.is_symlink() or not candidate.is_file():
        raise FileNotFoundError(relative_path)
    normalized = candidate.relative_to(root).as_posix()
    kept = _load_kept_backups(root)
    if keep:
        kept.add(normalized)
    else:
        kept.discard(normalized)
    payload = json.dumps({"version": 1, "paths": sorted(kept)}, indent=2, sort_keys=True) + "\n"
    destination = root / _KEEP_MANIFEST
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(payload)
    temporary.replace(destination)
    return kept


def list_storage_backups(
    root: Path, *, backup_retention_days: int = 14, now: datetime | None = None
) -> tuple[StorageBackup, ...]:
    now = now or datetime.now(UTC)
    root = root.resolve()
    archive_root = root / "archive"
    kept = _load_kept_backups(root)
    cutoff = (now - timedelta(days=max(1, backup_retention_days))).timestamp()
    backups: list[StorageBackup] = []
    for path, metadata in _walk_regular_files(archive_root):
        relative = path.relative_to(root).as_posix()
        backups.append(
            StorageBackup(
                relative_path=relative,
                size_bytes=metadata.st_size,
                modified_at=datetime.fromtimestamp(metadata.st_mtime, UTC).isoformat(),
                kept=relative in kept,
                expired=metadata.st_mtime <= cutoff,
            )
        )
    return tuple(sorted(backups, key=lambda item: (item.modified_at, item.relative_path), reverse=True))


def inspect_storage(
    root: Path,
    *,
    max_space_gb: float | None,
    referenced_tool_result_hashes: set[str],
    backup_retention_days: int = 14,
    now: datetime | None = None,
    reclaimed_bytes: int = 0,
    reclaimed_by_category: dict[str, int] | None = None,
    database_reclaim_mode: str | None = None,
) -> StorageInventory:
    """Inventory every regular Arden file and its owner-defined reclaimability."""

    now = now or datetime.now(UTC)
    root = root.resolve()
    blob_root = (root / "blobs" / "tool-results").resolve()
    archive_root = (root / "archive").resolve()
    orphan_before = (now - _ORPHAN_GRACE).timestamp()
    backup_before = (now - timedelta(days=max(1, backup_retention_days))).timestamp()
    kept_backups = _load_kept_backups(root)
    totals = dict.fromkeys(_CATEGORIES, 0)
    counts = dict.fromkeys(_CATEGORIES, 0)
    reclaimable = dict.fromkeys(_CATEGORIES, 0)
    candidates: list[_Candidate] = []

    for path, metadata in _walk_regular_files(root):
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        category_id = _category_id(relative)
        size = metadata.st_size
        totals[category_id] += size
        counts[category_id] += 1

        try:
            blob_relative = path.resolve().relative_to(blob_root)
        except ValueError:
            blob_relative = None
        if blob_relative is not None:
            digest = _blob_hash(blob_relative)
            if digest is not None and digest not in referenced_tool_result_hashes and metadata.st_mtime <= orphan_before:
                candidate = _Candidate(
                    kind="stale_tool_result",
                    path=path,
                    relative_path=relative.as_posix(),
                    category_id=category_id,
                    size=size,
                    modified_at=metadata.st_mtime,
                )
                candidates.append(candidate)
                reclaimable[category_id] += size
                continue

        try:
            path.resolve().relative_to(archive_root)
        except ValueError:
            continue
        normalized = relative.as_posix()
        if normalized not in kept_backups and metadata.st_mtime <= backup_before:
            candidate = _Candidate(
                kind="expired_backup",
                path=path,
                relative_path=normalized,
                category_id=category_id,
                size=size,
                modified_at=metadata.st_mtime,
            )
            candidates.append(candidate)
            reclaimable[category_id] += size

    categories = tuple(
        StorageCategory(
            id=category_id,
            label=definition.label,
            total_bytes=totals[category_id],
            reclaimable_bytes=reclaimable[category_id],
            item_count=counts[category_id],
            policy_tier=definition.tier,
            protection_reason=definition.protection_reason,
            description=definition.description,
        )
        for category_id, definition in _CATEGORIES.items()
        if totals[category_id] > 0
    )
    total = sum(category.total_bytes for category in categories)
    reclaimable_total = sum(category.reclaimable_bytes for category in categories)
    max_bytes = None if max_space_gb is None else int(max_space_gb * _GIB)
    target_bytes = None if max_bytes is None else int(max_bytes * _CLEAN_TARGET_RATIO)
    if max_bytes is None:
        status = "reclaimed" if reclaimed_bytes else "disabled"
    elif total <= max_bytes:
        status = "reclaimed" if reclaimed_bytes else "ok"
    else:
        status = "quota_blocked"
    report = StorageBudgetReport(
        status=status,
        total_bytes=total,
        reclaimable_bytes=reclaimable_total,
        protected_bytes=total - reclaimable_total,
        reclaimed_bytes=reclaimed_bytes,
        max_bytes=max_bytes,
        target_bytes=target_bytes,
        checked_at=now.isoformat(),
        categories=categories,
        reclaimed_by_category=reclaimed_by_category or {},
        database_reclaim_mode=database_reclaim_mode,
    )
    return StorageInventory(report=report, candidates=tuple(candidates))


def _delete_expired_backup(candidate: _Candidate, *, root: Path) -> bool:
    archive_root = (root / "archive").resolve()
    try:
        candidate.path.resolve().relative_to(archive_root)
        stat = candidate.path.stat(follow_symlinks=False)
    except (ValueError, FileNotFoundError, OSError):
        return False
    if candidate.path.is_symlink() or not candidate.path.is_file():
        return False
    if stat.st_size != candidate.size or stat.st_mtime != candidate.modified_at:
        return False
    try:
        candidate.path.unlink()
    except OSError:
        return False
    parent = candidate.path.parent
    while parent != archive_root:
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent
    return True


def enforce_storage_budget(
    root: Path,
    *,
    max_space_gb: float | None,
    referenced_tool_result_hashes: set[str],
    backup_retention_days: int = 14,
    expire_backups: bool = False,
    now: datetime | None = None,
    database_reclaim_mode: str | None = None,
) -> StorageBudgetReport:
    """Apply only reference-safe blob GC and explicitly enabled backup TTL."""

    now = now or datetime.now(UTC)
    root = root.resolve()
    inventory = inspect_storage(
        root,
        max_space_gb=max_space_gb,
        referenced_tool_result_hashes=referenced_tool_result_hashes,
        backup_retention_days=backup_retention_days,
        now=now,
        database_reclaim_mode=database_reclaim_mode,
    )
    total = inventory.report.total_bytes
    target = inventory.report.target_bytes
    reclaimed_by_category: dict[str, int] = {}

    for candidate in sorted(inventory.candidates, key=lambda item: (item.modified_at, item.relative_path)):
        should_delete = candidate.kind == "expired_backup" and expire_backups
        if candidate.kind == "stale_tool_result" and target is not None and total > target:
            should_delete = True
        if not should_delete:
            continue
        if candidate.kind == "stale_tool_result":
            deleted = delete_stale_raw_tool_result(
                candidate.path,
                blob_root=root / "blobs" / "tool-results",
                older_than_timestamp=(now - _ORPHAN_GRACE).timestamp(),
                expected_size=candidate.size,
            )
        else:
            deleted = _delete_expired_backup(candidate, root=root)
        if not deleted:
            continue
        total -= candidate.size
        reclaimed_by_category[candidate.category_id] = (
            reclaimed_by_category.get(candidate.category_id, 0) + candidate.size
        )

    reclaimed = sum(reclaimed_by_category.values())
    return inspect_storage(
        root,
        max_space_gb=max_space_gb,
        referenced_tool_result_hashes=referenced_tool_result_hashes,
        backup_retention_days=backup_retention_days,
        now=now,
        reclaimed_bytes=reclaimed,
        reclaimed_by_category=reclaimed_by_category,
        database_reclaim_mode=database_reclaim_mode,
    ).report


def execute_storage_file_action(
    root: Path,
    action: StoragePlanAction,
    *,
    referenced_tool_result_hashes: set[str],
    backup_retention_days: int,
    now: datetime | None = None,
) -> int:
    """Revalidate and execute one exact filesystem action from a plan."""

    now = now or datetime.now(UTC)
    inventory = inspect_storage(
        root,
        max_space_gb=None,
        referenced_tool_result_hashes=referenced_tool_result_hashes,
        backup_retention_days=backup_retention_days,
        now=now,
    )
    candidate = next(
        (
            candidate
            for candidate in inventory.candidates
            if candidate.kind == action.kind
            and candidate.relative_path == action.resource_id
            and candidate.size == action.estimated_reclaimable_bytes
        ),
        None,
    )
    if candidate is None:
        raise RuntimeError(f"planned storage resource is stale: {action.resource_id}")
    if candidate.kind == "stale_tool_result":
        deleted = delete_stale_raw_tool_result(
            candidate.path,
            blob_root=root / "blobs" / "tool-results",
            older_than_timestamp=(now - _ORPHAN_GRACE).timestamp(),
            expected_size=candidate.size,
        )
    else:
        deleted = _delete_expired_backup(candidate, root=root.resolve())
    if not deleted:
        raise RuntimeError(f"storage resource changed before deletion: {action.resource_id}")
    return candidate.size


def build_storage_cleanup_plan(
    inventory: StorageInventory,
    *,
    target_bytes: int,
    sessions: list[StorageSessionCandidate],
    allow_archived_chats: bool,
    allow_delete_cold_chats: bool,
    allow_current_chats: bool,
    current_chat_minimum: int,
    created_at: datetime | None = None,
) -> StorageCleanupPlan:
    """Build a deterministic, non-mutating pressure ladder."""

    target_bytes = max(0, target_bytes)
    needed = max(0, inventory.report.total_bytes - target_bytes)
    actions: list[StoragePlanAction] = []
    estimated = 0

    def add(action: StoragePlanAction) -> None:
        nonlocal estimated
        if estimated >= needed:
            return
        actions.append(action)
        estimated += max(0, action.estimated_reclaimable_bytes)

    for candidate in sorted(inventory.candidates, key=lambda item: (item.modified_at, item.relative_path)):
        add(
            StoragePlanAction(
                tier=0 if candidate.kind == "stale_tool_result" else 1,
                kind=candidate.kind,
                category_id=candidate.category_id,
                resource_id=candidate.relative_path,
                estimated_reclaimable_bytes=candidate.size,
                destructive=False,
                description=(
                    "Remove an unreferenced tool result"
                    if candidate.kind == "stale_tool_result"
                    else "Remove an expired backup archive"
                ),
            )
        )

    archived = sorted(
        (session for session in sessions if session.archived and not session.protected_reasons),
        key=lambda item: (item.last_activity, item.session_id),
    )
    if allow_archived_chats:
        for session in archived:
            if session.storage_state == "cold":
                if not allow_delete_cold_chats:
                    continue
                add(
                    StoragePlanAction(
                        tier=2,
                        kind="delete_cold_session",
                        category_id="cold_chats",
                        resource_id=session.session_id,
                        estimated_reclaimable_bytes=session.logical_bytes,
                        destructive=True,
                        description="Permanently delete a cold archived chat",
                    )
                )
            else:
                # Conservative estimate: full-fidelity zstd bundles observed in
                # practice use well below 40% of canonical transcript bytes.
                estimated_cold = max(4096, session.logical_bytes * 2 // 5)
                add(
                    StoragePlanAction(
                        tier=2,
                        kind="cold_convert_session",
                        category_id="chat_history",
                        resource_id=session.session_id,
                        estimated_reclaimable_bytes=max(0, session.logical_bytes - estimated_cold),
                        destructive=False,
                        description="Convert an archived chat to restorable cold storage",
                    )
                )

    current = sorted(
        (session for session in sessions if not session.archived),
        key=lambda item: (item.last_activity, item.session_id),
        reverse=True,
    )
    if allow_current_chats:
        for session in reversed(current[max(0, current_chat_minimum) :]):
            if session.protected_reasons:
                continue
            add(
                StoragePlanAction(
                    tier=3,
                    kind="delete_current_session",
                    category_id="chat_history",
                    resource_id=session.session_id,
                    estimated_reclaimable_bytes=session.logical_bytes,
                    destructive=True,
                    description="Permanently delete an inactive current chat",
                )
            )

    protected_counts: dict[str, int] = {}
    for session in sessions:
        for reason in session.protected_reasons:
            protected_counts[reason] = protected_counts.get(reason, 0) + 1
    blockers = [f"{count} chat(s) protected: {reason}" for reason, count in sorted(protected_counts.items())]
    if estimated < needed:
        blockers.append(f"Target remains short by {needed - estimated} bytes with enabled cleanup tiers")

    created_at = created_at or datetime.now(UTC)
    identity = {
        "before_bytes": inventory.report.total_bytes,
        "target_bytes": target_bytes,
        "actions": [asdict(action) for action in actions],
        "sessions": [asdict(session) for session in sorted(sessions, key=lambda item: item.session_id)],
        "files": [
            [candidate.kind, candidate.relative_path, candidate.size, candidate.modified_at]
            for candidate in sorted(inventory.candidates, key=lambda item: item.relative_path)
        ],
    }
    plan_id = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    estimated_after = max(0, inventory.report.total_bytes - estimated)
    return StorageCleanupPlan(
        plan_id=plan_id,
        before_bytes=inventory.report.total_bytes,
        target_bytes=target_bytes,
        estimated_after_bytes=estimated_after,
        estimated_reclaimable_bytes=estimated,
        attainable=estimated_after <= target_bytes,
        actions=tuple(actions),
        blockers=tuple(blockers),
        created_at=created_at.isoformat(),
    )
