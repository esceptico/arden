"""Run ONE visible dream pass over the live corpus — the user-facing proof of
the recursive memory. Samples neighborhoods from the ground records, runs the
dreamer's derivation phase (question-first -> derive -> verify -> commit), and
prints every committed inference with its premises, mode, and question.

Safe to run with the server up (additive writes; per-op commits). Uses the real
memory model + vector index. Bounded: --hoods neighborhoods, the consolidate
module's per-sweep derivation budget applies per chunk.

    uv run python -m scripts.run_dream [--hoods 12]
"""

import argparse
import asyncio
import random

from arden.config import get_config
from arden.llm.models import get_models
from arden.llm.router import get_completion_client
from arden.memory.consolidate import Consolidate, ConsolidateReport
from arden.memory.records import RecordStore
from arden.server.indexer import Indexer


def _effort(config, model_id):
    if not model_id:
        return None
    if (configured := config.reasoning_effort_for(model_id)):
        return configured
    efforts = get_models()[model_id].reasoning_efforts
    return ("low" if "low" in efforts else efforts[0]) if efforts else None


async def main(hoods_target: int) -> None:
    config = get_config()
    if not config.memory_model:
        print("no memory_model configured")
        return
    indexer = Indexer(db_path=config.search_db_path, embedding=config.embedding) if config.embedding else None
    if indexer:
        await indexer.connect()
    llm = get_completion_client(config.memory_model)
    store = RecordStore(db_path=config.memory_db_path, search_index=indexer.index if indexer else None)
    consolidate = Consolidate(
        store, llm, model=config.memory_model, db_path=config.memory_db_path,
        reasoning_effort=_effort(config, config.memory_model),
    )
    try:
        pool = await store.list(limit=1000)
        seeds = random.sample(pool, min(hoods_target, len(pool)))
        print(f"corpus: {len(pool)} active records | dreaming over {len(seeds)} neighborhoods "
              f"(model: {config.memory_model})\n", flush=True)

        before = {r.id for r in await store.derived_records(limit=1000)}
        total = ConsolidateReport()
        for i, seed in enumerate(seeds, 1):
            hood = [seed, *await store.neighborhood(seed, limit=7)]
            if len(hood) < 2:
                continue
            print(f"--- hood {i}/{len(seeds)} around: {seed.text[:90]!r}", flush=True)
            report = ConsolidateReport()
            await consolidate._dream([hood], report)
            for field in ("derived", "corroborated"):
                setattr(total, field, getattr(total, field) + getattr(report, field))

            for rec in await store.derived_records(limit=50):
                if rec.id in before:
                    continue
                before.add(rec.id)
                just = (await store.justifications_of(rec.id))[0]
                print(f"  DREAMED [{just.mode}] {rec.text}")
                print(f"    question: {just.question}")
                for pid in just.premise_ids:
                    premise = await store.get(pid)
                    print(f"    because:  {premise.text[:100]}")
            if report.corroborated:
                print(f"  (+{report.corroborated} conclusion(s) corroborated an existing record)")

        print(f"\nTOTAL: {total.derived} new inferences, {total.corroborated} corroborations", flush=True)
        derived_now = await store.derived_records(limit=1000)
        print(f"derived records in memory: {len(derived_now)}")
    finally:
        await consolidate.close()
        await store.close()
        if indexer:
            await indexer.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--hoods", type=int, default=12)
    args = parser.parse_args()
    asyncio.run(main(args.hoods))
