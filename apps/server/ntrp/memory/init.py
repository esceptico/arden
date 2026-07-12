"""/init — full memory re-derivation driver.

Wipes every record EXCEPT pinned, resets the curator + consolidate watermarks,
re-derives memory from ALL chat transcripts through the worthiness curator, and
consolidates the resulting pile. Integration state lives in targeted feed
automations (feeds/), not in memory records — there is no ingest pass.

Order matters: backup first (P0), then a FAIL-FAST wipe (P1 — abort the whole
run if it raises, surfacing the backup path), then per-session transcript
re-derivation (P2, isolated so one bad session can't abort the rest), then
consolidation (P3). The `max_llm_calls` budget bounds transcript curation.
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING
from uuid import uuid4

from ntrp.logging import get_logger
from ntrp.memory.models import now_iso

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ntrp.server.runtime.knowledge import KnowledgeRuntime

_logger = get_logger(__name__)

# A wide ceiling so the full chat history is enumerated (recent_session_scopes
# is the sweep's bounded worklist; re-derivation wants every chat session).
_SESSION_ENUMERATION_LIMIT = 100_000
_MAX_CONSOLIDATE_PASSES = 3
# Keep only the most recent N pre-wipe backups; older ones are pruned each /init
# so they don't accumulate unbounded (every run mints a uniquely-named copy).
INIT_BACKUP_KEEP = 3


def _prune_init_backups(db_path: Path, *, keep: int) -> None:
    backups = sorted(db_path.parent.glob(f"{db_path.name}.init-bak-*"))
    for stale in backups[:-keep] if keep > 0 else backups:
        try:
            stale.unlink()
        except OSError:
            _logger.warning("failed to prune old init backup %s", stale, exc_info=True)

async def run_memory_init(
    knowledge: KnowledgeRuntime,
    *,
    max_llm_calls: int = 400,
    wipe: bool = False,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """(Re)derive memory from chat transcripts.

    ADDITIVE by default (`wipe=False`): existing records are KEPT and the
    re-derivation reconciles against them (ADD/UPDATE/SUPERSEDE/NOOP), so /init can
    only enrich memory, never make it thinner. The re-derivation runs the curator
    in BULK mode — a generous gate that captures the user's durable history
    comprehensively, instead of the brutal turn-by-turn worthiness bar that admits
    almost nothing over a full-history pass. `wipe=True` is the destructive reset
    (wipe-except-pinned first) for when the pool is genuinely corrupt.

    Transcript re-derivation drains every chat session regardless of recency.
    Returns a report dict.
    """

    def _report_progress(message: str) -> None:
        if progress is not None:
            progress(message)

    record_store = knowledge._record_store
    curator = knowledge.memory_curator
    consolidate = knowledge._consolidate
    if record_store is None or curator is None:
        raise RuntimeError("memory not ready: record store / curator unavailable")

    sessions = curator._sessions
    db_path = knowledge.config.memory_db_path
    derivation_run_id = uuid4().hex

    # --- P0: backup ---------------------------------------------------------
    backup_path = f"{db_path}.init-bak-{now_iso().replace(':', '').replace('-', '')}"
    if db_path.exists():
        shutil.copy2(db_path, backup_path)
        _report_progress(f"backed up memory.db to {backup_path}")
        _prune_init_backups(db_path, keep=INIT_BACKUP_KEEP)
    else:
        backup_path = ""

    # --- P1: (optional wipe) + reset watermarks (FAIL-FAST) -----------------
    # Additive by default: keep existing records and let the bulk re-derivation
    # reconcile against them. A wipe is destructive and opt-in; if it raises we
    # abort BEFORE re-deriving (recoverable from backup_path). Watermarks are
    # always reset so the curator re-reads the FULL history of every transcript /
    # source rather than only new turns.
    wipe_result = {"deleted": 0, "kept_pinned": 0}
    if wipe:
        wipe_result = await record_store.wipe_except_pinned()
    await curator.reset_watermarks()
    if consolidate is not None:  # file-canonical build has no consolidate engine
        await consolidate.reset_watermark()
    if wipe:
        _report_progress(f"wiped {wipe_result['deleted']} records, kept {wipe_result['kept_pinned']} pinned")
    else:
        _report_progress("additive run — keeping existing records, re-reading full history")

    # --- P2: re-derive from ALL chat transcripts ----------------------------
    scopes = await sessions.recent_session_scopes(_SESSION_ENUMERATION_LIMIT)
    sessions_processed = 0
    admitted = 0
    llm_calls = 0
    capped = False
    for row in scopes:
        if row["session_type"] != "chat" or row["origin_automation_id"] is not None:
            continue
        if llm_calls >= max_llm_calls:
            capped = True
            break
        budget = max_llm_calls - llm_calls
        try:
            result = await curator.curate_session_fully(
                row["session_id"],
                area_id=row["area_id"],
                derivation_run_id=derivation_run_id,
                max_calls=budget,
                bulk=True,
            )
        except Exception:
            _logger.warning("init: session curation failed", session_id=row["session_id"], exc_info=True)
            continue
        sessions_processed += 1
        admitted += result["admitted"]
        llm_calls += result["calls"]
        if result["capped"]:
            capped = True
            break
    _report_progress(f"re-derived from {sessions_processed} session(s): {admitted} records admitted")

    # --- P3: consolidate (bounded loop) -------------------------------------
    consolidate_summary = {"merged": 0, "pruned": 0, "passes": 0}
    if consolidate is not None:  # file-canonical build: consolidation deferred
        for _ in range(_MAX_CONSOLIDATE_PASSES):
            report = await consolidate.run_once()
            consolidate_summary["merged"] += report.merged
            consolidate_summary["pruned"] += report.pruned
            consolidate_summary["passes"] += 1
            if report.pruned == 0 and report.merged == 0:
                break
    _report_progress(f"consolidated in {consolidate_summary['passes']} pass(es)")

    # --- P4: artifact projection (removed) ----------------------------------
    # File-canonical: the markdown pages ARE the source of truth. The curator
    # wrote each re-derived record straight to its page; exporting a projection
    # here would clobber those canonical pages.

    return {
        "wiped": wipe,
        "deleted": wipe_result["deleted"],
        "kept_pinned": wipe_result["kept_pinned"],
        "sessions_processed": sessions_processed,
        "admitted": admitted,
        "capped": capped,
        "backup_path": backup_path,
        "consolidate": consolidate_summary,
    }
