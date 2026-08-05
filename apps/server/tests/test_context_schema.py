"""Context schema startup contract."""

import json
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite
import pytest

import arden.context.schema as context_schema
import arden.database as database
from arden.context.models import SessionState
from arden.context.schema import SessionSchemaError, read_context_schema_manifest
from arden.context.store import SessionDataCorruptionError, SessionStore


async def _store(tmp_path: Path) -> tuple[aiosqlite.Connection, aiosqlite.Connection, SessionStore]:
    conn = await database.connect(tmp_path / "sessions.db")
    read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
    return conn, read_conn, SessionStore(conn, read_conn)


async def _downgrade_to_v5(conn: aiosqlite.Connection) -> None:
    await conn.execute("PRAGMA foreign_keys=OFF")
    await conn.executescript(
        """
        DROP INDEX idx_areas_active_name_key;
        DROP INDEX idx_areas_archived_updated;
        DROP INDEX idx_areas_page_id;
        DROP INDEX idx_areas_page_path;
        ALTER TABLE areas RENAME TO areas_v9;
        CREATE TABLE areas (
            area_id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            default_cwds TEXT NOT NULL DEFAULT '[]',
            instructions TEXT,
            knowledge_scope TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            archived_at TEXT,
            page_path TEXT,
            autonomy TEXT,
            attention TEXT,
            interrupts TEXT,
            paused_at TEXT,
            name_key TEXT,
            page_id TEXT
        );
        INSERT INTO areas (
            area_id, name, default_cwds, instructions, knowledge_scope,
            created_at, updated_at, archived_at, page_path, autonomy,
            attention, interrupts, paused_at, name_key, page_id
        )
        SELECT
            area_id, name, default_cwds, instructions, 'retired-scope',
            created_at, updated_at, archived_at, page_path, autonomy,
            attention, interrupts, paused_at, name_key, page_id
        FROM areas_v9;
        DROP TABLE areas_v9;
        CREATE INDEX idx_areas_archived_updated ON areas(archived_at, updated_at DESC);
        CREATE UNIQUE INDEX idx_areas_active_name_key ON areas(name_key) WHERE archived_at IS NULL;
        CREATE UNIQUE INDEX idx_areas_page_path ON areas(page_path) WHERE page_path IS NOT NULL;
        CREATE UNIQUE INDEX idx_areas_page_id ON areas(page_id) WHERE page_id IS NOT NULL;

        DROP INDEX idx_sessions_activity;
        DROP INDEX idx_sessions_archived;
        DROP INDEX idx_sessions_area_activity;
        DROP INDEX idx_sessions_parent_activity;
        ALTER TABLE sessions RENAME TO sessions_v9;
        CREATE TABLE sessions (
            session_id TEXT PRIMARY KEY,
            started_at TEXT NOT NULL,
            last_activity TEXT NOT NULL,
            messages TEXT,
            metadata TEXT,
            name TEXT,
            archived_at TEXT,
            session_type TEXT NOT NULL DEFAULT 'chat',
            origin_automation_id TEXT,
            area_id TEXT REFERENCES areas(area_id) ON DELETE SET NULL,
            chat_model TEXT,
            parent_session_id TEXT,
            parent_tool_call_id TEXT,
            agent_type TEXT,
            agent_status TEXT,
            slice_key TEXT,
            storage_state TEXT NOT NULL DEFAULT 'hot' CHECK(storage_state IN ('hot', 'cold')),
            cold_bundle_path TEXT,
            cold_bundle_sha256 TEXT,
            cold_bundle_bytes INTEGER,
            cold_logical_bytes INTEGER,
            cold_message_count INTEGER,
            cold_prose_sha256 TEXT,
            cold_blob_hashes_json TEXT,
            last_accessed_at TEXT,
            context_generation INTEGER NOT NULL DEFAULT 0
        );
        INSERT INTO sessions (
            session_id, started_at, last_activity, messages, metadata, name,
            archived_at, session_type, origin_automation_id, area_id, chat_model,
            parent_session_id, parent_tool_call_id, agent_type, agent_status, slice_key, storage_state,
            cold_bundle_path, cold_bundle_sha256, cold_bundle_bytes, cold_logical_bytes,
            cold_message_count, cold_prose_sha256, cold_blob_hashes_json, last_accessed_at, context_generation
        )
        SELECT
            session_id, started_at, last_activity, messages, metadata, name,
            archived_at, session_type, origin_automation_id, area_id, chat_model,
            parent_session_id, parent_tool_call_id, agent_type, agent_status, NULL, storage_state,
            cold_bundle_path, cold_bundle_sha256, cold_bundle_bytes, cold_logical_bytes,
            cold_message_count, cold_prose_sha256, cold_blob_hashes_json, last_accessed_at, context_generation
        FROM sessions_v9;
        DROP TABLE sessions_v9;
        CREATE INDEX idx_sessions_activity ON sessions(last_activity);
        CREATE INDEX idx_sessions_archived ON sessions(archived_at);
        CREATE INDEX idx_sessions_area_activity ON sessions(area_id, last_activity DESC);
        CREATE INDEX idx_sessions_parent_activity ON sessions(parent_session_id, started_at);

        DROP INDEX idx_background_agent_runs_session_agent_ref;
        DROP INDEX idx_background_agent_runs_session_status;
        ALTER TABLE background_agent_runs RENAME TO background_agent_runs_v9;
        CREATE TABLE background_agent_runs (
            task_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            parent_run_id TEXT,
            status TEXT NOT NULL,
            command TEXT NOT NULL,
            detail TEXT,
            result_ref TEXT,
            result_text TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            updated_at TEXT NOT NULL,
            ended_at TEXT,
            cancel_requested_at TEXT,
            notified_at TEXT,
            parent_tool_call_id TEXT,
            agent_type TEXT NOT NULL DEFAULT 'background_research',
            wait INTEGER NOT NULL DEFAULT 0,
            child_session_id TEXT,
            completion_id TEXT,
            spawn_spec TEXT,
            spawn_attempts INTEGER NOT NULL DEFAULT 0,
            suspension_id TEXT,
            cancel_actor TEXT,
            terminal_cause TEXT,
            cancel_generation INTEGER NOT NULL DEFAULT 0,
            cancel_idempotency_key TEXT,
            PRIMARY KEY (session_id, task_id)
        );
        INSERT INTO background_agent_runs (
            task_id, session_id, parent_run_id, status, command, detail, result_ref,
            result_text, created_at, started_at, updated_at, ended_at, cancel_requested_at,
            notified_at, parent_tool_call_id, agent_type, wait, child_session_id,
            completion_id, spawn_spec, spawn_attempts, suspension_id, cancel_actor,
            terminal_cause, cancel_generation, cancel_idempotency_key
        )
        SELECT
            task_id, session_id, parent_run_id, status, command, detail, result_ref,
            result_text, created_at, started_at, updated_at, ended_at, cancel_requested_at,
            notified_at, parent_tool_call_id, agent_type, wait, child_session_id,
            completion_id, spawn_spec, spawn_attempts, suspension_id, cancel_actor,
            terminal_cause, cancel_generation, cancel_idempotency_key
        FROM background_agent_runs_v9;
        DROP TABLE background_agent_runs_v9;
        CREATE INDEX idx_background_agent_runs_session_status
            ON background_agent_runs(session_id, status);

        DROP INDEX idx_chat_compactions_session_boundary;
        ALTER TABLE chat_compactions RENAME TO chat_compactions_v9;
        CREATE TABLE chat_compactions (
            compaction_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            boundary_seq INTEGER NOT NULL,
            messages_before INTEGER NOT NULL,
            messages_after INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            rehydration_state TEXT
        );
        INSERT INTO chat_compactions
        SELECT compaction_id, session_id, boundary_seq, messages_before, messages_after,
               created_at, rehydration_state
        FROM chat_compactions_v9;
        DROP TABLE chat_compactions_v9;
        CREATE INDEX idx_chat_compactions_session_boundary
            ON chat_compactions(session_id, boundary_seq);

        DROP INDEX idx_tool_calls_run;
        DROP INDEX idx_tool_calls_session_started;
        ALTER TABLE tool_calls RENAME TO tool_calls_v9;
        CREATE TABLE tool_calls (
            run_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            tool_call_id TEXT NOT NULL,
            tool_name TEXT NOT NULL,
            action TEXT NOT NULL,
            scope TEXT NOT NULL,
            args_hash TEXT,
            status TEXT NOT NULL,
            result_preview TEXT,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            result_ref TEXT,
            outcome_json TEXT,
            PRIMARY KEY (run_id, tool_call_id)
        );
        INSERT INTO tool_calls
        SELECT run_id, session_id, tool_call_id, tool_name, action, scope, args_hash,
               status, result_preview, started_at, ended_at, result_ref, outcome_json
        FROM tool_calls_v9;
        DROP TABLE tool_calls_v9;
        CREATE INDEX idx_tool_calls_run ON tool_calls(run_id);
        CREATE INDEX idx_tool_calls_session_started ON tool_calls(session_id, started_at);

        CREATE TABLE tool_results_legacy (
            content_hash TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            byte_len INTEGER NOT NULL,
            created_at TEXT NOT NULL
        );
        INSERT INTO tool_results_legacy VALUES ('legacy-hash', 'retired', 7, '2026-01-01T00:00:00+00:00');

        CREATE TABLE chat_queued_messages (
            client_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            status TEXT NOT NULL,
            message_json TEXT NOT NULL,
            enqueued_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            ingested_at TEXT,
            enqueued_seq INTEGER,
            ingested_seq INTEGER
        );
        CREATE INDEX idx_chat_queued_messages_session_status
            ON chat_queued_messages(session_id, status);
        CREATE INDEX idx_chat_queued_messages_run_status
            ON chat_queued_messages(run_id, status);

        UPDATE session_store_meta SET value = '5' WHERE key = 'schema_version';
        """
    )
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.commit()


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

        assert [row["value"] for row in version] == ["9"]
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
async def test_v5_upgrade_backfills_missing_transcript_atomically(tmp_path: Path):
    conn, read_conn, store = await _store(tmp_path)
    try:
        await store.init_schema()
        state = SessionState(session_id="v5", started_at=datetime.now(UTC))
        await store.save_session(state, [{"role": "user", "content": "kept"}])
        await read_conn.close()
        await conn.execute("DELETE FROM session_messages WHERE session_id = 'v5'")
        await conn.execute("DELETE FROM session_turns WHERE session_id = 'v5'")
        await conn.commit()
        await _downgrade_to_v5(conn)
        read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
        store.read_conn = read_conn

        await store.init_schema()

        row = (await conn.execute_fetchall(
            "SELECT active_message_count FROM sessions WHERE session_id = 'v5'"
        ))[0]
        transcript = await conn.execute_fetchall("SELECT message_id FROM session_messages WHERE session_id = 'v5'")
        assert row["active_message_count"] == 1
        assert len(transcript) == 1
        version = await conn.execute_fetchall("SELECT value FROM session_store_meta")
        assert [item["value"] for item in version] == ["9"]
    finally:
        await read_conn.close()
        await conn.close()


