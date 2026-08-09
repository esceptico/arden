"""Durable session events and their raw tool-result manifests."""

import asyncio
import hashlib
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import aiosqlite

from arden import database
from arden.agent.types.tool_outcomes import ToolOutcome
from arden.agent.types.tools import ToolResult, ToolResultPayloadError
from arden.constants import (
    RAW_SEARCH_RESULT_RETENTION_DAYS,
    RAW_TOOL_RESULT_INLINE_MAX_BYTES,
    SESSION_EVENT_DURABLE_RETENTION,
    SESSION_EVENT_PRUNE_INTERVAL,
)
from arden.context.errors import SessionDataCorruptionError
from arden.core.raw_tool_results import (
    RawToolResultBlob,
    internal_blob_from_data,
    persist_raw_tool_result,
    preview_text,
    read_raw_tool_result,
    strip_internal_raw_tool_result_data,
)
from arden.core.tool_result_files import (
    persist_result,
    persist_result_payload,
    result_file_path,
    result_payload_file_path,
)
from arden.events.sse import event_from_payload
from arden.logging import get_logger
from arden.server.bus import StreamRecord

_logger = get_logger(__name__)

_DURABLE_TOOL_RESULT_DATA_KEYS = (
    "child_agent",
    "workflow",
    "workflow_id",
    "usage",
    "cost",
    "html",
    "title",
    "mode",
    "provenance",
)


