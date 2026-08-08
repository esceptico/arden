"""Context schema startup contract."""

from pathlib import Path

import aiosqlite
import pytest

import arden.database as database
from arden.context.schema import SessionSchemaError
from arden.context.store import SessionStore


async def _store(tmp_path: Path) -> tuple[aiosqlite.Connection, aiosqlite.Connection, SessionStore]:
    conn = await database.connect(tmp_path / "sessions.db")
    read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
    return conn, read_conn, SessionStore(conn, read_conn, event_conn=conn)


@pytest.mark.asyncio
async def test_fresh_context_schema_is_canonical(tmp_path: Path):
    conn, read_conn, store = await _store(tmp_path)
    try:
        await store.init_schema()

        version = await conn.execute_fetchall("SELECT value FROM session_store_meta")
        area_columns = {row["name"] for row in await conn.execute_fetchall("PRAGMA table_info(areas)")}
        triggers = await conn.execute_fetchall(
            "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'session_messages_a_%'"
        )

        assert [row["value"] for row in version] == ["10"]
        assert {"attention", "interrupts", "paused_at"} <= area_columns
        assert {row["name"] for row in triggers} == {
            "session_messages_ai",
            "session_messages_ad",
            "session_messages_au",
        }
    finally:
        await read_conn.close()
        await conn.close()


@pytest.mark.asyncio
async def test_current_schema_startup_never_runs_ddl(tmp_path: Path, monkeypatch):
    conn, read_conn, store = await _store(tmp_path)
    try:
        await store.init_schema()

        async def unexpected(*_args, **_kwargs):
            raise AssertionError("healthy schema startup must not execute DDL")

        monkeypatch.setattr(conn, "execute", unexpected)
        monkeypatch.setattr(conn, "executescript", unexpected)

        await store.init_schema()
    finally:
        await read_conn.close()
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["4", "5", "11"])
async def test_noncurrent_schema_versions_fail_without_mutation(tmp_path: Path, version: str):
    conn, read_conn, store = await _store(tmp_path)
    try:
        await store.init_schema()
        await read_conn.close()
        await conn.execute("UPDATE session_store_meta SET value = ?", (version,))
        await conn.commit()
        read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
        store.read_conn = read_conn

        with pytest.raises(SessionSchemaError, match=r"unsupported|newer"):
            await store.init_schema()

        stored = await conn.execute_fetchall("SELECT value FROM session_store_meta")
        assert [item["value"] for item in stored] == [version]
    finally:
        await read_conn.close()
        await conn.close()


@pytest.mark.asyncio
async def test_partial_schema_fails_without_repair(tmp_path: Path):
    conn, read_conn, store = await _store(tmp_path)
    try:
        await store.init_schema()
        await read_conn.close()
        await conn.execute("DROP TRIGGER session_messages_ai")
        await conn.commit()
        read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
        store.read_conn = read_conn

        with pytest.raises(SessionSchemaError, match="FTS triggers"):
            await store.init_schema()

        assert not await conn.execute_fetchall(
            "SELECT 1 FROM sqlite_master WHERE type = 'trigger' AND name = 'session_messages_ai'"
        )
    finally:
        await read_conn.close()
        await conn.close()
