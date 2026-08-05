"""The single supported context-schema upgrade."""

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import aiosqlite

from arden.context.errors import SessionDataCorruptionError
from arden.context.schema_sql import CANONICAL_SQL_OBJECTS, CANONICAL_TABLE_SQL
from arden.core.public_refs import is_public_ref, public_ref

StrictProjection = Callable[[str, object], list[dict]]
MetadataParser = Callable[[str, str | None], dict]
StampMessages = Callable[[list[dict], str], None]
MirrorMessages = Callable[..., Awaitable[None]]
SchemaValidator = Callable[[], Awaitable[None]]

_REBUILT_TABLES = (
    "areas",
    "sessions",
    "background_agent_runs",
    "chat_compactions",
    "tool_calls",
)


def _reject_nonfinite_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value}")


def _session_ref_text(name: object, session_type: object) -> tuple[str, str]:
    if not isinstance(session_type, str) or session_type not in {"chat", "channel", "agent"}:
        raise SessionDataCorruptionError(f"invalid persisted session type: {session_type!r}")
    kind = session_type
    title = name.strip() if isinstance(name, str) and name.strip() else kind
    return title, kind


async def _backfill_public_refs(conn: aiosqlite.Connection) -> None:
    await conn.execute("ALTER TABLE areas ADD COLUMN area_ref TEXT")
    area_refs: set[str] = set()
    for row in await conn.execute_fetchall("SELECT area_id, name FROM areas"):
        area_ref = public_ref(row["name"], row["area_id"], empty_slug="area")
        if area_ref in area_refs:
            raise RuntimeError(f"area public ref collision: {area_ref}")
        area_refs.add(area_ref)
        await conn.execute("UPDATE areas SET area_ref = ? WHERE area_id = ?", (area_ref, row["area_id"]))

    await conn.execute("ALTER TABLE sessions ADD COLUMN public_ref TEXT")
    await conn.execute("ALTER TABLE background_agent_runs ADD COLUMN agent_ref TEXT")

    used_agent_refs: set[tuple[str, str]] = set()
    runs = await conn.execute_fetchall(
        "SELECT session_id, task_id, command, child_session_id FROM background_agent_runs"
    )
    for row in runs:
        task_id = str(row["task_id"])
        agent_ref = public_ref(
            str(row["command"] or "agent").strip() or "agent",
            f"{row['session_id']}:{task_id}",
            empty_slug="agent",
        )
        ref_key = (str(row["session_id"]), agent_ref)
        if ref_key in used_agent_refs:
            raise RuntimeError(f"background agent public ref collision: {agent_ref}")
        used_agent_refs.add(ref_key)
        await conn.execute(
            "UPDATE background_agent_runs SET agent_ref = ? WHERE session_id = ? AND task_id = ?",
            (agent_ref, row["session_id"], task_id),
        )
        child_session_id = row["child_session_id"]
        if child_session_id:
            child = await conn.execute_fetchall(
                "SELECT public_ref FROM sessions WHERE session_id = ?",
                (child_session_id,),
            )
            if not child:
                raise RuntimeError(f"background agent {task_id!r} owns a missing child session")
            if child[0]["public_ref"] is not None and child[0]["public_ref"] != agent_ref:
                raise RuntimeError(f"child session {child_session_id!r} conflicts with its agent ref")
            await conn.execute(
                "UPDATE sessions SET public_ref = ? WHERE session_id = ?",
                (agent_ref, child_session_id),
            )

    used_session_refs: set[str] = set()
    sessions = await conn.execute_fetchall("SELECT session_id, public_ref, name, session_type FROM sessions")
    for row in sessions:
        session_ref = row["public_ref"]
        if session_ref is None:
            title, kind = _session_ref_text(row["name"], row["session_type"])
            session_ref = public_ref(title, row["session_id"], empty_slug=kind)
            await conn.execute(
                "UPDATE sessions SET public_ref = ? WHERE session_id = ?",
                (session_ref, row["session_id"]),
            )
        if not isinstance(session_ref, str) or not is_public_ref(session_ref):
            raise RuntimeError(f"session {row['session_id']!r} has an invalid public ref")
        if session_ref in used_session_refs:
            raise RuntimeError(f"session public ref collision: {session_ref}")
        used_session_refs.add(session_ref)


def _legacy_provider_arguments(value: object, *, location: str) -> dict:
    if not isinstance(value, str):
        raise SessionDataCorruptionError(f"{location} arguments must be encoded JSON")
    try:
        parsed = json.loads(value, parse_constant=_reject_nonfinite_json_constant)
    except (TypeError, ValueError) as exc:
        raise SessionDataCorruptionError(f"{location} arguments are invalid JSON") from exc
    if not isinstance(parsed, dict):
        raise SessionDataCorruptionError(f"{location} arguments must be an object")
    return parsed


