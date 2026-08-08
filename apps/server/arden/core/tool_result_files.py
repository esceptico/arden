"""Session-local on-disk store for large tool results, scoped per session.

Oversized tool output is written under the *session's own* results dir and the
model re-reads/searches it by path (offset/limit/grep) — the pattern Claude Code
uses. Scoping per session is load-bearing: an agent that greps its offloaded
results then only ever sees its OWN session's results, not a global pile of every
session's history. A flat global store (which this used to be) grew unbounded —
to 5GB across weeks — and a subagent grepping it re-matched its own + historical
outputs forever, never converging. `prune_offload_store` bounds growth by age so
it can't accumulate again. Long-term raw evidence is stored separately via
core.raw_tool_results and the tool_results manifest.

Every lookup requires its owning session. Tool-call IDs are only unique within
their run and must never select another session's cached payload.
"""

import re
import shutil
import time
from copy import deepcopy
from pathlib import Path
from typing import Any

from arden.settings import ARDEN_DIR

RESULTS_BASE = ARDEN_DIR / "tool-results"
# Offloaded results older than this are pruned on startup. Recent results stay
# re-readable; old ones (referenced only by long-finished runs) are disposable.
RESULTS_MAX_AGE_SECONDS = 24 * 3600


def _safe(session_id: str) -> str:
    # Child session ids contain "::"; keep dir names filesystem-safe.
    return re.sub(r"[^A-Za-z0-9_.-]", "_", session_id) or "_"


def session_results_dir(session_id: str) -> Path:
    return RESULTS_BASE / _safe(session_id)


def result_file_path(session_id: str, tool_call_id: str) -> Path:
    return session_results_dir(session_id) / f"{tool_call_id}.txt"


def result_payload_file_path(session_id: str, tool_call_id: str) -> Path:
    return session_results_dir(session_id) / f"{tool_call_id}.payload.json"


def _ensure_ignore_marker() -> None:
    """Drop a ripgrep `.ignore` at the store root so file_search_text/file_find never
    walk offloaded results — the result store is not a search corpus. ripgrep
    honors this even when a search is rooted AT the store (returns nothing instead
    of grepping GBs), while an explicit single-file file_read still works."""
    marker = RESULTS_BASE / ".ignore"
    if not marker.exists():
        RESULTS_BASE.mkdir(parents=True, exist_ok=True)
        marker.write_text("*\n", encoding="utf-8")


def persist_result(session_id: str, tool_call_id: str, content: str) -> Path:
    path = result_file_path(session_id, tool_call_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_ignore_marker()
    path.write_text(content, encoding="utf-8")
    return path


def persist_result_payload(session_id: str, tool_call_id: str, content: str) -> Path:
    path = result_payload_file_path(session_id, tool_call_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    _ensure_ignore_marker()
    path.write_text(content, encoding="utf-8")
    return path


def clone_result_files(source_session_id: str, target_session_id: str, tool_call_ids: set[str]) -> None:
    """Copy readable caches for a branch so it does not depend on its source."""
    if not tool_call_ids:
        return
    _ensure_ignore_marker()
    for tool_call_id in tool_call_ids:
        pairs = (
            (result_file_path(source_session_id, tool_call_id), result_file_path(target_session_id, tool_call_id)),
            (
                result_payload_file_path(source_session_id, tool_call_id),
                result_payload_file_path(target_session_id, tool_call_id),
            ),
        )
        for source, target in pairs:
            if not source.exists():
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)


def rewrite_result_paths(value: Any, source_session_id: str, target_session_id: str) -> Any:
    """Rebase only Arden-owned message pointers, preserving provider replay state."""
    source = str(session_results_dir(source_session_id))
    target = str(session_results_dir(target_session_id))

    def rewrite_owned(item: Any) -> Any:
        if isinstance(item, str):
            return item.replace(source, target)
        if isinstance(item, dict):
            return {key: rewrite_owned(child) for key, child in item.items()}
        if isinstance(item, list):
            return [rewrite_owned(child) for child in item]
        if isinstance(item, tuple):
            return tuple(rewrite_owned(child) for child in item)
        return deepcopy(item)

    def rewrite_message(message: Any) -> Any:
        if not isinstance(message, dict):
            return deepcopy(message)
        copied = deepcopy(message)
        if copied.get("role") == "tool":
            for key in ("content", "data"):
                if key in copied:
                    copied[key] = rewrite_owned(copied[key])
        compaction = copied.get("compaction")
        if isinstance(compaction, dict) and compaction.get("kind") == "session_handoff":
            if "content" in copied:
                copied["content"] = rewrite_owned(copied["content"])
            copied["compaction"] = rewrite_owned(compaction)
        return copied

    if isinstance(value, list):
        return [rewrite_message(message) for message in value]
    return rewrite_message(value)


def purge_session_results(session_id: str) -> None:
    shutil.rmtree(session_results_dir(session_id), ignore_errors=True)


def prune_offload_store(max_age_seconds: int = RESULTS_MAX_AGE_SECONDS) -> int:
    """Delete offloaded result files older than max_age_seconds and drop empty
    session dirs. Returns the count removed. A bounded store keeps grep/read fast
    and stops the cross-session accumulation that made search never converge."""
    if not RESULTS_BASE.exists():
        return 0
    _ensure_ignore_marker()
    cutoff = time.time() - max_age_seconds
    removed = 0
    for pattern in ("*.txt", "*.payload.json"):
        for path in RESULTS_BASE.rglob(pattern):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                pass
    for child in RESULTS_BASE.iterdir():
        if child.is_dir():
            try:
                child.rmdir()  # only succeeds if empty
            except OSError:
                pass
    return removed
