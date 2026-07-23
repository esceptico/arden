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

tool_call_ids are globally unique, so `find_result_file` can still locate a file
from the id alone (glob across session dirs) for layers that don't carry the
session — e.g. the context-budget middleware rewriting an older result to a stub.
"""

import re
import shutil
import time
from pathlib import Path

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


def _ensure_ignore_marker() -> None:
    """Drop a ripgrep `.ignore` at the store root so search_text/find_files never
    walk offloaded results — the result store is not a search corpus. ripgrep
    honors this even when a search is rooted AT the store (returns nothing instead
    of grepping GBs), while an explicit single-file read_file still works."""
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


def find_result_file(tool_call_id: str) -> Path | None:
    """Locate an offloaded result by globally unique id."""
    if not RESULTS_BASE.exists():
        return None
    return next(RESULTS_BASE.glob(f"**/{tool_call_id}.txt"), None)


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
    for path in RESULTS_BASE.rglob("*.txt"):
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
