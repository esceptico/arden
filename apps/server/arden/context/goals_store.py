import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from uuid import uuid4

import aiosqlite

SessionLock = Callable[[str], Awaitable[asyncio.Lock]]


class GoalStore:
    """Persistence boundary for session goals and todo state."""

    def __init__(
        self,
        conn: aiosqlite.Connection,
        read_conn: aiosqlite.Connection,
        session_lock: SessionLock,
    ):
        self._conn = conn
        self._read_conn = read_conn
        self._session_lock = session_lock

    @staticmethod
    def _payload(row: aiosqlite.Row) -> dict:
        return {
            "session_id": row["session_id"],
            "goal_id": row["goal_id"],
            "objective": row["objective"],
            "status": row["status"],
            "evidence": json.loads(row["evidence_json"] or "[]"),
            "blocked_reason": row["blocked_reason"],
            "token_budget": row["token_budget"],
            "tokens_used": int(row["tokens_used"] or 0),
            "time_used_seconds": int(row["time_used_seconds"] or 0),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    async def set(self, session_id: str, objective: str, *, token_budget: int | None = None) -> dict:
        lock = await self._session_lock(session_id)
        async with lock:
            now = datetime.now(UTC).isoformat()
            goal_id = uuid4().hex
            await self._conn.execute(
                """
                INSERT INTO session_goals (
                    session_id, goal_id, objective, status, evidence_json,
                    blocked_reason, token_budget, tokens_used, time_used_seconds,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, 'active', '[]', NULL, ?, 0, 0, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    goal_id = excluded.goal_id,
                    objective = excluded.objective,
                    status = excluded.status,
                    evidence_json = excluded.evidence_json,
                    blocked_reason = NULL,
                    token_budget = excluded.token_budget,
                    tokens_used = 0,
                    time_used_seconds = 0,
                    created_at = excluded.created_at,
                    updated_at = excluded.updated_at
                """,
                (session_id, goal_id, objective, token_budget, now, now),
            )
            await self._conn.commit()
            goal = await self.get(session_id)
            if goal is None:
                raise RuntimeError("goal insert failed")
            return goal

    async def get(self, session_id: str) -> dict | None:
        rows = await self._read_conn.execute_fetchall(
            "SELECT * FROM session_goals WHERE session_id = ?",
            (session_id,),
        )
        return self._payload(rows[0]) if rows else None

    async def set_todo_override(self, session_id: str, items: list[dict], explanation: str | None = None) -> dict:
        return await self._set_todos("session_todo_overrides", session_id, items, explanation)

    async def get_todo_override(self, session_id: str) -> dict | None:
        return await self._get_todos("session_todo_overrides", session_id)

    async def clear_todo_override(self, session_id: str) -> bool:
        return await self._clear_todos("session_todo_overrides", session_id)

    async def set_session_todos(self, session_id: str, items: list[dict], explanation: str | None = None) -> dict:
        return await self._set_todos("session_todos", session_id, items, explanation)

    async def get_session_todos(self, session_id: str) -> dict | None:
        return await self._get_todos("session_todos", session_id)

    async def clear_session_todos(self, session_id: str) -> bool:
        return await self._clear_todos("session_todos", session_id)

    async def _set_todos(
        self,
        table: str,
        session_id: str,
        items: list[dict],
        explanation: str | None,
    ) -> dict:
        now = datetime.now(UTC).isoformat()
        lock = await self._session_lock(session_id)
        async with lock:
            await self._conn.execute(
                f"""
                INSERT INTO {table} (session_id, items_json, explanation, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    items_json = excluded.items_json,
                    explanation = excluded.explanation,
                    updated_at = excluded.updated_at
                """,
                (session_id, json.dumps(items), explanation, now),
            )
            await self._conn.commit()
        return {"items": items, "explanation": explanation, "updated_at": now}

    async def _get_todos(self, table: str, session_id: str) -> dict | None:
        rows = await self._read_conn.execute_fetchall(
            f"SELECT items_json, explanation, updated_at FROM {table} WHERE session_id = ?",
            (session_id,),
        )
        if not rows:
            return None
        row = rows[0]
        return {
            "items": json.loads(row["items_json"]),
            "explanation": row["explanation"],
            "updated_at": row["updated_at"],
        }

    async def _clear_todos(self, table: str, session_id: str) -> bool:
        lock = await self._session_lock(session_id)
        async with lock:
            cursor = await self._conn.execute(f"DELETE FROM {table} WHERE session_id = ?", (session_id,))
            await self._conn.commit()
            return cursor.rowcount > 0

    async def clear(self, session_id: str) -> bool:
        lock = await self._session_lock(session_id)
        async with lock:
            cursor = await self._conn.execute("DELETE FROM session_goals WHERE session_id = ?", (session_id,))
            await self._conn.commit()
            return cursor.rowcount > 0

    async def update(
        self,
        session_id: str,
        *,
        goal_id: str | None = None,
        status: str | None = None,
        evidence: str | None = None,
        blocked_reason: str | None = None,
        evidence_kind: str | None = None,
        evidence_blocked_reason: str | None = None,
        tokens_used_delta: int = 0,
        time_used_seconds_delta: int = 0,
    ) -> dict | None:
        lock = await self._session_lock(session_id)
        async with lock:
            current = await self.get(session_id)
            if current is None or (goal_id is not None and current["goal_id"] != goal_id):
                return None
            next_evidence = list(current["evidence"])
            if evidence:
                entry = {"text": evidence, "created_at": datetime.now(UTC).isoformat()}
                if evidence_kind:
                    entry["kind"] = evidence_kind
                if evidence_blocked_reason:
                    entry["blocked_reason"] = evidence_blocked_reason
                next_evidence.append(entry)
            next_status = status or current["status"]
            next_tokens_used = current["tokens_used"] + max(0, tokens_used_delta)
            if (
                status is None
                and current.get("token_budget")
                and next_tokens_used >= current["token_budget"]
                and current["status"] == "active"
            ):
                next_status = "budget_limited"
            await self._conn.execute(
                """
                UPDATE session_goals
                SET status = ?, evidence_json = ?, blocked_reason = ?, tokens_used = tokens_used + ?,
                    time_used_seconds = time_used_seconds + ?, updated_at = ?
                WHERE session_id = ?
                """,
                (
                    next_status,
                    json.dumps(next_evidence),
                    blocked_reason if next_status == "blocked" else None,
                    max(0, tokens_used_delta),
                    max(0, time_used_seconds_delta),
                    datetime.now(UTC).isoformat(),
                    session_id,
                ),
            )
            await self._conn.commit()
            return await self.get(session_id)