class SessionEventStore:
    """Own event replay, retention, and durable raw-result recovery."""

    def __init__(self, write_conn: aiosqlite.Connection, read_conn: aiosqlite.Connection):
        self._write_conn = write_conn
        self._read_conn = read_conn
        self._write_lock = asyncio.Lock()

    @asynccontextmanager
    async def _transaction(self) -> AsyncIterator[None]:
        try:
            await self._write_conn.execute("BEGIN IMMEDIATE")
            yield
            await self._write_conn.commit()
        except BaseException:
            await database.rollback_safely(self._write_conn)
            raise

    @asynccontextmanager
    async def _locked_transaction(self) -> AsyncIterator[None]:
        async with self._write_lock, self._transaction():
            yield

    @staticmethod
    def active_tool_result_ids(messages: list[dict]) -> set[str]:
        """Return tool-call IDs whose offloaded results remain in active context."""

        refs: set[str] = set()
        for message in messages:
            tool_call_id = message.get("tool_call_id") if message.get("role") == "tool" else None
            if isinstance(tool_call_id, str) and tool_call_id:
                refs.add(tool_call_id)
            compaction = message.get("compaction")
            nested = compaction.get("tool_result_refs") if isinstance(compaction, dict) else None
            if isinstance(nested, list):
                refs.update(ref for ref in nested if isinstance(ref, str) and ref)
        return refs

    @staticmethod
    def _tool_result_id(*, session_id: str, tool_call_id: str, content_sha256: str) -> str:
        seed = f"{session_id}\0{tool_call_id}\0{content_sha256}".encode()
        return f"tr_{hashlib.sha256(seed).hexdigest()[:24]}"

    async def restore_active_tool_result_files(self, session_id: str, messages: list[dict]) -> None:
        """Rehydrate pruned file-read targets from the durable result manifest."""

        missing = {
            tool_call_id: (
                not result_file_path(session_id, tool_call_id).exists(),
                not result_payload_file_path(session_id, tool_call_id).exists(),
            )
            for tool_call_id in self.active_tool_result_ids(messages)
        }
        missing = {tool_call_id: needs for tool_call_id, needs in missing.items() if any(needs)}
        if not missing:
            return
        placeholders = ",".join("?" for _ in missing)
        rows = await self._read_conn.execute_fetchall(
            f"""
            SELECT tool_call_id, blob_path, compression
            FROM tool_results
            WHERE session_id = ?
              AND tool_call_id IN ({placeholders})
            ORDER BY created_at DESC
            """,
            (session_id, *sorted(missing)),
        )
        restored: set[str] = set()
        for row in rows:
            tool_call_id = str(row["tool_call_id"])
            if tool_call_id in restored:
                continue
            try:
                content = await asyncio.to_thread(
                    read_raw_tool_result,
                    row["blob_path"],
                    compression=row["compression"],
                )
                missing_raw, missing_payload = missing[tool_call_id]
                decoded = ToolResult.from_serialized_payload(content)
                if missing_raw:
                    await asyncio.to_thread(
                        persist_result,
                        session_id,
                        tool_call_id,
                        decoded.content if decoded is not None else content,
                    )
                if missing_payload and decoded is not None:
                    await asyncio.to_thread(persist_result_payload, session_id, tool_call_id, content)
            except (OSError, ToolResultPayloadError) as exc:
                raise SessionDataCorruptionError(
                    f"session {session_id} tool result {tool_call_id} cannot be rehydrated"
                ) from exc
            restored.add(tool_call_id)

    async def clone_for_branch(
        self,
        *,
        connection: aiosqlite.Connection,
        source_session_id: str,
        target_session_id: str,
        tool_call_ids: set[str],
        projection: str,
    ) -> None:
        """Clone manifests inside the caller-owned branch transaction."""

        if not tool_call_ids:
            return
        placeholders = ",".join("?" for _ in tool_call_ids)
        rows = await connection.execute_fetchall(
            f"""
            SELECT * FROM tool_results
            WHERE session_id = ? AND tool_call_id IN ({placeholders})
            ORDER BY created_at ASC
            """,
            (source_session_id, *sorted(tool_call_ids)),
        )
        cloned_ids = {str(row["tool_call_id"]) for row in rows}
        missing_offloads = sorted(
            tool_call_id
            for tool_call_id in tool_call_ids - cloned_ids
            if str(result_file_path(target_session_id, tool_call_id)) in projection
            or str(result_payload_file_path(target_session_id, tool_call_id)) in projection
        )
        if missing_offloads:
            raise ValueError(f"tool results unavailable for branch: {', '.join(missing_offloads)}")
        await connection.executemany(
            """
            INSERT OR IGNORE INTO tool_results (
                tool_result_id, session_id, run_id, tool_call_id, tool_name,
                content_sha256, content_bytes, stored_bytes, compression,
                blob_ref, blob_path, preview, retention_class, expires_at,
                source_event_seq, created_at
            )
            VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            [
                (
                    self._tool_result_id(
                        session_id=target_session_id,
                        tool_call_id=str(row["tool_call_id"]),
                        content_sha256=str(row["content_sha256"]),
                    ),
                    target_session_id,
                    row["tool_call_id"],
                    row["tool_name"],
                    row["content_sha256"],
                    row["content_bytes"],
                    row["stored_bytes"],
                    row["compression"],
                    row["blob_ref"],
                    row["blob_path"],
                    row["preview"],
                    row["retention_class"],
                    row["expires_at"],
                    row["created_at"],
                )
                for row in rows
            ],
        )

    async def list_tool_result_content_hashes(self) -> set[str]:
        rows = await self._read_conn.execute_fetchall("SELECT DISTINCT content_sha256 FROM tool_results")
        return {row["content_sha256"] for row in rows}

    async def prune_expired_tool_results(self, *, limit: int = 1000, now: datetime | None = None) -> int:
        """Drop expired, inactive manifests; active context references pin them."""

        if limit <= 0:
            return 0
        cutoff = (now or datetime.now(UTC)).isoformat()
        async with self._locked_transaction():
            cursor = await self._write_conn.execute(
                """
                DELETE FROM tool_results
                WHERE tool_result_id IN (
                    SELECT tool_result_id
                    FROM tool_results
                    WHERE expires_at IS NOT NULL AND expires_at <= ?
                      AND NOT EXISTS (
                          SELECT 1
                          FROM sessions AS active_session
                          WHERE active_session.session_id = tool_results.session_id
                            AND CASE
                                WHEN json_valid(active_session.messages)
                                THEN json_type(active_session.messages) <> 'array'
                                ELSE 1
                            END
                      )
                      AND NOT EXISTS (
                          SELECT 1
                          FROM sessions AS active_session,
                               json_each(
                                   CASE
                                       WHEN json_valid(active_session.messages) THEN active_session.messages
                                       ELSE '[]'
                                   END
                               ) AS active_message
                          WHERE active_session.session_id = tool_results.session_id
                            AND json_extract(active_message.value, '$.role') = 'tool'
                            AND json_extract(active_message.value, '$.tool_call_id') = tool_results.tool_call_id
                      )
                      AND NOT EXISTS (
                          SELECT 1
                          FROM sessions AS active_session,
                               json_each(
                                   CASE
                                       WHEN json_valid(active_session.messages) THEN active_session.messages
                                       ELSE '[]'
                                   END
                               ) AS active_message,
                               json_each(
                                   CASE
                                       WHEN json_type(active_message.value, '$.compaction.tool_result_refs') = 'array'
                                       THEN json_extract(active_message.value, '$.compaction.tool_result_refs')
                                       ELSE '[]'
                                   END
                               ) AS active_ref
                          WHERE active_session.session_id = tool_results.session_id
                            AND CAST(active_ref.value AS TEXT) = tool_results.tool_call_id
                      )
                    ORDER BY expires_at, tool_result_id
                    LIMIT ?
                )
                """,
                (cutoff, limit),
            )
        return cursor.rowcount

    async def get_tool_result_for_call(self, *, run_id: str, tool_call_id: str) -> ToolResult | None:
        now = datetime.now(UTC).isoformat()
        rows = await self._read_conn.execute_fetchall(
            """
            SELECT
                result.*,
                call.outcome_json AS call_outcome_json
            FROM tool_calls AS call
            JOIN tool_results AS result ON result.tool_result_id = call.result_ref
            WHERE call.run_id = ? AND call.tool_call_id = ?
              AND (result.expires_at IS NULL OR result.expires_at > ?)
            ORDER BY result.created_at DESC
            LIMIT 1
            """,
            (run_id, tool_call_id, now),
        )
        if not rows:
            return None
        row = rows[0]
        content = await asyncio.to_thread(read_raw_tool_result, row["blob_path"], compression=row["compression"])
        if hashlib.sha256(content.encode("utf-8")).hexdigest() != row["content_sha256"]:
            raise ToolResultPayloadError("durable tool result content hash does not match its manifest")
        decoded = ToolResult.from_serialized_payload(content)
        if decoded is None:
            raise ToolResultPayloadError("durable tool result is not a typed ToolResult envelope")
        if decoded.outcome is not None or not row["call_outcome_json"]:
            return decoded
        outcome = ToolOutcome.decode(json.loads(row["call_outcome_json"]))
        try:
            return replace(decoded, outcome=outcome)
        except ValueError as exc:
            raise ToolResultPayloadError("durable tool result conflicts with its recorded outcome") from exc

    @staticmethod
    def _raw_tool_result_pointer_content(*, preview: str, tool_result_id: str, content_bytes: int) -> str:
        prefix = preview_text(preview)
        note = f"[Full raw tool result stored as {tool_result_id}; {content_bytes} bytes.]"
        return f"{prefix}\n\n{note}" if prefix else note

    @staticmethod
    def _strip_empty_raw_tool_result_fields(payload: dict) -> dict:
        for key in ("raw_ref", "content_sha256", "content_bytes", "retention_class"):
            if payload.get(key) is None:
                payload.pop(key, None)
        return payload

    @staticmethod
    def _allowlisted_durable_tool_result_data(data: dict | None) -> dict | None:
        if not isinstance(data, dict):
            return None
        allowed = {key: data[key] for key in _DURABLE_TOOL_RESULT_DATA_KEYS if key in data}
        return allowed or None

    @classmethod
    def _prepare_durable_tool_result_payload(
        cls,
        *,
        record: StreamRecord,
        payload: dict,
        now: str,
    ) -> tuple[dict, tuple | None, tuple | None]:
        if payload.get("type") != "TOOL_CALL_RESULT":
            return payload, None, None

        payload = dict(payload)
        content = payload.get("content")
        blob: RawToolResultBlob | None = internal_blob_from_data(payload.get("data"))
        cleaned_data = strip_internal_raw_tool_result_data(payload.get("data"))
        cleaned_data = cls._allowlisted_durable_tool_result_data(cleaned_data)
        if cleaned_data is None:
            payload.pop("data", None)
        else:
            payload["data"] = cleaned_data

        tool_call_id = payload.get("tool_call_id")
        if not isinstance(tool_call_id, str) or not tool_call_id:
            return cls._strip_empty_raw_tool_result_fields(payload), None, None

        if blob is None:
            if not isinstance(content, str) or len(content.encode("utf-8")) <= RAW_TOOL_RESULT_INLINE_MAX_BYTES:
                return cls._strip_empty_raw_tool_result_fields(payload), None, None
            blob = persist_raw_tool_result(content)
            preview = payload.get("preview") if isinstance(payload.get("preview"), str) else ""
            preview = preview or preview_text(content)
            payload["preview"] = preview_text(preview)
            payload["content"] = cls._raw_tool_result_pointer_content(
                preview=preview,
                tool_result_id=cls._tool_result_id(
                    session_id=record.session_id,
                    tool_call_id=tool_call_id,
                    content_sha256=blob.content_sha256,
                ),
                content_bytes=blob.content_bytes,
            )

        tool_result_id = cls._tool_result_id(
            session_id=record.session_id,
            tool_call_id=tool_call_id,
            content_sha256=blob.content_sha256,
        )
        run_id = payload.get("run_id") if isinstance(payload.get("run_id"), str) else None
        preview = payload.get("preview") if isinstance(payload.get("preview"), str) else ""
        tool_name = payload.get("name") if isinstance(payload.get("name"), str) else None
        if tool_name == "file_search_text":
            retention_class = "search_temporary"
            expires_at = (datetime.fromisoformat(now) + timedelta(days=RAW_SEARCH_RESULT_RETENTION_DAYS)).isoformat()
        else:
            retention_class = "session"
            expires_at = None
        payload["raw_ref"] = tool_result_id
        payload.pop("content_sha256", None)
        payload["content_bytes"] = blob.content_bytes
        payload["retention_class"] = retention_class

        tool_result_row = (
            tool_result_id,
            record.session_id,
            run_id,
            tool_call_id,
            tool_name,
            blob.content_sha256,
            blob.content_bytes,
            blob.stored_bytes,
            blob.compression,
            blob.blob_ref,
            blob.blob_path,
            preview_text(preview),
            retention_class,
            expires_at,
            record.seq,
            now,
        )
        return payload, tool_result_row, (tool_result_id, record.session_id, tool_call_id)

    async def record_session_event(self, record: StreamRecord) -> None:
        await self.record_session_events([record])

    async def record_session_events(self, records: list[StreamRecord]) -> None:
        """Persist a durable event batch with one commit."""

        if not records:
            return

        def _build_rows() -> tuple[list[tuple], list[tuple], list[tuple], dict[str, int]]:
            now = datetime.now(UTC).isoformat()
            rows: list[tuple] = []
            tool_result_rows: list[tuple] = []
            tool_call_ref_updates: list[tuple] = []
            per_session: dict[str, int] = {}
            for record in records:
                sse = record.event.to_sse()
                payload = json.loads(sse["data"])
                payload, tool_result_row, tool_call_ref_update = self._prepare_durable_tool_result_payload(
                    record=record,
                    payload=payload,
                    now=now,
                )
                if tool_result_row is not None:
                    tool_result_rows.append(tool_result_row)
                if tool_call_ref_update is not None:
                    tool_call_ref_updates.append(tool_call_ref_update)
                run_id = payload.get("run_id") if isinstance(payload.get("run_id"), str) else None
                rows.append(
                    (
                        record.session_id,
                        record.seq,
                        str(payload.get("type") or sse["event"]),
                        json.dumps(payload, default=str),
                        run_id,
                        now,
                    )
                )
                per_session[record.session_id] = per_session.get(record.session_id, 0) + 1
            return rows, tool_result_rows, tool_call_ref_updates, per_session

        rows, tool_result_rows, tool_call_ref_updates, per_session = await asyncio.to_thread(_build_rows)
        async with self._write_lock:
            async with self._transaction():
                if tool_result_rows:
                    await self._write_conn.executemany(
                        """
                        INSERT OR IGNORE INTO tool_results (
                            tool_result_id, session_id, run_id, tool_call_id, tool_name,
                            content_sha256, content_bytes, stored_bytes, compression,
                            blob_ref, blob_path, preview, retention_class, expires_at,
                            source_event_seq, created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        tool_result_rows,
                    )
                if tool_call_ref_updates:
                    await self._write_conn.executemany(
                        """
                        UPDATE tool_calls
                        SET result_ref = ?
                        WHERE session_id = ? AND tool_call_id = ?
                        """,
                        tool_call_ref_updates,
                    )
                await self._write_conn.executemany(
                    """
                    INSERT INTO session_events (
                        session_id, seq, event_type, event_json, run_id, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    rows,
                )
                await self._write_conn.executemany(
                    """
                    INSERT INTO session_event_retention_state(session_id, writes_since_prune)
                    VALUES (?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        writes_since_prune = writes_since_prune + excluded.writes_since_prune
                    """,
                    tuple(per_session.items()),
                )
            try:
                due = await self._write_conn.execute_fetchall(
                    f"""
                    SELECT session_id
                    FROM session_event_retention_state
                    WHERE session_id IN ({",".join("?" for _ in per_session)})
                      AND writes_since_prune >= ?
                    """,
                    (*per_session, SESSION_EVENT_PRUNE_INTERVAL),
                )
                for row in due:
                    async with self._transaction():
                        await self._prune_session_events(row["session_id"], SESSION_EVENT_DURABLE_RETENTION)
            except Exception:
                _logger.exception(
                    "session_event_retention_failed",
                    session_ids=sorted(per_session),
                )

    async def prune_session_events(self, session_id: str, keep: int = SESSION_EVENT_DURABLE_RETENTION) -> int:
        async with self._locked_transaction():
            return await self._prune_session_events(session_id, keep)

    async def _prune_session_events(self, session_id: str, keep: int) -> int:
        cursor = await self._write_conn.execute(
            """
            DELETE FROM session_events
            WHERE session_id = ?
              AND seq <= (
                SELECT seq FROM session_events
                WHERE session_id = ?
                ORDER BY seq DESC
                LIMIT 1 OFFSET ?
              )
            """,
            (session_id, session_id, keep),
        )
        await self._write_conn.execute(
            """
            INSERT INTO session_event_retention_state(session_id, writes_since_prune)
            VALUES (?, 0)
            ON CONFLICT(session_id) DO UPDATE SET writes_since_prune = 0
            """,
            (session_id,),
        )
        return cursor.rowcount

    async def reconcile_due_session_event_retention(self) -> int:
        rows = await self._read_conn.execute_fetchall(
            """
            SELECT session_id
            FROM session_event_retention_state
            WHERE writes_since_prune >= ?
            ORDER BY session_id
            """,
            (SESSION_EVENT_PRUNE_INTERVAL,),
        )
        deleted = 0
        for row in rows:
            deleted += await self.prune_session_events(row["session_id"], SESSION_EVENT_DURABLE_RETENTION)
        return deleted

    async def list_session_events(
        self,
        session_id: str,
        *,
        after_seq: int = 0,
        limit: int = 10000,
    ) -> list[StreamRecord]:
        rows = await self._read_conn.execute_fetchall(
            """
            SELECT seq, event_json
            FROM session_events
            WHERE session_id = ? AND seq > ?
            ORDER BY seq ASC
            LIMIT ?
            """,
            (session_id, after_seq, limit),
        )
        return [
            StreamRecord(
                seq=row["seq"],
                session_id=session_id,
                event=event_from_payload(json.loads(row["event_json"])),
            )
            for row in rows
        ]

    async def get_latest_session_event_seq(self, session_id: str) -> int:
        rows = await self._read_conn.execute_fetchall(
            "SELECT COALESCE(MAX(seq), 0) AS latest_seq FROM session_events WHERE session_id = ?",
            (session_id,),
        )
        return int(rows[0]["latest_seq"] or 0)

    async def get_latest_session_checkpoint_seq(self, session_id: str) -> int:
        rows = await self._read_conn.execute_fetchall(
            "SELECT COALESCE(MAX(last_seq), 0) AS latest_seq "
            "FROM chat_runs WHERE session_id = ? AND last_seq IS NOT NULL",
            (session_id,),
        )
        return int(rows[0]["latest_seq"] or 0)