def _migrate_provider_tool_calls_in_message(message: dict, *, location: str) -> bool:
    if "provider_tool_calls" not in message:
        return False
    calls = message["provider_tool_calls"]
    if not isinstance(calls, list):
        raise SessionDataCorruptionError(f"{location} provider_tool_calls must be an array")

    from arden.agent.types.llm import ProviderToolCall, ProviderToolPayloadError

    migrated_calls: list[dict] = []
    required_fields = {"id", "name", "arguments", "result", "done"}
    allowed_fields = required_fields | {"provider_item"}
    for index, raw_call in enumerate(calls):
        call_location = f"{location} provider_tool_calls[{index}]"
        if not isinstance(raw_call, dict):
            raise SessionDataCorruptionError(f"{call_location} must be an object")
        missing = required_fields - set(raw_call)
        unknown = set(raw_call) - allowed_fields
        if missing:
            raise SessionDataCorruptionError(f"{call_location} is missing fields: {sorted(missing)}")
        if unknown:
            raise SessionDataCorruptionError(f"{call_location} has unknown fields: {sorted(unknown)}")

        arguments = _legacy_provider_arguments(raw_call["arguments"], location=call_location)
        argument_sources = [arguments]
        provider_item = raw_call.get("provider_item")
        if provider_item is not None:
            if not isinstance(provider_item, dict):
                raise SessionDataCorruptionError(f"{call_location} provider_item must be an object")
            provider_arguments = provider_item.get("arguments")
            if provider_arguments is not None:
                if not isinstance(provider_arguments, dict):
                    raise SessionDataCorruptionError(f"{call_location} provider_item arguments must be an object")
                argument_sources.append(provider_arguments)

        loaded_tool_names: list[str] = []
        for argument_source in argument_sources:
            for key in ("tools", "paths", "names"):
                values = argument_source.get(key)
                if values is None:
                    continue
                if not isinstance(values, list):
                    raise SessionDataCorruptionError(f"{call_location} {key} must be an array")
                for value in values:
                    if not isinstance(value, str) or not value.strip():
                        raise SessionDataCorruptionError(f"{call_location} {key} must contain non-empty strings")
                    if value not in loaded_tool_names:
                        loaded_tool_names.append(value)

        try:
            migrated_call = ProviderToolCall(
                id=raw_call["id"],
                name=raw_call["name"],
                arguments=arguments,
                result=raw_call["result"],
                done=raw_call["done"],
                loaded_tool_names=tuple(loaded_tool_names),
            )
        except ProviderToolPayloadError as exc:
            raise SessionDataCorruptionError(f"{call_location} is invalid: {exc}") from exc
        migrated_calls.append(migrated_call.to_history_dict())

    message["provider_tool_calls"] = migrated_calls
    return True


async def _rewrite_provider_receipts(
    conn: aiosqlite.Connection,
    *,
    strict_active_projection: StrictProjection,
) -> None:
    session_rows = await conn.execute_fetchall(
        "SELECT session_id, messages FROM sessions WHERE instr(messages, '\"provider_tool_calls\"') > 0"
    )
    for row in session_rows:
        session_id = str(row["session_id"])
        messages = strict_active_projection(session_id, row["messages"])
        changed = False
        for index, message in enumerate(messages):
            changed |= _migrate_provider_tool_calls_in_message(
                message,
                location=f"session {session_id} messages[{index}]",
            )
        if changed:
            await conn.execute(
                "UPDATE sessions SET messages = ? WHERE session_id = ?",
                (json.dumps(messages, ensure_ascii=False, separators=(",", ":")), session_id),
            )

    transcript_rows = await conn.execute_fetchall(
        "SELECT rowid, session_id, message_id, message_json FROM session_messages "
        "WHERE instr(message_json, '\"provider_tool_calls\"') > 0"
    )
    for row in transcript_rows:
        location = f"session {row['session_id']} transcript message {row['message_id']}"
        try:
            message = json.loads(row["message_json"], parse_constant=_reject_nonfinite_json_constant)
        except (TypeError, ValueError) as exc:
            raise SessionDataCorruptionError(f"{location} is invalid JSON") from exc
        if not isinstance(message, dict):
            raise SessionDataCorruptionError(f"{location} must be an object")
        if _migrate_provider_tool_calls_in_message(message, location=location):
            await conn.execute(
                "UPDATE session_messages SET message_json = ? WHERE rowid = ?",
                (json.dumps(message, ensure_ascii=False, separators=(",", ":")), row["rowid"]),
            )


