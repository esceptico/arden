"""Exact typed manifest and postcondition for the current context schema."""

from collections import defaultdict
from dataclasses import dataclass

import aiosqlite

from arden.context.errors import SessionSchemaError
from arden.context.schema_sql import CANONICAL_SQL_OBJECTS, normalize_schema_sql


@dataclass(frozen=True, slots=True)
class ColumnManifest:
    name: str
    declared_type: str
    not_null: bool
    default: str | None
    primary_key_position: int


@dataclass(frozen=True, slots=True)
class ForeignKeyManifest:
    referenced_table: str
    source_column: str
    referenced_column: str
    on_update: str
    on_delete: str
    match: str


@dataclass(frozen=True, slots=True)
class TableManifest:
    name: str
    sql: str
    columns: tuple[ColumnManifest, ...]
    foreign_keys: tuple[ForeignKeyManifest, ...]


@dataclass(frozen=True, slots=True)
class IndexTermManifest:
    column: str | None
    descending: bool
    collation: str


@dataclass(frozen=True, slots=True)
class IndexManifest:
    table: str
    name: str
    unique: bool
    origin: str
    partial: bool
    terms: tuple[IndexTermManifest, ...]
    sql: str | None


@dataclass(frozen=True, slots=True)
class TriggerManifest:
    table: str
    name: str
    sql: str


@dataclass(frozen=True, slots=True)
class ContextSchemaManifest:
    tables: tuple[TableManifest, ...]
    indexes: tuple[IndexManifest, ...]
    triggers: tuple[TriggerManifest, ...]


_FTS_SHADOW_SQL = {
    "session_messages_fts_config": "CREATE TABLE 'session_messages_fts_config'(k PRIMARY KEY,v) WITHOUT ROWID",
    "session_messages_fts_data": "CREATE TABLE 'session_messages_fts_data'(id INTEGER PRIMARY KEY,block BLOB)",
    "session_messages_fts_docsize": "CREATE TABLE 'session_messages_fts_docsize'(id INTEGER PRIMARY KEY,sz BLOB)",
    "session_messages_fts_idx": (
        "CREATE TABLE 'session_messages_fts_idx'(segid,term,pgno,PRIMARY KEY(segid,term)) WITHOUT ROWID"
    ),
}

_OPTIONAL_INDEX_SQL = {
    "idx_chat_runs_recovery_status": (
        "CREATE INDEX idx_chat_runs_recovery_status ON chat_runs(status) "
        "WHERE status IN ('pending','running','backgrounded','interrupted')"
    ),
    "idx_chat_queued_messages_recovery_status_run": (
        "CREATE INDEX idx_chat_queued_messages_recovery_status_run ON chat_queued_messages(status,run_id) "
        "WHERE status IN ('queued','failed_retryable')"
    ),
    "idx_background_agent_runs_recovery_status": (
        "CREATE INDEX idx_background_agent_runs_recovery_status ON background_agent_runs(status,updated_at) "
        "WHERE status IN ('running','activity','cancel_requested','interrupted')"
    ),
    "idx_background_agent_runs_undelivered": (
        "CREATE INDEX idx_background_agent_runs_undelivered ON background_agent_runs(ended_at) "
        "WHERE completion_id IS NOT NULL AND notified_at IS NULL"
    ),
    "idx_sessions_agent_recovery": (
        "CREATE INDEX idx_sessions_agent_recovery ON sessions(agent_status) "
        "WHERE session_type = 'agent' AND agent_status = 'running'"
    ),
}

