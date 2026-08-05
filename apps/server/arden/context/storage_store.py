import asyncio
import hashlib
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiosqlite

from arden.storage_budget import StorageSessionCandidate
from arden.trajectory.cold_storage import ColdBundleManifest, read_cold_session_bundle, write_cold_session_bundle

SQL_LOAD_SESSION = "SELECT * FROM sessions WHERE session_id = ?"
SessionLock = Callable[[str], Awaitable[asyncio.Lock]]
StrictProjection = Callable[[str, object], list[dict]]


class SessionStorageStore:
    """Cold storage, deletion, and database reclamation for sessions."""

    def __init__(
        self,
        conn: aiosqlite.Connection,
        read_conn: aiosqlite.Connection,
        maintenance_lock: asyncio.Lock,
        session_lock: SessionLock,
        strict_active_projection: StrictProjection,
    ):
        self._conn = conn
        self._read_conn = read_conn
        self._maintenance_lock = maintenance_lock
        self._session_lock = session_lock
        self._strict_active_projection = strict_active_projection

    async def permanently_delete_session(self, session_id: str) -> bool:
        return await self.purge_session(session_id, require_archived=True)

    @staticmethod
    def _quoted_identifier(identifier: str) -> str:
        return '"' + identifier.replace('"', '""') + '"'

    @asynccontextmanager
    async def maintenance_connection(self) -> AsyncIterator[aiosqlite.Connection]:
        """Use an isolated transaction connection for file-backed databases."""

        rows = await self._conn.execute_fetchall("PRAGMA database_list")
        database_path = next((str(row["file"]) for row in rows if row["name"] == "main"), "")
        if not database_path:
            yield self._conn
            return
        connection = await aiosqlite.connect(database_path)
        connection.row_factory = aiosqlite.Row
        try:
            await connection.execute("PRAGMA busy_timeout=5000")
            await connection.execute("PRAGMA foreign_keys=ON")
            yield connection
        finally:
            await connection.close()

    async def session_owned_tables(self, connection: aiosqlite.Connection) -> list[str]:
        rows = await connection.execute_fetchall(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        owned: list[str] = []
        for row in rows:
            table = str(row["name"])
            if table == "sessions" or table.startswith("session_messages_fts"):
                continue
            columns = await connection.execute_fetchall(f"PRAGMA table_info({self._quoted_identifier(table)})")
            if any(column["name"] == "session_id" for column in columns):
                owned.append(table)
        return owned

    async def _snapshot_session(self, session_id: str) -> dict[str, Any] | None:
        rows = await self._read_conn.execute_fetchall(SQL_LOAD_SESSION, (session_id,))
        if not rows:
            return None
        tables: dict[str, list[dict[str, Any]]] = {}
        for table in await self.session_owned_tables(self._read_conn):
            table_rows = await self._read_conn.execute_fetchall(
                f"SELECT * FROM {self._quoted_identifier(table)} WHERE session_id = ?",
                (session_id,),
            )
            if table_rows:
                tables[table] = [dict(row) for row in table_rows]
        return {"session": dict(rows[0]), "tables": tables}

    async def cold_convert_session(
        self,
        session_id: str,
        *,
        cold_root: Path,
    ) -> ColdBundleManifest | None:
        """Move an archived hot session into a verified, restorable bundle."""

        session_lock = await self._session_lock(session_id)
        async with self._maintenance_lock, session_lock:
            snapshot = await self._snapshot_session(session_id)
            if snapshot is None:
                return None
            session = snapshot["session"]
            if session.get("archived_at") is None:
                raise ValueError("only archived sessions can be converted to cold storage")
            if session.get("storage_state") == "cold":
                return None
            digest = hashlib.sha256(session_id.encode()).hexdigest()
            bundle_path = cold_root / digest[:2] / f"{digest}.session.tar.zst"
            manifest = await asyncio.to_thread(write_cold_session_bundle, snapshot, bundle_path)
            try:
                async with self.maintenance_connection() as connection:
                    await connection.execute("BEGIN IMMEDIATE")
                    current = await connection.execute_fetchall(
                        "SELECT archived_at, storage_state FROM sessions WHERE session_id = ?",
                        (session_id,),
                    )
                    if not current or current[0]["archived_at"] is None or current[0]["storage_state"] != "hot":
                        await connection.rollback()
                        raise RuntimeError("session changed while cold conversion was being prepared")
                    for table in await self.session_owned_tables(connection):
                        await connection.execute(
                            f"DELETE FROM {self._quoted_identifier(table)} WHERE session_id = ?",
                            (session_id,),
                        )
                    await connection.execute(
                        """
                        UPDATE sessions
                        SET messages = '[]', metadata = '{}', storage_state = 'cold',
                            active_message_count = 0,
                            context_generation = context_generation + 1,
                            cold_bundle_path = ?, cold_bundle_sha256 = ?, cold_bundle_bytes = ?,
                            cold_logical_bytes = ?, cold_message_count = ?, cold_prose_sha256 = ?,
                            cold_blob_hashes_json = ?
                        WHERE session_id = ?
                        """,
                        (
                            manifest.bundle_path,
                            manifest.bundle_sha256,
                            manifest.bundle_bytes,
                            manifest.logical_bytes,
                            manifest.message_count,
                            manifest.user_assistant_prose_sha256,
                            json.dumps(manifest.blob_hashes),
                            session_id,
                        ),
                    )
                    await connection.commit()
            except Exception:
                bundle_path.unlink(missing_ok=True)
                raise
            return manifest

    async def rehydrate_cold_session(self, session_id: str, *, blob_root: Path) -> bool:
        """Restore a cold bundle into the live schema before unarchiving it."""

        session_lock = await self._session_lock(session_id)
        async with self._maintenance_lock, session_lock:
            rows = await self._read_conn.execute_fetchall(SQL_LOAD_SESSION, (session_id,))
            if not rows or rows[0]["storage_state"] != "cold":
                return False
            row = rows[0]
            bundle_path = Path(str(row["cold_bundle_path"] or ""))
            manifest = ColdBundleManifest(
                format_version="arden-cold-session-v1",
                session_id=session_id,
                bundle_path=str(bundle_path),
                bundle_sha256=str(row["cold_bundle_sha256"]),
                bundle_bytes=int(row["cold_bundle_bytes"] or 0),
                message_count=int(row["cold_message_count"] or 0),
                logical_bytes=int(row["cold_logical_bytes"] or 0),
                user_assistant_prose_sha256=str(row["cold_prose_sha256"]),
                blob_hashes=tuple(json.loads(row["cold_blob_hashes_json"] or "[]")),
            )
            snapshot = await asyncio.to_thread(
                read_cold_session_bundle,
                bundle_path,
                blob_root=blob_root,
                restore_blobs=True,
                verify_manifest=manifest,
            )
            async with self.maintenance_connection() as connection:
                await connection.execute("BEGIN IMMEDIATE")
                current = await connection.execute_fetchall(
                    "SELECT storage_state, cold_bundle_sha256, context_generation FROM sessions WHERE session_id = ?",
                    (session_id,),
                )
                if (
                    not current
                    or current[0]["storage_state"] != "cold"
                    or current[0]["cold_bundle_sha256"] != manifest.bundle_sha256
                ):
                    await connection.rollback()
                    raise RuntimeError("cold session changed while restoration was being prepared")
                for table, table_rows in snapshot.get("tables", {}).items():
                    if not table_rows:
                        continue
                    schema_rows = await connection.execute_fetchall(
                        f"PRAGMA table_info({self._quoted_identifier(table)})"
                    )
                    available = {schema_row["name"] for schema_row in schema_rows}
                    columns = [column for column in table_rows[0] if column in available]
                    if not columns:
                        continue
                    quoted_columns = ", ".join(self._quoted_identifier(column) for column in columns)
                    placeholders = ", ".join("?" for _ in columns)
                    await connection.executemany(
                        f"INSERT INTO {self._quoted_identifier(table)} ({quoted_columns}) VALUES ({placeholders})",
                        [tuple(table_row.get(column) for column in columns) for table_row in table_rows],
                    )
                original = snapshot["session"]
                restored_messages = self._strict_active_projection(session_id, original.get("messages"))
                schema_rows = await connection.execute_fetchall("PRAGMA table_info(sessions)")
                restorable = {
                    schema_row["name"]
                    for schema_row in schema_rows
                    if schema_row["name"] not in {"session_id", "context_generation", "active_message_count"}
                    and not schema_row["name"].startswith("cold_")
                }
                columns = [column for column in original if column in restorable and column != "storage_state"]
                assignments = ", ".join(f"{self._quoted_identifier(column)} = ?" for column in columns)
                await connection.execute(
                    f"""
                    UPDATE sessions SET {assignments}, storage_state = 'hot',
                        active_message_count = ?,
                        cold_bundle_path = NULL, cold_bundle_sha256 = NULL, cold_bundle_bytes = NULL,
                        cold_logical_bytes = NULL, cold_message_count = NULL, cold_prose_sha256 = NULL,
                        cold_blob_hashes_json = NULL
                    WHERE session_id = ?
                    """,
                    (*[original.get(column) for column in columns], len(restored_messages), session_id),
                )
                await connection.commit()
            bundle_path.unlink(missing_ok=True)
            return True

    async def purge_session(self, session_id: str, *, require_archived: bool) -> bool:
        """Delete the session row and every table row owned by it."""

        session_lock = await self._session_lock(session_id)
        async with self._maintenance_lock, session_lock:
            async with self.maintenance_connection() as connection:
                await connection.execute("BEGIN IMMEDIATE")
                rows = await connection.execute_fetchall(
                    "SELECT archived_at, cold_bundle_path FROM sessions WHERE session_id = ?",
                    (session_id,),
                )
                if not rows or (require_archived and rows[0]["archived_at"] is None):
                    await connection.rollback()
                    return False
                bundle_path = Path(rows[0]["cold_bundle_path"]) if rows[0]["cold_bundle_path"] else None
                for table in await self.session_owned_tables(connection):
                    await connection.execute(
                        f"DELETE FROM {self._quoted_identifier(table)} WHERE session_id = ?",
                        (session_id,),
                    )
                await connection.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
                await connection.commit()
            if bundle_path is not None:
                bundle_path.unlink(missing_ok=True)
            return True

    async def list_storage_cleanup_candidates(self) -> list[StorageSessionCandidate]:
        rows = await self._read_conn.execute_fetchall(
            """
            SELECT s.session_id, s.archived_at, s.storage_state,
                   COALESCE(s.last_accessed_at, s.last_activity) AS last_activity,
                   s.session_type, s.origin_automation_id,
                   CASE WHEN s.storage_state = 'cold'
                        THEN COALESCE(s.cold_bundle_bytes, 0)
                        ELSE length(COALESCE(s.messages, '')) + length(COALESCE(s.metadata, ''))
                           + COALESCE((SELECT sum(length(message_json) + length(COALESCE(search_text, '')))
                                       FROM session_messages m WHERE m.session_id = s.session_id), 0)
                           + COALESCE((SELECT sum(length(event_json))
                                       FROM session_events e WHERE e.session_id = s.session_id), 0)
                           + COALESCE((SELECT sum(stored_bytes)
                                       FROM tool_results t WHERE t.session_id = s.session_id), 0)
                   END AS logical_bytes,
                   CASE WHEN s.storage_state = 'cold' THEN COALESCE(s.cold_message_count, 0)
                        ELSE (SELECT count(*) FROM session_messages m WHERE m.session_id = s.session_id)
                   END AS message_count,
                   (SELECT status FROM session_goals g WHERE g.session_id = s.session_id) AS goal_status
            FROM sessions s
            ORDER BY s.last_activity ASC, s.session_id ASC
            """
        )
        candidates: list[StorageSessionCandidate] = []
        for row in rows:
            protected: list[str] = []
            if (row["session_type"] or "chat") != "chat" or row["origin_automation_id"] is not None:
                protected.append("automation or agent session")
            if row["goal_status"] not in {None, "complete"}:
                protected.append("unfinished goal")
            candidates.append(
                StorageSessionCandidate(
                    session_id=row["session_id"],
                    archived=row["archived_at"] is not None,
                    storage_state=row["storage_state"] or "hot",
                    last_activity=row["last_activity"],
                    logical_bytes=max(0, int(row["logical_bytes"] or 0)),
                    message_count=max(0, int(row["message_count"] or 0)),
                    protected_reasons=tuple(protected),
                )
            )
        return candidates

    async def database_reclaim_status(self) -> dict[str, int | str]:
        mode_rows = await self._read_conn.execute_fetchall("PRAGMA auto_vacuum")
        page_rows = await self._read_conn.execute_fetchall("PRAGMA page_size")
        free_rows = await self._read_conn.execute_fetchall("PRAGMA freelist_count")
        mode = int(mode_rows[0][0])
        return {
            "mode": "incremental" if mode == 2 else "offline_migration_required",
            "page_size": int(page_rows[0][0]),
            "free_pages": int(free_rows[0][0]),
        }

    async def incremental_vacuum(self, *, pages: int = 2048) -> int:
        status = await self.database_reclaim_status()
        if status["mode"] != "incremental":
            return 0
        before = int(status["free_pages"]) * int(status["page_size"])
        async with self._maintenance_lock:
            await self._conn.execute(f"PRAGMA incremental_vacuum({max(1, min(pages, 8192))})")
            await self._conn.commit()
            await self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        after_status = await self.database_reclaim_status()
        after = int(after_status["free_pages"]) * int(after_status["page_size"])
        return max(0, before - after)

    async def record_storage_cleanup_run(
        self,
        *,
        plan_id: str,
        started_at: str,
        before_bytes: int,
        target_bytes: int,
        after_bytes: int,
        reclaimed_bytes: int,
        actions: list[dict[str, Any]],
        status: str,
        error: str | None = None,
    ) -> str:
        run_id = f"storage_{uuid4().hex}"
        await self._conn.execute(
            """
            INSERT INTO storage_cleanup_runs (
                run_id, plan_id, started_at, completed_at, before_bytes, target_bytes,
                after_bytes, reclaimed_bytes, actions_json, status, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                plan_id,
                started_at,
                datetime.now(UTC).isoformat(),
                before_bytes,
                target_bytes,
                after_bytes,
                reclaimed_bytes,
                json.dumps(actions, sort_keys=True),
                status,
                error,
            ),
        )
        await self._conn.commit()
        return run_id

    async def latest_storage_cleanup_run(self) -> dict[str, Any] | None:
        rows = await self._read_conn.execute_fetchall(
            "SELECT * FROM storage_cleanup_runs ORDER BY completed_at DESC LIMIT 1"
        )
        if not rows:
            return None
        row = rows[0]
        return {
            "run_id": row["run_id"],
            "plan_id": row["plan_id"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "before_bytes": row["before_bytes"],
            "target_bytes": row["target_bytes"],
            "after_bytes": row["after_bytes"],
            "reclaimed_bytes": row["reclaimed_bytes"],
            "actions": json.loads(row["actions_json"]),
            "status": row["status"],
            "error": row["error"],
        }
