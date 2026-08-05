import json
from typing import Any

import aiosqlite

AREA_FILTER_UNSET = object()
LATEST_VISIBLE_ANCHOR_ROW_LIMIT = 1000
MAX_FTS_QUERY_CHARS = 500


class TranscriptStore:
    """Transcript browsing, turn navigation, and full-text search."""

    def __init__(self, read_conn: aiosqlite.Connection):
        self._read_conn = read_conn

    @staticmethod
    def _message_row_payload(row: aiosqlite.Row) -> dict:
        return {
            "session_id": row["session_id"],
            "message_id": row["message_id"],
            "seq": row["seq"],
            "role": row["role"],
            "client_id": row["client_id"],
            "created_at": row["created_at"],
            "message": json.loads(row["message_json"]),
        }

    async def list_session_messages(
        self,
        session_id: str,
        limit: int = 100,
        before: str | None = None,
        after: str | None = None,
        around: str | None = None,
        around_seq: int | None = None,
        before_seq: int | None = None,
        after_seq: int | None = None,
        area_id: str | object | None = AREA_FILTER_UNSET,
    ) -> dict:
        if area_id is not AREA_FILTER_UNSET and not await self._session_matches_area(session_id, area_id):
            return {
                "messages": [],
                "has_more_before": False,
                "has_more_after": False,
                "before": None,
                "after": None,
            }
        limit = max(1, min(limit, 250))

        async def seq_for_message(ref: str | None) -> int | None:
            if not ref:
                return None
            rows = await self._read_conn.execute_fetchall(
                """
                SELECT seq FROM session_messages
                WHERE session_id = ? AND (message_id = ? OR client_id = ?)
                LIMIT 1
                """,
                (session_id, ref, ref),
            )
            return int(rows[0]["seq"]) if rows else None

        rows: list[Any]
        around_at = await seq_for_message(around)
        # Raw-int seq cursors (from search hits / prior pages) take precedence
        # over message-id refs when both are somehow supplied.
        before_at = before_seq if before_seq is not None else await seq_for_message(before)
        after_at = after_seq if after_seq is not None else await seq_for_message(after)
        if around_seq is not None:
            start = max(0, around_seq - (limit // 2))
            rows = await self._read_conn.execute_fetchall(
                """
                SELECT * FROM session_messages
                WHERE session_id = ? AND seq >= ?
                ORDER BY seq ASC
                LIMIT ?
                """,
                (session_id, start, limit),
            )
        elif around_at is not None:
            start = max(0, around_at - (limit // 2))
            rows = await self._read_conn.execute_fetchall(
                """
                SELECT * FROM session_messages
                WHERE session_id = ? AND seq >= ?
                ORDER BY seq ASC
                LIMIT ?
                """,
                (session_id, start, limit),
            )
        elif before_at is not None:
            desc_rows = await self._read_conn.execute_fetchall(
                """
                SELECT * FROM session_messages
                WHERE session_id = ? AND seq < ?
                ORDER BY seq DESC
                LIMIT ?
                """,
                (session_id, before_at, limit),
            )
            rows = list(reversed(desc_rows))
        elif after_at is not None:
            rows = await self._read_conn.execute_fetchall(
                """
                SELECT * FROM session_messages
                WHERE session_id = ? AND seq > ?
                ORDER BY seq ASC
                LIMIT ?
                """,
                (session_id, after_at, limit),
            )
        else:
            desc_rows = await self._read_conn.execute_fetchall(
                """
                SELECT * FROM session_messages
                WHERE session_id = ?
                ORDER BY seq DESC
                LIMIT ?
                """,
                (session_id, limit),
            )
            rows = list(reversed(desc_rows))
            rows = await self._latest_rows_with_visible_user_anchor(session_id, rows)

        messages = [self._message_row_payload(row) for row in rows]
        first_seq = messages[0]["seq"] if messages else None
        last_seq = messages[-1]["seq"] if messages else None
        has_more_before = False
        has_more_after = False
        if first_seq is not None:
            has_more_before = bool(
                await self._read_conn.execute_fetchall(
                    "SELECT 1 FROM session_messages WHERE session_id = ? AND seq < ? LIMIT 1",
                    (session_id, first_seq),
                )
            )
        if last_seq is not None:
            has_more_after = bool(
                await self._read_conn.execute_fetchall(
                    "SELECT 1 FROM session_messages WHERE session_id = ? AND seq > ? LIMIT 1",
                    (session_id, last_seq),
                )
            )

        return {
            "messages": messages,
            "has_more_before": has_more_before,
            "has_more_after": has_more_after,
            "before": messages[0]["message_id"] if messages else None,
            "after": messages[-1]["message_id"] if messages else None,
        }

    async def messages_since(self, session_id: str, seq: int) -> list[dict]:
        """Ordered transcript rows with seq > `seq` (oldest-first) for the
        curator. Returns the same `_message_row_payload` shape as list_messages
        (carries `seq`, `role`, parsed `message`)."""
        rows = await self._read_conn.execute_fetchall(
            """
            SELECT * FROM session_messages
            WHERE session_id = ? AND seq > ?
            ORDER BY seq ASC
            """,
            (session_id, seq),
        )
        return [self._message_row_payload(row) for row in rows]

    async def recent_session_scopes(self, limit: int) -> list[dict]:
        """The `limit` most-recently-active live sessions (archived excluded),
        as {session_id, area_id, session_type, origin_automation_id} — the
        curation sweep's worklist (it gates on the origin fields)."""
        rows = await self._read_conn.execute_fetchall(
            """
            SELECT session_id, area_id, session_type, origin_automation_id FROM sessions
            WHERE archived_at IS NULL
            ORDER BY last_activity DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [
            {
                "session_id": row["session_id"],
                "area_id": row["area_id"],
                "session_type": row["session_type"] or "chat",
                "origin_automation_id": row["origin_automation_id"],
            }
            for row in rows
        ]

    async def session_scope(self, session_id: str) -> dict | None:
        """Return one session's scope exactly, independent of sweep recency."""
        rows = await self._read_conn.execute_fetchall(
            """
            SELECT session_id, area_id, session_type, origin_automation_id
            FROM sessions
            WHERE session_id = ?
            """,
            (session_id,),
        )
        if not rows:
            return None
        row = rows[0]
        return {
            "session_id": row["session_id"],
            "area_id": row["area_id"],
            "session_type": row["session_type"] or "chat",
            "origin_automation_id": row["origin_automation_id"],
        }

    async def search_messages(
        self,
        query: str,
        *,
        limit: int = 20,
        offset: int = 0,
        session_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
        area_id: str | object | None = AREA_FILTER_UNSET,
    ) -> dict:
        """Full-text search across transcript messages using SQLite FTS5.

        Returns {hits, has_more}. Each hit carries session_id + session name,
        seq, role, created_at, and a trimmed snippet. Scope to one chat with
        `session_id`; bound by time with ISO `since`/`until`. Empty/whitespace
        query → no hits (rather than an FTS syntax error)."""
        q = query.strip()
        if not q:
            return {"hits": [], "has_more": False}
        # Bound the FTS5 parser input so an oversized/pathological query can't peg
        # a core (it spins without raising, so the except-fallback below won't catch it).
        q = q[:MAX_FTS_QUERY_CHARS]
        limit = max(1, min(limit, 100))
        offset = max(0, offset)

        where = ["session_messages_fts MATCH ?"]
        params: list[Any] = [q]
        if session_id is not None:
            where.append("m.session_id = ?")
            params.append(session_id)
        if since is not None:
            where.append("m.created_at >= ?")
            params.append(since)
        if until is not None:
            where.append("m.created_at <= ?")
            params.append(until)
        if area_id is not AREA_FILTER_UNSET:
            if area_id is None:
                where.append("s.area_id IS NULL")
            else:
                where.append("s.area_id = ?")
                params.append(area_id)

        # One extra row signals a further page.
        fts_sql_limit, fts_sql_offset = limit + 1, offset
        sql = f"""
            SELECT m.session_id AS session_id, s.public_ref AS public_ref, s.name AS session_name,
                   m.seq AS seq, m.role AS role, m.created_at AS created_at,
                   snippet(session_messages_fts, 0, '[', ']', '…', 16) AS snippet
            FROM session_messages_fts
            JOIN session_messages m ON m.rowid = session_messages_fts.rowid
            LEFT JOIN sessions s ON s.session_id = m.session_id
            WHERE {" AND ".join(where)}
            ORDER BY bm25(session_messages_fts), m.created_at DESC
            LIMIT ? OFFSET ?
        """
        params.extend([fts_sql_limit, fts_sql_offset])

        try:
            fts_rows = await self._read_conn.execute_fetchall(sql, tuple(params))
        except Exception:
            # Malformed FTS query (stray operators, unbalanced quotes). Retry
            # as a quoted phrase so user text never surfaces a SQL error.
            phrase = '"' + q.replace('"', '""') + '"'
            params[0] = phrase
            fts_rows = await self._read_conn.execute_fetchall(sql, tuple(params))

        has_more = len(fts_rows) > limit
        hits = [self._search_hit(r) for r in fts_rows[:limit]]
        return {"hits": hits, "has_more": has_more}

    @staticmethod
    def _search_hit(r: Any, snippet: str | None = None) -> dict:
        return {
            "session_id": r["session_id"],
            "public_ref": r["public_ref"],
            "session_name": r["session_name"],
            "seq": r["seq"],
            "role": r["role"],
            "created_at": r["created_at"],
            "snippet": (snippet if snippet is not None else (r["snippet"] or "")).strip(),
        }

    async def _session_matches_area(self, session_id: str, area_id: str | object | None) -> bool:
        rows = await self._read_conn.execute_fetchall(
            "SELECT area_id FROM sessions WHERE session_id = ? LIMIT 1",
            (session_id,),
        )
        return bool(rows) and rows[0]["area_id"] == area_id

    def _row_is_visible_user(self, row: Any) -> bool:
        if row["role"] != "user":
            return False
        try:
            message = json.loads(row["message_json"])
        except Exception:
            return True
        return not bool(message.get("is_meta"))

    async def _latest_rows_with_visible_user_anchor(self, session_id: str, rows: list[Any]) -> list[Any]:
        if not rows:
            return rows

        visible_users = [row for row in rows if self._row_is_visible_user(row)]
        if len(visible_users) >= 2:
            return rows

        if visible_users:
            anchor = visible_users[0]
            previous_anchor = await self._visible_user_before(session_id, anchor["seq"])
            if previous_anchor:
                expanded = await self._bounded_rows_between(
                    session_id,
                    previous_anchor["seq"],
                    rows[-1]["seq"],
                    max_count=LATEST_VISIBLE_ANCHOR_ROW_LIMIT,
                )
                if expanded is not None:
                    return expanded
            return rows

        anchor = await self._visible_user_before(session_id, rows[0]["seq"])
        if not anchor:
            # No visible-user anchor before the window. Automation / channel
            # sessions drive their turns with meta user messages
            # (loop:/bg:/goal:), so a tool-heavy active run leaves the newest
            # window with zero visible anchors. Fall back to the most recent
            # user turn boundary regardless of meta, so prior turns still load
            # instead of dead-ending on the active run's tool stream.
            return await self._expand_from_user_boundary(session_id, rows)

        previous_anchor = await self._visible_user_before(session_id, anchor["seq"])
        if previous_anchor:
            expanded = await self._bounded_rows_between(
                session_id,
                previous_anchor["seq"],
                rows[-1]["seq"],
                max_count=LATEST_VISIBLE_ANCHOR_ROW_LIMIT,
            )
            if expanded is not None:
                return expanded

        expanded = await self._bounded_rows_between(
            session_id,
            anchor["seq"],
            rows[-1]["seq"],
            max_count=LATEST_VISIBLE_ANCHOR_ROW_LIMIT,
        )
        if expanded is not None:
            return expanded
        return rows

    async def _visible_user_before(self, session_id: str, before_seq: int) -> Any | None:
        rows = await self._read_conn.execute_fetchall(
            """
            SELECT * FROM session_messages
            WHERE session_id = ? AND seq < ? AND role = 'user'
            ORDER BY seq DESC
            LIMIT 50
            """,
            (session_id, before_seq),
        )
        for row in rows:
            if self._row_is_visible_user(row):
                return row
        return None

    async def _user_before(self, session_id: str, before_seq: int) -> Any | None:
        """Most recent user row before `before_seq`, meta or not — a turn
        boundary for sessions that have no visible (non-meta) user."""
        rows = await self._read_conn.execute_fetchall(
            """
            SELECT * FROM session_messages
            WHERE session_id = ? AND seq < ? AND role = 'user'
            ORDER BY seq DESC
            LIMIT 1
            """,
            (session_id, before_seq),
        )
        return rows[0] if rows else None

    async def _expand_from_user_boundary(self, session_id: str, rows: list[Any]) -> list[Any]:
        boundary = await self._user_before(session_id, rows[0]["seq"])
        if boundary is None:
            return rows
        # Reach back one further turn boundary so the previous exchange shows,
        # not just the active run's own opening line.
        previous = await self._user_before(session_id, boundary["seq"])
        start_seq = (previous or boundary)["seq"]
        expanded = await self._bounded_rows_between(
            session_id,
            start_seq,
            rows[-1]["seq"],
            max_count=LATEST_VISIBLE_ANCHOR_ROW_LIMIT,
        )
        return expanded if expanded is not None else rows

    async def _bounded_rows_between(
        self,
        session_id: str,
        start_seq: int,
        end_seq: int,
        *,
        max_count: int = 250,
    ) -> list[Any] | None:
        count_rows = await self._read_conn.execute_fetchall(
            """
            SELECT COUNT(*) AS count FROM session_messages
            WHERE session_id = ? AND seq >= ? AND seq <= ?
            """,
            (session_id, start_seq, end_seq),
        )
        if not count_rows or int(count_rows[0]["count"]) > max_count:
            return None
        return await self._read_conn.execute_fetchall(
            """
            SELECT * FROM session_messages
            WHERE session_id = ? AND seq >= ? AND seq <= ?
            ORDER BY seq ASC
            """,
            (session_id, start_seq, end_seq),
        )

    async def list_session_turns(self, session_id: str, limit: int = 100) -> list[dict]:
        rows = await self._read_conn.execute_fetchall(
            """
            SELECT *
            FROM session_turns
            WHERE session_id = ?
            ORDER BY turn_index ASC
            LIMIT ?
            """,
            (session_id, max(1, min(limit, 500))),
        )
        return [
            {
                "session_id": row["session_id"],
                "turn_id": row["turn_id"],
                "turn_index": row["turn_index"],
                "user_message_id": row["user_message_id"],
                "message_start_id": row["message_start_id"],
                "message_end_id": row["message_end_id"],
                "message_start_seq": row["message_start_seq"],
                "message_end_seq": row["message_end_seq"],
                "started_at": row["started_at"],
                "ended_at": row["ended_at"],
            }
            for row in rows
        ]