_EXPECTED_AUTO_INDEX_COLUMNS = {
    ("areas", "sqlite_autoindex_areas_1", "pk", ("area_id",)),
    ("areas", "sqlite_autoindex_areas_2", "u", ("area_ref",)),
    ("background_agent_events", "sqlite_autoindex_background_agent_events_1", "pk", ("session_id", "seq")),
    ("background_agent_runs", "sqlite_autoindex_background_agent_runs_1", "pk", ("session_id", "task_id")),
    ("chat_compactions", "sqlite_autoindex_chat_compactions_1", "pk", ("compaction_id",)),
    ("chat_idempotency_keys", "sqlite_autoindex_chat_idempotency_keys_1", "pk", ("session_id", "client_id")),
    ("chat_queued_messages", "sqlite_autoindex_chat_queued_messages_1", "pk", ("client_id",)),
    ("chat_runs", "sqlite_autoindex_chat_runs_1", "pk", ("run_id",)),
    ("execution_cancellation_scopes", "sqlite_autoindex_execution_cancellation_scopes_1", "pk", ("session_id",)),
    ("run_sidecars", "sqlite_autoindex_run_sidecars_1", "pk", ("run_id",)),
    ("session_event_retention_state", "sqlite_autoindex_session_event_retention_state_1", "pk", ("session_id",)),
    ("session_events", "sqlite_autoindex_session_events_1", "pk", ("session_id", "seq")),
    ("session_goals", "sqlite_autoindex_session_goals_1", "pk", ("session_id",)),
    ("session_messages", "sqlite_autoindex_session_messages_1", "pk", ("session_id", "message_id")),
    ("session_messages", "sqlite_autoindex_session_messages_2", "u", ("session_id", "seq")),
    ("session_messages_fts_config", "sqlite_autoindex_session_messages_fts_config_1", "pk", ("k",)),
    ("session_messages_fts_idx", "sqlite_autoindex_session_messages_fts_idx_1", "pk", ("segid", "term")),
    ("session_store_meta", "sqlite_autoindex_session_store_meta_1", "pk", ("key",)),
    ("session_todo_overrides", "sqlite_autoindex_session_todo_overrides_1", "pk", ("session_id",)),
    ("session_todos", "sqlite_autoindex_session_todos_1", "pk", ("session_id",)),
    ("session_turns", "sqlite_autoindex_session_turns_1", "pk", ("session_id", "turn_id")),
    ("session_turns", "sqlite_autoindex_session_turns_2", "u", ("session_id", "turn_index")),
    ("sessions", "sqlite_autoindex_sessions_1", "pk", ("session_id",)),
    ("sessions", "sqlite_autoindex_sessions_2", "u", ("public_ref",)),
    ("storage_cleanup_runs", "sqlite_autoindex_storage_cleanup_runs_1", "pk", ("run_id",)),
    ("tool_approvals", "sqlite_autoindex_tool_approvals_1", "pk", ("run_id", "tool_call_id")),
    ("tool_calls", "sqlite_autoindex_tool_calls_1", "pk", ("run_id", "tool_call_id")),
    ("tool_results", "sqlite_autoindex_tool_results_1", "pk", ("tool_result_id",)),
    ("tool_results", "sqlite_autoindex_tool_results_2", "u", ("session_id", "tool_call_id", "content_sha256")),
}
_EXPECTED_AUTO_INDEXES = {
    (
        table,
        name,
        True,
        origin,
        False,
        tuple((column, False, "BINARY") for column in columns),
    )
    for table, name, origin, columns in _EXPECTED_AUTO_INDEX_COLUMNS
}

_EXPECTED_FOREIGN_KEYS = {
    ForeignKeyManifest(
        referenced_table="areas",
        source_column="area_id",
        referenced_column="area_id",
        on_update="NO ACTION",
        on_delete="SET NULL",
        match="NONE",
    )
}


