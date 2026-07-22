from pathlib import Path

import pytest

from arden.memory.records import RecordStore


@pytest.mark.anyio
async def test_record_store_schema_does_not_create_derivation_tables(tmp_path: Path):
    store = RecordStore(tmp_path / "memory.db")
    await store.add("plain fact", kind="fact")
    conn = await store._ensure_conn()
    rows = await conn.execute_fetchall("SELECT name FROM sqlite_master WHERE type = 'table'")
    names = {r["name"] for r in rows}
    assert "justifications" not in names
    assert "justification_premises" not in names
    assert "nogoods" not in names
