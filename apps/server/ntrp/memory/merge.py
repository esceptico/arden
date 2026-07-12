"""Conservative exact-line three-way merge for generated memory pages."""

from __future__ import annotations

import difflib
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(frozen=True)
class MergeResult:
    merged: bytes | None
    candidate: bytes
    review_required: bool
    reason: Literal["merged", "overlap", "missing_base", "stale_source"]


@dataclass(frozen=True)
class _Hunk:
    start: int
    end: int
    replacement: tuple[bytes, ...]


def _hunks(base: tuple[bytes, ...], changed: tuple[bytes, ...]) -> tuple[_Hunk, ...]:
    matcher = difflib.SequenceMatcher(None, base, changed, autojunk=False)
    return tuple(
        _Hunk(i1, i2, changed[j1:j2])
        for tag, i1, i2, j1, j2 in matcher.get_opcodes()
        if tag != "equal"
    )


def _overlap(left: _Hunk, right: _Hunk) -> bool:
    if left.start == left.end and right.start == right.end:
        return left.start == right.start
    if left.start == left.end:
        return right.start < left.start < right.end
    if right.start == right.end:
        return left.start < right.start < left.end
    return max(left.start, right.start) < min(left.end, right.end)


def _map_position(position: int, current: tuple[_Hunk, ...], *, include_insertions: bool) -> int:
    mapped = position
    for hunk in current:
        if hunk.start == hunk.end:
            if hunk.start < position or (include_insertions and hunk.start == position):
                mapped += len(hunk.replacement)
            continue
        if hunk.end <= position:
            mapped += len(hunk.replacement) - (hunk.end - hunk.start)
    return mapped


def three_way_merge(base: bytes | None, current: bytes, generated: bytes) -> MergeResult:
    """Apply only exact, non-overlapping generated line hunks to current bytes."""
    if base is None:
        return MergeResult(None, generated, True, "missing_base")
    if current == generated:
        return MergeResult(current, current, False, "merged")

    base_lines = tuple(base.splitlines(keepends=True))
    current_lines = tuple(current.splitlines(keepends=True))
    generated_lines = tuple(generated.splitlines(keepends=True))
    current_hunks = _hunks(base_lines, current_lines)
    generated_hunks = _hunks(base_lines, generated_lines)

    for generated_hunk in generated_hunks:
        for current_hunk in current_hunks:
            if not _overlap(generated_hunk, current_hunk):
                continue
            if generated_hunk == current_hunk:
                continue
            return MergeResult(None, generated, True, "overlap")

    merged = list(current_lines)
    for hunk in reversed(generated_hunks):
        start = _map_position(hunk.start, current_hunks, include_insertions=True)
        end = _map_position(hunk.end, current_hunks, include_insertions=False)
        merged[start:end] = hunk.replacement
    content = b"".join(merged)
    return MergeResult(content, content, False, "merged")


def synthesis_page_key(path: str) -> str:
    rel = Path(path)
    if rel.is_absolute() or not rel.parts or any(part in {"", ".", ".."} for part in rel.parts):
        raise ValueError(f"invalid synthesis page path: {path}")
    return hashlib.sha256(rel.as_posix().encode("utf-8")).hexdigest()


def synthesis_base_rel(path: str, revision: str) -> Path:
    name = revision or "empty"
    return Path(".ntrp/maintenance/synthesis-bases") / synthesis_page_key(path) / f"{name}.md"


def synthesis_candidate_rel(path: str, revision: str) -> Path:
    name = revision or "empty"
    return Path(".ntrp/maintenance/synthesis-candidates") / synthesis_page_key(path) / f"{name}.md"


__all__ = [
    "MergeResult",
    "synthesis_base_rel",
    "synthesis_candidate_rel",
    "synthesis_page_key",
    "three_way_merge",
]
