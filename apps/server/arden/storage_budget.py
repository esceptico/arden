"""Safe, bounded maintenance for Arden-owned storage."""

import os
import stat
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

_GIB = 1024**3
_CLEAN_TARGET_RATIO = 0.85
_ORPHAN_GRACE = timedelta(days=7)


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

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class _Candidate:
    path: Path
    size: int
    modified_at: float


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


def enforce_storage_budget(
    root: Path,
    *,
    max_space_gb: float | None,
    referenced_tool_result_hashes: set[str],
    now: datetime | None = None,
) -> StorageBudgetReport:
    """Inventory Arden and remove only stale, unreferenced tool-result blobs.

    Canonical databases, active/pinned sessions, logs, and explicit archives
    are always classified as protected. Directory symlinks are never followed.
    """

    now = now or datetime.now(UTC)
    root = root.resolve()
    blob_root = (root / "blobs" / "tool-results").resolve()
    orphan_before = (now - _ORPHAN_GRACE).timestamp()
    total = 0
    candidates: list[_Candidate] = []

    for path, metadata in _walk_regular_files(root):
        size = metadata.st_size
        total += size
        try:
            relative = path.resolve().relative_to(blob_root)
        except ValueError:
            continue
        digest = _blob_hash(relative)
        if digest is None or digest in referenced_tool_result_hashes or metadata.st_mtime > orphan_before:
            continue
        candidates.append(_Candidate(path=path, size=size, modified_at=metadata.st_mtime))

    reclaimable = sum(candidate.size for candidate in candidates)
    max_bytes = None if max_space_gb is None else int(max_space_gb * _GIB)
    target_bytes = None if max_bytes is None else int(max_bytes * _CLEAN_TARGET_RATIO)
    reclaimed = 0

    if max_bytes is not None and total > max_bytes:
        for candidate in sorted(candidates, key=lambda item: (item.modified_at, str(item.path))):
            if total - reclaimed <= target_bytes:
                break
            try:
                metadata = candidate.path.lstat()
                resolved = candidate.path.resolve()
                resolved.relative_to(blob_root)
                if not stat.S_ISREG(metadata.st_mode) or metadata.st_size != candidate.size:
                    continue
                candidate.path.unlink()
            except (FileNotFoundError, PermissionError, ValueError):
                continue
            reclaimed += candidate.size

    remaining = total - reclaimed
    remaining_reclaimable = reclaimable - reclaimed
    if max_bytes is None:
        status = "disabled"
    elif total <= max_bytes:
        status = "ok"
    elif target_bytes is not None and remaining <= target_bytes:
        status = "reclaimed"
    else:
        status = "quota_blocked"

    return StorageBudgetReport(
        status=status,
        total_bytes=remaining,
        reclaimable_bytes=remaining_reclaimable,
        protected_bytes=remaining - remaining_reclaimable,
        reclaimed_bytes=reclaimed,
        max_bytes=max_bytes,
        target_bytes=target_bytes,
        checked_at=now.isoformat(),
    )
