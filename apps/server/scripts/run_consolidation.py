"""Run the dreamer's consolidation over the WHOLE record corpus now, with the real
memory LLM + vector index — instead of waiting for the slow background sweep. Resets
the watermark so every active record is re-examined: integrate same-topic observations
into coherent typed facts/patterns (superseding the raws), retype standing observations,
merge dups, supersede stale, drop orphans. Run with the server stopped.

    uv run python -m scripts.run_consolidation
"""

import asyncio

from arden.config import get_config
from arden.llm.models import get_models
from arden.llm.router import get_completion_client
from arden.memory.consolidate import Consolidate
from arden.memory.records import RecordStore
from arden.server.indexer import Indexer


def _effort(config, model_id):
    if not model_id:
        return None
    if (configured := config.reasoning_effort_for(model_id)):
        return configured
    efforts = get_models()[model_id].reasoning_efforts
    return ("low" if "low" in efforts else efforts[0]) if efforts else None


async def _kinds(store: RecordStore) -> dict:
    conn = await store._ensure_conn()
    rows = await conn.execute_fetchall(
        "SELECT kind, count(*) AS c FROM records WHERE superseded_by IS NULL GROUP BY kind ORDER BY c DESC"
    )
    return {r["kind"]: r["c"] for r in rows}


async def main() -> None:
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
        print("BEFORE:", await _kinds(store), flush=True)
        conn = await consolidate._ensure_conn()
        await conn.execute(
            "INSERT INTO meta (key, value) VALUES ('consolidate_watermark', '2000-01-01T00:00:00+00:00') "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
        )
        await conn.commit()
        totals = {"merged": 0, "retyped": 0, "superseded": 0, "dropped": 0}
        for i in range(1, 12):
            rep = await consolidate.run_once()
            batch = {"merged": rep.merged, "retyped": rep.retyped,
                     "superseded": rep.superseded, "dropped": rep.dropped}
            for k in totals:
                totals[k] += batch[k]
            print(f"batch {i}: {batch}  | kinds now: {await _kinds(store)}", flush=True)
            if all(v == 0 for v in batch.values()):
                break
        print("TOTALS:", totals, flush=True)
        print("AFTER:", await _kinds(store), flush=True)
    finally:
        await consolidate.close()
        await store.close()
        if indexer:
            await indexer.close()


if __name__ == "__main__":
    asyncio.run(main())