@pytest.mark.asyncio
async def test_real_v5_upgrade_matches_fresh_topology_and_retires_legacy(tmp_path: Path):
    conn, read_conn, store = await _store(tmp_path)
    try:
        await store.init_schema()
        fresh = await read_context_schema_manifest(conn)
        await read_conn.close()
        await _downgrade_to_v5(conn)

        session_columns = [row["name"] for row in await conn.execute_fetchall("PRAGMA table_info(sessions)")]
        assert session_columns == [
            "session_id", "started_at", "last_activity", "messages", "metadata", "name", "archived_at",
            "session_type", "origin_automation_id", "area_id", "chat_model", "parent_session_id",
            "parent_tool_call_id", "agent_type", "agent_status", "slice_key", "storage_state",
            "cold_bundle_path", "cold_bundle_sha256", "cold_bundle_bytes", "cold_logical_bytes",
            "cold_message_count", "cold_prose_sha256", "cold_blob_hashes_json", "last_accessed_at",
            "context_generation",
        ]
        assert await conn.execute_fetchall(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'tool_results_legacy'"
        )

        read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
        store.read_conn = read_conn
        await store.init_schema()

        assert await read_context_schema_manifest(conn) == fresh
        assert not await conn.execute_fetchall("SELECT 1 FROM pragma_table_info('sessions') WHERE name = 'slice_key'")
        assert not await conn.execute_fetchall(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name LIKE 'tool_results_legacy%'"
        )
    finally:
        await read_conn.close()
        await conn.close()