async def read_context_schema_manifest(
    conn: aiosqlite.Connection,
    *,
    include_optional_indexes: bool = False,
) -> ContextSchemaManifest:
    """Read the exact typed topology owned by the context store."""

    table_names = {item.name for item in CANONICAL_SQL_OBJECTS if item.kind == "table"} | set(_FTS_SHADOW_SQL)
    placeholders = ",".join("?" for _ in table_names)
    parameters = tuple(sorted(table_names))
    object_rows = await conn.execute_fetchall(
        f"SELECT name, sql FROM sqlite_master WHERE type = 'table' AND name IN ({placeholders}) ORDER BY name",
        parameters,
    )
    column_rows = await conn.execute_fetchall(
        f"""
        SELECT owner.name AS table_name, column_info.cid, column_info.name,
               column_info.type, column_info.[notnull] AS not_null,
               column_info.dflt_value, column_info.pk
        FROM sqlite_master AS owner, pragma_table_info(owner.name) AS column_info
        WHERE owner.type = 'table' AND owner.name IN ({placeholders})
        ORDER BY owner.name, column_info.cid
        """,
        parameters,
    )
    fk_rows = await conn.execute_fetchall(
        f"""
        SELECT owner.name AS table_name, foreign_key.id, foreign_key.seq,
               foreign_key.[table] AS referenced_table, foreign_key.[from] AS source_column,
               foreign_key.[to] AS referenced_column, foreign_key.on_update,
               foreign_key.on_delete, foreign_key.[match] AS match_type
        FROM sqlite_master AS owner, pragma_foreign_key_list(owner.name) AS foreign_key
        WHERE owner.type = 'table' AND owner.name IN ({placeholders})
        ORDER BY owner.name, foreign_key.id, foreign_key.seq
        """,
        parameters,
    )
    index_rows = await conn.execute_fetchall(
        f"""
        SELECT owner.name AS table_name, listed.name, listed.[unique] AS is_unique,
               listed.origin, listed.partial, definition.sql
        FROM sqlite_master AS owner, pragma_index_list(owner.name) AS listed
        LEFT JOIN sqlite_master AS definition
          ON definition.type = 'index' AND definition.name = listed.name
        WHERE owner.type = 'table' AND owner.name IN ({placeholders})
        ORDER BY owner.name, listed.name
        """,
        parameters,
    )
    term_rows = await conn.execute_fetchall(
        f"""
        SELECT listed.name AS index_name, term.seqno, term.name,
               term.[desc] AS descending, term.coll AS collation
        FROM sqlite_master AS owner, pragma_index_list(owner.name) AS listed,
             pragma_index_xinfo(listed.name) AS term
        WHERE owner.type = 'table' AND owner.name IN ({placeholders}) AND term.[key] = 1
        ORDER BY listed.name, term.seqno
        """,
        parameters,
    )
    trigger_rows = await conn.execute_fetchall(
        f"SELECT tbl_name, name, sql FROM sqlite_master "
        f"WHERE type = 'trigger' AND tbl_name IN ({placeholders}) ORDER BY name",
        parameters,
    )

    columns_by_table: dict[str, list[ColumnManifest]] = defaultdict(list)
    for row in column_rows:
        columns_by_table[row["table_name"]].append(
            ColumnManifest(
                name=row["name"],
                declared_type=row["type"],
                not_null=bool(row["not_null"]),
                default=row["dflt_value"],
                primary_key_position=int(row["pk"]),
            )
        )
    foreign_keys_by_table: dict[str, list[ForeignKeyManifest]] = defaultdict(list)
    for row in fk_rows:
        foreign_keys_by_table[row["table_name"]].append(
            ForeignKeyManifest(
                referenced_table=row["referenced_table"],
                source_column=row["source_column"],
                referenced_column=row["referenced_column"],
                on_update=row["on_update"],
                on_delete=row["on_delete"],
                match=row["match_type"],
            )
        )
    terms_by_index: dict[str, list[IndexTermManifest]] = defaultdict(list)
    for row in term_rows:
        terms_by_index[row["index_name"]].append(
            IndexTermManifest(
                column=row["name"],
                descending=bool(row["descending"]),
                collation=row["collation"],
            )
        )

    return ContextSchemaManifest(
        tables=tuple(
            TableManifest(
                name=row["name"],
                sql=normalize_schema_sql(row["sql"] or ""),
                columns=tuple(columns_by_table[row["name"]]),
                foreign_keys=tuple(foreign_keys_by_table[row["name"]]),
            )
            for row in object_rows
        ),
        indexes=tuple(
            IndexManifest(
                table=row["table_name"],
                name=row["name"],
                unique=bool(row["is_unique"]),
                origin=row["origin"],
                partial=bool(row["partial"]),
                terms=tuple(terms_by_index[row["name"]]),
                sql=normalize_schema_sql(row["sql"]) if row["sql"] else None,
            )
            for row in index_rows
            if include_optional_indexes or row["name"] not in _OPTIONAL_INDEX_SQL
        ),
        triggers=tuple(
            TriggerManifest(
                table=row["tbl_name"],
                name=row["name"],
                sql=normalize_schema_sql(row["sql"] or ""),
            )
            for row in trigger_rows
        ),
    )


