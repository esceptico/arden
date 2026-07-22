"""One-shot CONSOLIDATE/LINT over the live record pool.

Runs the same O(delta) consolidation pass the Dreamer runs (merge duplicates,
supersede stale, drop orphans) over EVERY active record in ~/.arden/memory.db,
collapsing the seeded near-duplicate pile into a clean current set. Reports
before/after active-record counts and a few merge examples.

The pass is the production `Consolidate` engine — every decision is the memory
LLM judging an FTS/vector-recalled neighborhood (no wordlist/regex heuristics).
The watermark is reset first so the whole pool is swept once; consolidation is
idempotent, so a re-run is cheap.

    uv run python -m scripts.consolidate_records          # dry-run report only
    uv run python -m scripts.consolidate_records --apply  # actually consolidate
"""

import argparse
import asyncio

from arden.config import get_config
from arden.llm import router
from arden.llm.router import get_completion_client
from arden.memory.consolidate import WATERMARK_KEY, Consolidate
from arden.memory.records import RecordStore


async def _active_count(records: RecordStore) -> int:
    conn = await records._ensure_conn()
    rows = await conn.execute_fetchall(
        "SELECT COUNT(*) AS n FROM records WHERE superseded_by IS NULL"
    )
    return rows[0]["n"]


async def _superseded_examples(records: RecordStore, limit: int = 8) -> list[tuple[str, str]]:
    """(loser_text -> survivor_text) pairs produced by this run."""
    conn = await records._ensure_conn()
    rows = await conn.execute_fetchall(
        "SELECT l.text AS loser, s.text AS survivor "
        "FROM records l JOIN records s ON l.superseded_by = s.id "
        "WHERE l.superseded_by IS NOT NULL "
        "ORDER BY l.last_confirmed_at DESC LIMIT ?",
        (limit,),
    )
    return [(r["loser"], r["survivor"]) for r in rows]


async def main(apply: bool) -> None:
    config = get_config()
    router.init(config)

    if not config.memory_model:
        raise SystemExit("no memory_model configured — set one in ~/.arden/settings.json")

    llm = get_completion_client(config.memory_model)
    records = RecordStore(db_path=config.memory_db_path, search_index=None)  # FTS-only recall
    consolidate = Consolidate(
        records,
        llm,
        model=config.memory_model,
        db_path=config.memory_db_path,
    )

    before = await _active_count(records)
    print(f"DB:           {config.memory_db_path}")
    print(f"Model:        {config.memory_model}")
    print(f"Active before: {before}")

    if not apply:
        print("\n(dry-run — pass --apply to consolidate)")
        await consolidate.close()
        await records.close()
        return

    # Reset the watermark so the WHOLE active pool is swept this run, then sweep
    # to exhaustion (each sweep is capped at MAX_ITEMS_PER_SWEEP).
    conn = await consolidate._ensure_conn()
    await conn.execute("DELETE FROM meta WHERE key = ?", (WATERMARK_KEY,))
    await conn.commit()

    total_merged = total_superseded = total_dropped = 0
    sweep = 0
    while True:
        sweep += 1
        report = await consolidate.run_once()
        total_merged += report.merged
        total_superseded += report.superseded
        total_dropped += report.dropped
        active = await _active_count(records)
        print(
            f"  sweep {sweep}: merged={report.merged} superseded={report.superseded} "
            f"dropped={report.dropped} -> active={active}"
        )
        # A sweep that resolved nothing AND advanced its watermark to the tail
        # means the pool is clean; stop. We detect this by the delta running dry:
        # run_once advanced to sweep_start when delta was empty, so a subsequent
        # sweep over a now-empty delta produces an all-zero report.
        if report.merged == 0 and report.superseded == 0 and report.dropped == 0:
            break
        if sweep > 50:
            print("  (stopping after 50 sweeps)")
            break

    after = await _active_count(records)
    print(f"\nActive after:  {after}   (collapsed {before - after})")
    print(f"Totals:        merged={total_merged} superseded={total_superseded} dropped={total_dropped}")

    examples = await _superseded_examples(records)
    if examples:
        print("\nMerged/superseded examples (loser -> survivor):")
        for loser, survivor in examples:
            print(f"  - {loser[:80]!r}\n      -> {survivor[:80]!r}")

    await consolidate.close()
    await records.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="actually consolidate (default: dry-run)")
    args = parser.parse_args()
    asyncio.run(main(args.apply))