@pytest.mark.asyncio
async def test_v5_upgrade_rewrites_provider_receipts_and_repairs_partial_transcript(tmp_path: Path):
    conn, read_conn, store = await _store(tmp_path)
    try:
        await store.init_schema()
        legacy_call = {
            "id": "provider-1",
            "name": "code_execution",
            "arguments": json.dumps({"tools": ["alpha"]}),
            "result": "ok",
            "done": True,
            "provider_item": {"arguments": {"names": ["beta"]}},
        }
        messages = [
            {"role": "user", "content": "run"},
            {"role": "assistant", "content": "done", "provider_tool_calls": [legacy_call]},
        ]
        await store.save_session(SessionState(session_id="legacy-provider", started_at=datetime.now(UTC)), messages)
        await read_conn.close()
        second_id = messages[1]["message_id"]
        await conn.execute(
            "DELETE FROM session_messages WHERE session_id = 'legacy-provider' AND message_id = ?",
            (second_id,),
        )
        await conn.commit()
        await _downgrade_to_v5(conn)
        read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
        store.read_conn = read_conn

        await store.init_schema()

        projection = json.loads((await conn.execute_fetchall(
            "SELECT messages FROM sessions WHERE session_id = 'legacy-provider'"
        ))[0]["messages"])
        migrated_call = projection[1]["provider_tool_calls"][0]
        assert migrated_call["arguments"] == {"tools": ["alpha"]}
        assert migrated_call["loaded_tool_names"] == ["alpha", "beta"]
        assert "provider_item" not in migrated_call
        transcript = await conn.execute_fetchall(
            "SELECT message_json FROM session_messages WHERE session_id = 'legacy-provider' ORDER BY seq"
        )
        assert len(transcript) == 2
        assert json.loads(transcript[1]["message_json"])["provider_tool_calls"][0] == migrated_call
    finally:
        await read_conn.close()
        await conn.close()