async def _repair_transcript_gaps(
    conn: aiosqlite.Connection,
    *,
    strict_active_projection: StrictProjection,
    stamp_messages: StampMessages,
    mirror_session_messages: MirrorMessages,
) -> None:
    transcript_counts = {
        str(row["session_id"]): int(row["message_count"])
        for row in await conn.execute_fetchall(
            "SELECT session_id, count(*) AS message_count FROM session_messages GROUP BY session_id"
        )
    }
    rows = await conn.execute_fetchall("SELECT session_id, messages FROM sessions")
    now = datetime.now(UTC).isoformat()
    for row in rows:
        session_id = str(row["session_id"])
        messages = strict_active_projection(session_id, row["messages"])
        if not messages:
            continue
        if transcript_counts.get(session_id, 0) == 0:
            stamp_messages(messages, now)
            await mirror_session_messages(session_id, messages, connection=conn)
            await conn.execute(
                "UPDATE sessions SET messages = ? WHERE session_id = ?",
                (json.dumps(messages, default=str), session_id),
            )
            continue

        message_ids = [message.get("message_id") for message in messages]
        if any(not isinstance(message_id, str) or not message_id for message_id in message_ids):
            raise SessionDataCorruptionError(
                f"session {session_id} has an active message without a stable transcript id"
            )
        if len(set(message_ids)) != len(message_ids):
            raise SessionDataCorruptionError(f"session {session_id} has duplicate active message ids")

    gap_rows = await conn.execute_fetchall(
        """
        SELECT DISTINCT s.session_id, s.messages
        FROM sessions AS s, json_each(s.messages) AS active
        WHERE EXISTS (
            SELECT 1 FROM session_messages AS any_message WHERE any_message.session_id = s.session_id
        )
          AND NOT EXISTS (
            SELECT 1
            FROM session_messages AS transcript
            WHERE transcript.session_id = s.session_id
              AND transcript.message_id = json_extract(active.value, '$.message_id')
          )
        """
    )
    for row in gap_rows:
        session_id = str(row["session_id"])
        messages = strict_active_projection(session_id, row["messages"])
        await mirror_session_messages(session_id, messages, connection=conn)


async def _rebuild_canonical_tables(conn: aiosqlite.Connection) -> None:
    for table in _REBUILT_TABLES:
        await conn.execute(f"CREATE TEMP TABLE _v5_{table} AS SELECT * FROM {table}")

    for table in reversed(_REBUILT_TABLES):
        await conn.execute(f"DROP TABLE {table}")
    for table in _REBUILT_TABLES:
        await conn.execute(CANONICAL_TABLE_SQL[table])

    await conn.execute(
        """
        INSERT INTO areas (
            area_id, area_ref, name, name_key, default_cwds, instructions,
            page_path, page_id, autonomy, attention, interrupts, paused_at,
            created_at, updated_at, archived_at
        )
        SELECT
            area_id, area_ref, name, name_key, default_cwds, instructions,
            page_path, page_id, autonomy, COALESCE(attention, 'ambient'),
            COALESCE(interrupts, 'asks'), paused_at, created_at, updated_at, archived_at
        FROM _v5_areas
        """
    )
    await conn.execute(
        """
        INSERT INTO sessions (
            session_id, public_ref, started_at, last_activity, last_accessed_at,
            messages, metadata, name, archived_at, session_type, origin_automation_id,
            parent_session_id, parent_tool_call_id, agent_type, agent_status, area_id,
            chat_model, context_generation, active_message_count, storage_state,
            cold_bundle_path, cold_bundle_sha256, cold_bundle_bytes, cold_logical_bytes,
            cold_message_count, cold_prose_sha256, cold_blob_hashes_json
        )
        SELECT
            session_id, public_ref, started_at, last_activity, last_accessed_at,
            messages, metadata, name, archived_at, session_type, origin_automation_id,
            parent_session_id, parent_tool_call_id, agent_type, agent_status, area_id,
            chat_model, context_generation, active_message_count, storage_state,
            cold_bundle_path, cold_bundle_sha256, cold_bundle_bytes, cold_logical_bytes,
            cold_message_count, cold_prose_sha256, cold_blob_hashes_json
        FROM _v5_sessions
        """
    )
    await conn.execute(
        """
        INSERT INTO background_agent_runs (
            task_id, agent_ref, session_id, parent_run_id, parent_tool_call_id,
            suspension_id, child_session_id, agent_type, wait, status, command,
            detail, result_ref, result_text, created_at, started_at, updated_at,
            ended_at, cancel_requested_at, cancel_actor, terminal_cause,
            cancel_generation, cancel_idempotency_key, notified_at, completion_id,
            spawn_spec, spawn_attempts
        )
        SELECT
            task_id, agent_ref, session_id, parent_run_id, parent_tool_call_id,
            suspension_id, child_session_id, agent_type, wait, status, command,
            detail, result_ref, result_text, created_at, started_at, updated_at,
            ended_at, cancel_requested_at, cancel_actor, terminal_cause,
            cancel_generation, cancel_idempotency_key, notified_at, completion_id,
            spawn_spec, spawn_attempts
        FROM _v5_background_agent_runs
        """
    )
    await conn.execute(
        """
        INSERT INTO chat_compactions (
            compaction_id, session_id, boundary_seq, messages_before, messages_after,
            rehydration_state, created_at
        )
        SELECT
            compaction_id, session_id, boundary_seq, messages_before, messages_after,
            rehydration_state, created_at
        FROM _v5_chat_compactions
        """
    )
    await conn.execute(
        """
        INSERT INTO tool_calls (
            run_id, session_id, tool_call_id, tool_name, action, scope, args_hash,
            status, result_preview, result_ref, outcome_json, started_at, ended_at
        )
        SELECT
            run_id, session_id, tool_call_id, tool_name, action, scope, args_hash,
            status, result_preview, result_ref, outcome_json, started_at, ended_at
        FROM _v5_tool_calls
        """
    )

    for table in _REBUILT_TABLES:
        await conn.execute(f"DROP TABLE temp._v5_{table}")
    for item in CANONICAL_SQL_OBJECTS:
        if item.kind == "index" and item.table in _REBUILT_TABLES:
            await conn.execute(item.sql)


