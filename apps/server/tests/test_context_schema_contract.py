"""Atomic creation and exact current-schema validation."""

from pathlib import Path

import aiosqlite
import pytest

import arden.context.schema as context_schema
import arden.database as database
from arden.context.schema import SessionSchemaError
from arden.context.store import SessionStore


async def _store(tmp_path: Path) -> tuple[aiosqlite.Connection, aiosqlite.Connection, SessionStore]:
    conn = await database.connect(tmp_path / "sessions.db")
    read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
    return conn, read_conn, SessionStore(conn, read_conn)


@pytest.mark.asyncio
async def test_fresh_schema_failure_rolls_back_every_object(tmp_path: Path, monkeypatch):
    conn, read_conn, store = await _store(tmp_path)
    try:
        monkeypatch.setattr(
            context_schema,
            "CANONICAL_SCHEMA_SQL",
            context_schema.CANONICAL_SCHEMA_SQL + "\nBROKEN STATEMENT;",
        )
        with pytest.raises(aiosqlite.OperationalError):
            await store.init_schema()

        objects = await conn.execute_fetchall(
            "SELECT name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"
        )
        assert objects == []
    finally:
        await read_conn.close()
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mutation",
    [
        "DROP INDEX idx_sessions_activity; CREATE INDEX idx_sessions_activity ON sessions(name);",
        "CREATE INDEX stray_context_index ON sessions(name);",
    ],
)
async def test_current_schema_rejects_noncanonical_indexes_without_repair(tmp_path: Path, mutation: str):
    conn, read_conn, store = await _store(tmp_path)
    try:
        await store.init_schema()
        await conn.executescript(mutation)
        await conn.commit()

        with pytest.raises(SessionSchemaError, match=r"named indexes|non-canonical"):
            await store.init_schema()
    finally:
        await read_conn.close()
        await conn.close()