@pytest.mark.asyncio
async def test_v5_cold_session_rejected_before_mutation(tmp_path: Path):
    conn, read_conn, store = await _store(tmp_path)
    try:
        await store.init_schema()
        await store.save_session(SessionState(session_id="cold", started_at=datetime.now(UTC)), [])
        await read_conn.close()
        await _downgrade_to_v5(conn)
        await conn.execute("UPDATE sessions SET storage_state = 'cold' WHERE session_id = 'cold'")
        await conn.commit()
        read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
        store.read_conn = read_conn

        with pytest.raises(SessionDataCorruptionError, match="restored from cold storage"):
            await store.init_schema()

        assert (await conn.execute_fetchall("SELECT value FROM session_store_meta"))[0]["value"] == "5"
        assert "public_ref" not in {row["name"] for row in await conn.execute_fetchall("PRAGMA table_info(sessions)")}
        assert await conn.execute_fetchall(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'tool_results_legacy'"
        )
    finally:
        await read_conn.close()
        await conn.close()


@pytest.mark.asyncio
async def test_v5_postcondition_failure_rolls_back_ddl(tmp_path: Path, monkeypatch):
    conn, read_conn, store = await _store(tmp_path)
    try:
        await store.init_schema()
        await read_conn.close()
        await _downgrade_to_v5(conn)
        read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
        store.read_conn = read_conn

        async def reject_postcondition(_conn):
            raise RuntimeError("injected postcondition failure")

        monkeypatch.setattr(context_schema, "_assert_v9_shape", reject_postcondition)
        with pytest.raises(RuntimeError, match="injected postcondition"):
            await store.init_schema()

        assert (await conn.execute_fetchall("SELECT value FROM session_store_meta"))[0]["value"] == "5"
        columns = {row["name"] for row in await conn.execute_fetchall("PRAGMA table_info(sessions)")}
        assert "slice_key" in columns and "public_ref" not in columns
        assert await conn.execute_fetchall(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'tool_results_legacy'"
        )
        assert int((await conn.execute_fetchall("PRAGMA foreign_keys"))[0][0]) == 1
    finally:
        await read_conn.close()
        await conn.close()


@pytest.mark.asyncio
async def test_v5_corruption_rolls_back_before_schema_mutation(tmp_path: Path):
    conn, read_conn, store = await _store(tmp_path)
    try:
        await store.init_schema()
        await store.save_session(SessionState(session_id="broken", started_at=datetime.now(UTC)), [])
        await read_conn.close()
        await conn.execute("UPDATE sessions SET messages = '{' WHERE session_id = 'broken'")
        await conn.commit()
        await _downgrade_to_v5(conn)
        read_conn = await database.connect(tmp_path / "sessions.db", readonly=True)
        store.read_conn = read_conn

        with pytest.raises(SessionDataCorruptionError, match="messages projection"):
            await store.init_schema()

        columns = {row["name"] for row in await conn.execute_fetchall("PRAGMA table_info(sessions)")}
        assert "active_message_count" not in columns
        assert "public_ref" not in columns
        version = await conn.execute_fetchall("SELECT value FROM session_store_meta")
        assert [item["value"] for item in version] == ["5"]
    finally:
        await read_conn.close()
        await conn.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["4", "10"])
async def test_unsupported_schema_versions_fail_without_mutation(tmp_path: Path, version: str):
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
