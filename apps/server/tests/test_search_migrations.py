import pytest

import arden.database as database
from arden.database import serialize_embedding
from arden.search.migrations import CURRENT_SCHEMA_VERSION
from arden.search.store import SearchStore


@pytest.mark.asyncio
async def test_search_migration_removes_legacy_notes_source(tmp_path):
    conn = await database.connect(tmp_path / "search.db", vec=True)
    store = SearchStore(conn, embedding_dim=3)
    await store.init_schema()

    await store.upsert("notes", "note-1", "Legacy note", "obsolete", serialize_embedding([1, 0, 0]))
    await store.upsert("memory", "fact-1", "Memory", "keep", serialize_embedding([0, 1, 0]))
    await conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES ('schema_version', '0')",
    )
    await conn.commit()

    await store.init_schema()

    assert await store.get_stats() == {"memory": 1}
    rows = await conn.execute_fetchall("SELECT value FROM meta WHERE key = 'schema_version'")
    assert rows[0]["value"] == str(CURRENT_SCHEMA_VERSION)
    await conn.close()