def _index_signature(index: IndexManifest) -> tuple:
    return (
        index.table,
        index.name,
        index.unique,
        index.origin,
        index.partial,
        tuple((term.column, term.descending, term.collation) for term in index.terms),
    )


async def assert_v9_schema(conn: aiosqlite.Connection) -> None:
    """Reject any current-schema topology that is not canonical."""

    manifest = await read_context_schema_manifest(conn, include_optional_indexes=True)
    expected_table_sql = {
        item.name: normalize_schema_sql(item.sql)
        for item in CANONICAL_SQL_OBJECTS
        if item.kind == "table"
    } | {name: normalize_schema_sql(sql) for name, sql in _FTS_SHADOW_SQL.items()}
    actual_tables = {table.name: table for table in manifest.tables}
    if set(actual_tables) != set(expected_table_sql):
        raise SessionSchemaError(
            f"context schema v9 has invalid tables: expected {sorted(expected_table_sql)}, got {sorted(actual_tables)}"
        )
    for name, expected_sql in expected_table_sql.items():
        if actual_tables[name].sql != expected_sql:
            raise SessionSchemaError(f"context schema v9 has a non-canonical {name} table")

    expected_indexes = {
        item.name: normalize_schema_sql(item.sql)
        for item in CANONICAL_SQL_OBJECTS
        if item.kind == "index"
    }
    actual_named = {index.name: index for index in manifest.indexes if index.origin == "c"}
    required_named = set(expected_indexes)
    optional_named = set(actual_named) & set(_OPTIONAL_INDEX_SQL)
    if set(actual_named) != required_named | optional_named:
        raise SessionSchemaError(
            f"context schema v9 has invalid named indexes: expected {sorted(required_named)}, "
            f"got {sorted(actual_named)}"
        )
    for name, expected_sql in expected_indexes.items():
        if actual_named[name].sql != expected_sql:
            raise SessionSchemaError(f"context schema v9 has a non-canonical {name} index")
    for name in optional_named:
        if actual_named[name].sql != normalize_schema_sql(_OPTIONAL_INDEX_SQL[name]):
            raise SessionSchemaError(f"context schema v9 has a non-canonical {name} recovery index")

    actual_auto = {
        _index_signature(index)
        for index in manifest.indexes
        if index.origin in {"pk", "u"}
    }
    if actual_auto != _EXPECTED_AUTO_INDEXES:
        raise SessionSchemaError("context schema v9 has invalid automatic indexes")

    expected_triggers = {
        item.name: (item.table, normalize_schema_sql(item.sql))
        for item in CANONICAL_SQL_OBJECTS
        if item.kind == "trigger"
    }
    actual_triggers = {trigger.name: (trigger.table, trigger.sql) for trigger in manifest.triggers}
    if actual_triggers != expected_triggers:
        raise SessionSchemaError("context schema v9 has invalid session-message FTS triggers")

    actual_foreign_keys = {
        foreign_key
        for table in manifest.tables
        for foreign_key in table.foreign_keys
    }
    if actual_foreign_keys != _EXPECTED_FOREIGN_KEYS:
        raise SessionSchemaError("context schema v9 has invalid foreign keys")

    legacy = await conn.execute_fetchall(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND "
        "(name GLOB '*_old*' OR name LIKE 'tool_results_legacy%')"
    )
    if legacy:
        raise SessionSchemaError(f"context schema v9 has legacy tables: {[row['name'] for row in legacy]}")