async def migrate_v5_to_v9(
    conn: aiosqlite.Connection,
    *,
    strict_active_projection: StrictProjection,
    parse_metadata: MetadataParser,
    stamp_messages: StampMessages,
    mirror_session_messages: MirrorMessages,
    validate_schema: SchemaValidator,
) -> None:
    """Atomically convert the one accepted v5 layout to canonical v9."""

    cold = await conn.execute_fetchall(
        "SELECT session_id FROM sessions WHERE storage_state = 'cold' LIMIT 1"
    )
    if cold:
        raise SessionDataCorruptionError(
            f"session {cold[0]['session_id']} must be restored from cold storage before schema v9 migration"
        )

    if conn.in_transaction:
        raise RuntimeError("context migration requires an idle database connection")
    foreign_keys = await conn.execute_fetchall("PRAGMA foreign_keys")
    if int(foreign_keys[0][0]) != 1:
        raise RuntimeError("context migration requires foreign key enforcement")

    await conn.execute("PRAGMA foreign_keys=OFF")
    try:
        await conn.execute("BEGIN IMMEDIATE")
        rows = await conn.execute_fetchall("SELECT session_id, messages, metadata FROM sessions")
        for row in rows:
            session_id = str(row["session_id"])
            strict_active_projection(session_id, row["messages"])
            parse_metadata(session_id, row["metadata"])

        await _rewrite_provider_receipts(conn, strict_active_projection=strict_active_projection)
        await _backfill_public_refs(conn)
        await conn.execute("ALTER TABLE sessions ADD COLUMN active_message_count INTEGER NOT NULL DEFAULT 0")
        await conn.execute(
            """
            UPDATE sessions
            SET active_message_count = json_array_length(messages),
                metadata = json_remove(metadata, '$.last_message_count')
            """
        )
        await _repair_transcript_gaps(
            conn,
            strict_active_projection=strict_active_projection,
            stamp_messages=stamp_messages,
            mirror_session_messages=mirror_session_messages,
        )
        await _rebuild_canonical_tables(conn)
        await conn.execute("DROP TABLE tool_results_legacy")

        cursor = await conn.execute(
            "UPDATE session_store_meta SET value = '9' WHERE key = 'schema_version' AND value = '5'"
        )
        if cursor.rowcount != 1:
            raise RuntimeError("schema v5 version row changed during migration")
        await validate_schema()
        violations = await conn.execute_fetchall("PRAGMA foreign_key_check")
        if violations:
            raise RuntimeError("context migration produced foreign key violations")
        await conn.commit()
    except BaseException:
        if conn.in_transaction:
            await conn.rollback()
        raise
    finally:
        await conn.execute("PRAGMA foreign_keys=ON")
