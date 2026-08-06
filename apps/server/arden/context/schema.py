"""Strict startup contract for the context SQLite database."""

from collections.abc import Awaitable, Callable

import aiosqlite

from arden.context.errors import SessionSchemaError
from arden.context.schema_manifest import assert_current_schema as _assert_current_shape
from arden.context.schema_manifest import (
    read_context_schema_manifest as _read_context_schema_manifest,
)
from arden.context.schema_sql import (
    CANONICAL_SCHEMA_SQL,
    CANONICAL_SQL_OBJECTS,
    normalize_schema_sql,
)
from arden.context.schema_v5 import migrate_v5_to_current

CURRENT_VERSION = 10
V5_UPGRADE_VERSION = 5
read_context_schema_manifest = _read_context_schema_manifest

StrictProjection = Callable[[str, object], list[dict]]
MetadataParser = Callable[[str, str | None], dict]
StampMessages = Callable[[list[dict], str], None]
MirrorMessages = Callable[..., Awaitable[None]]

_CURRENT_COLUMN_NAMES: dict[str, set[str]] = {
    "areas": {
        "area_id", "area_ref", "name", "name_key", "default_cwds",
        "instructions", "page_path", "page_id", "autonomy", "attention",
        "interrupts", "paused_at", "created_at", "updated_at", "archived_at",
    },
    "background_agent_events": {
        "session_id", "seq", "task_id", "status", "detail",
        "result_ref", "terminal", "created_at", "event_id", "delivered_at",
    },
    "background_agent_runs": {
        "task_id", "agent_ref", "session_id", "parent_run_id", "parent_tool_call_id",
        "suspension_id", "child_session_id", "agent_type", "wait", "status",
        "command", "detail", "result_ref", "result_text", "created_at",
        "started_at", "updated_at", "ended_at", "cancel_requested_at", "cancel_actor",
        "terminal_cause", "cancel_generation", "cancel_idempotency_key", "notified_at",
        "completion_id", "spawn_spec", "spawn_attempts",
    },
    "chat_compactions": {
        "compaction_id", "session_id", "boundary_seq", "messages_before", "messages_after",
        "rehydration_state", "created_at",
    },
    "chat_idempotency_keys": {
        "session_id", "client_id", "request_hash", "run_id", "message_id",
        "status", "created_at", "updated_at", "expires_at",
    },
    "chat_runs": {
        "run_id", "session_id", "status", "stop_reason", "started_at",
        "updated_at", "ended_at", "last_seq", "metadata_json", "error_code",
        "error_message", "client_id",
    },
    "execution_cancellation_scopes": {
        "session_id", "actor", "cause", "generation", "idempotency_key", "updated_at",
    },
    "run_sidecars": {
        "run_id", "session_id", "context_manifest_json", "source_refs_json", "evidence_json",
        "updated_at",
    },
    "session_event_retention_state": {"session_id", "writes_since_prune"},
    "session_events": {"session_id", "seq", "event_type", "event_json", "run_id", "created_at"},
    "session_goals": {
        "session_id", "goal_id", "objective", "status", "evidence_json",
        "blocked_reason", "token_budget", "tokens_used", "time_used_seconds", "created_at", "updated_at",
    },
    "session_messages": {
        "session_id", "message_id", "seq", "role", "message_json", "client_id", "created_at", "search_text",
    },
    "session_messages_fts": {"search_text"},
    "session_store_meta": {"key", "value"},
    "session_todo_overrides": {"session_id", "items_json", "explanation", "updated_at"},
    "session_todos": {"session_id", "items_json", "explanation", "updated_at"},
    "session_turns": {
        "session_id", "turn_id", "turn_index", "user_message_id", "message_start_id",
        "message_end_id", "message_start_seq", "message_end_seq", "started_at", "ended_at",
    },
    "sessions": {
        "session_id", "public_ref", "started_at", "last_activity", "last_accessed_at",
        "messages", "metadata", "name", "archived_at", "session_type", "origin_automation_id",
        "parent_session_id", "parent_tool_call_id", "agent_type", "agent_status", "area_id", "chat_model",
        "context_generation", "active_message_count", "storage_state", "cold_bundle_path",
        "cold_bundle_sha256", "cold_bundle_bytes", "cold_logical_bytes", "cold_message_count",
        "cold_prose_sha256", "cold_blob_hashes_json",
    },
    "storage_cleanup_runs": {
        "run_id", "plan_id", "started_at", "completed_at", "before_bytes", "target_bytes",
        "after_bytes", "reclaimed_bytes", "actions_json", "status", "error",
    },
    "tool_approvals": {
        "run_id", "session_id", "tool_call_id", "tool_name", "action", "scope", "preview", "diff",
        "status", "requested_at", "resolved_at", "expires_at", "result_feedback", "kind", "payload_json",
        "resolution_json",
    },
    "tool_calls": {
        "run_id", "session_id", "tool_call_id", "tool_name", "action", "scope", "args_hash", "status",
        "result_preview", "result_ref", "outcome_json", "started_at", "ended_at",
    },
    "tool_results": {
        "tool_result_id", "session_id", "run_id", "tool_call_id", "tool_name", "content_sha256",
        "content_bytes", "stored_bytes", "compression", "blob_ref", "blob_path", "preview",
        "retention_class", "expires_at", "source_event_seq", "created_at",
    },
}

_V5_ONLY_COLUMN_NAMES = {
    "chat_queued_messages": {
        "client_id", "session_id", "run_id", "status", "message_json",
        "enqueued_at", "updated_at", "ingested_at", "enqueued_seq", "ingested_seq",
    },
}

_V5_ONLY_INDEX_NAMES = {
    "idx_chat_queued_messages_session_status",
    "idx_chat_queued_messages_run_status",
}


