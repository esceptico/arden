"""vec0 `source` partition key — a small partition (e.g. ~89 memory_line
vectors) must not be starved out of KNN by a huge one (~53k transcripts).

Pre-partition the query was a global top-k then a post-filter, so when many
transcript vectors sat on the query point the memory_line rows never made the
window and came back empty. The partition key filters INSIDE the KNN, so each
source is searched on its own. This test wedges 100 transcript vectors exactly
on the query point and asserts memory_line still returns its rows.
"""

import numpy as np
import pytest

import ntrp.database as database
from ntrp.search.store import SearchStore

pytestmark = pytest.mark.asyncio


def _vec(*xs) -> bytes:
    return database.serialize_embedding(np.array(xs, dtype=np.float32))


async def test_small_partition_not_starved_by_large_one(tmp_path):
    conn = await database.connect(tmp_path / "search.db", vec=True)
    try:
        store = SearchStore(conn, embedding_dim=4)
        await store.init_schema()
        assert store._has_vec, "partition-key vec0 CREATE failed"

        # 100 transcript vectors sitting exactly on the query point.
        for i in range(100):
            await store.upsert("transcript", f"t{i}", "t", "t", _vec(1, 0, 0, 0))
        # 2 memory_line vectors slightly off-query.
        await store.upsert("memory_line", "m1", "m", "gravel bike", _vec(0.9, 0.1, 0, 0))
        await store.upsert("memory_line", "m2", "m", "cat", _vec(0.8, 0.2, 0, 0))

        q = _vec(1, 0, 0, 0)
        mem = await store.vector_search(q, sources=["memory_line"], limit=10)
        mem_ids = {r[0] for r in mem}
        rows = await conn.execute_fetchall(
            f"SELECT id, source FROM items WHERE id IN ({','.join('?' * len(mem_ids))})",
            list(mem_ids),
        )
        # memory_line search returns ONLY memory_line rows, and isn't starved to 0.
        assert {r["source"] for r in rows} == {"memory_line"}
        assert len(mem) == 2

        # The big partition still searches fine on its own.
        assert len(await store.vector_search(q, sources=["transcript"], limit=10)) > 0
    finally:
        await conn.close()