async def initialize_context_schema(
    conn: aiosqlite.Connection,
    *,
    strict_active_projection: StrictProjection,
    parse_metadata: MetadataParser,
    stamp_messages: StampMessages,
    mirror_session_messages: MirrorMessages,
) -> None:
    """Create, verify, or make the one supported schema upgrade."""

    if await _is_fresh(conn):
        await _create_fresh(conn)
        return

    version = await _schema_version(conn)
    if version == CURRENT_VERSION:
        await _assert_current_shape(conn)
        return
    if version == V5_UPGRADE_VERSION:
        await _assert_v5_shape(conn)
        await migrate_v5_to_current(
            conn,
            strict_active_projection=strict_active_projection,
            parse_metadata=parse_metadata,
            stamp_messages=stamp_messages,
            mirror_session_messages=mirror_session_messages,
            validate_schema=lambda: _assert_current_shape(conn),
        )
        return
    if version > CURRENT_VERSION:
        raise SessionSchemaError(f"context schema v{version} is newer than this server")
    raise SessionSchemaError(
        f"context schema v{version} is unsupported; only fresh, v5, and v{CURRENT_VERSION} databases are accepted"
    )


async def _is_fresh(conn: aiosqlite.Connection) -> bool:
    rows = await conn.execute_fetchall(
        "SELECT 1 FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' LIMIT 1"
    )
    return not rows


async def _create_fresh(conn: aiosqlite.Connection) -> None:
    await conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
    mode = await conn.execute_fetchall("PRAGMA auto_vacuum")
    if int(mode[0][0]) != 2:
        await conn.execute("VACUUM")
    try:
        await conn.executescript(f"BEGIN IMMEDIATE;\n{CANONICAL_SCHEMA_SQL}")
        await _assert_current_shape(conn)
        await conn.commit()
    except BaseException:
        await conn.rollback()
        raise


async def _schema_version(conn: aiosqlite.Connection) -> int:
    meta = await conn.execute_fetchall(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'session_store_meta'"
    )
    if not meta:
        raise SessionSchemaError("context database is partial: session_store_meta is missing")

    rows = await conn.execute_fetchall("SELECT key, value FROM session_store_meta ORDER BY key")
    if len(rows) != 1 or rows[0]["key"] != "schema_version":
        raise SessionSchemaError("context schema metadata must contain exactly one schema_version row")
    raw_version = rows[0]["value"]
    try:
        version = int(raw_version)
    except (TypeError, ValueError) as exc:
        raise SessionSchemaError("persisted context schema version is invalid") from exc
    if str(version) != raw_version:
        raise SessionSchemaError("persisted context schema version must be a canonical integer")
    return version


async def _assert_v5_shape(conn: aiosqlite.Connection) -> None:
    expected = {name: set(columns) for name, columns in _CURRENT_COLUMN_NAMES.items()}
    expected.update(_V5_ONLY_COLUMN_NAMES)
    expected["sessions"] -= {"active_message_count", "public_ref"}
    expected["sessions"].add("slice_key")
    expected["background_agent_runs"].remove("agent_ref")
    expected["areas"].remove("area_ref")
    expected["areas"].add("knowledge_scope")

    for table, expected_columns in expected.items():
        rows = await conn.execute_fetchall(f"PRAGMA table_info({table})")
        actual_columns = {row["name"] for row in rows}
        if actual_columns != expected_columns:
            raise SessionSchemaError(
                f"context schema v5 has invalid {table} columns: "
                f"expected {sorted(expected_columns)}, got {sorted(actual_columns)}"
            )

    legacy_rows = await conn.execute_fetchall("PRAGMA table_info(tool_results_legacy)")
    legacy_signature = tuple(
        (row["name"], row["type"], bool(row["notnull"]), row["dflt_value"], int(row["pk"]))
        for row in legacy_rows
    )
    expected_legacy = (
        ("content_hash", "TEXT", False, None, 1),
        ("content", "TEXT", True, None, 0),
        ("byte_len", "INTEGER", True, None, 0),
        ("created_at", "TEXT", True, None, 0),
    )
    if legacy_signature != expected_legacy:
        raise SessionSchemaError("context schema v5 has an invalid tool_results_legacy table")

    indexes = await conn.execute_fetchall(
        "SELECT name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_autoindex%'"
    )
    actual_indexes = {row["name"] for row in indexes}
    required_indexes = {
        item.name
        for item in CANONICAL_SQL_OBJECTS
        if item.kind == "index" and item.name != "idx_background_agent_runs_session_agent_ref"
    }
    missing_indexes = (required_indexes | _V5_ONLY_INDEX_NAMES) - actual_indexes
    if missing_indexes:
        raise SessionSchemaError(f"context schema v5 is missing indexes: {sorted(missing_indexes)}")

    trigger_rows = await conn.execute_fetchall(
        "SELECT name, tbl_name, sql FROM sqlite_master WHERE type = 'trigger' AND tbl_name = 'session_messages'"
    )
    expected_triggers = {
        item.name: (item.table, normalize_schema_sql(item.sql))
        for item in CANONICAL_SQL_OBJECTS
        if item.kind == "trigger"
    }
    actual_triggers = {
        row["name"]: (row["tbl_name"], normalize_schema_sql(row["sql"] or ""))
        for row in trigger_rows
    }
    if actual_triggers != expected_triggers:
        raise SessionSchemaError("context schema v5 has invalid session-message FTS triggers")

    legacy_names = await conn.execute_fetchall(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND "
        "(name GLOB '*_old*' OR name LIKE 'tool_results_legacy%')"
    )
    if {row["name"] for row in legacy_names} != {"tool_results_legacy"}:
        raise SessionSchemaError("context schema v5 has unexpected legacy tables")
