import json
from datetime import UTC, datetime
from uuid import uuid4

import aiosqlite

from arden.areas.paths import normalize_area_page_path
from arden.core.public_refs import public_ref

AREA_PATCH_UNSET = object()


class AreaStore:
    """Persistence boundary for Area records."""

    def __init__(self, conn: aiosqlite.Connection, read_conn: aiosqlite.Connection):
        self._conn = conn
        self._read_conn = read_conn

    @staticmethod
    def _normalize_cwd(default_cwd: str | None) -> str | None:
        cwd = default_cwd.strip() if default_cwd else ""
        return cwd or None

    @staticmethod
    def _name_key(name: str) -> str:
        return name.strip().casefold()

    @staticmethod
    def _payload(row: aiosqlite.Row) -> dict:
        return {
            "area_id": row["area_id"],
            "area_ref": row["area_ref"],
            "name": row["name"],
            "default_cwd": (json.loads(row["default_cwds"] or "[]") or [None])[0],
            "instructions": row["instructions"],
            "page_path": row["page_path"],
            "page_id": row["page_id"],
            "autonomy": row["autonomy"],
            "attention": row["attention"] or "ambient",
            "interrupts": row["interrupts"] or "asks",
            "paused_at": row["paused_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "archived_at": row["archived_at"],
        }

    async def _assert_name_available(self, name_key: str, *, exclude_area_id: str | None = None) -> None:
        sql = "SELECT area_id FROM areas WHERE name_key = ? AND archived_at IS NULL"
        params: list[object] = [name_key]
        if exclude_area_id is not None:
            sql += " AND area_id != ?"
            params.append(exclude_area_id)
        rows = await self._read_conn.execute_fetchall(sql, tuple(params))
        if rows:
            raise ValueError("An active Area with that name already exists")

    async def _assert_page_available(self, page_path: str | None, *, exclude_area_id: str | None = None) -> None:
        if page_path is None:
            return
        sql = "SELECT area_id FROM areas WHERE page_path = ?"
        params: list[object] = [page_path]
        if exclude_area_id is not None:
            sql += " AND area_id != ?"
            params.append(exclude_area_id)
        rows = await self._read_conn.execute_fetchall(sql, tuple(params))
        if rows:
            raise ValueError("That page is already attached to another Area")

    async def _assert_page_id_available(self, page_id: str | None, *, exclude_area_id: str | None = None) -> None:
        if page_id is None:
            return
        sql = "SELECT area_id FROM areas WHERE page_id = ?"
        params: list[object] = [page_id]
        if exclude_area_id is not None:
            sql += " AND area_id != ?"
            params.append(exclude_area_id)
        rows = await self._read_conn.execute_fetchall(sql, tuple(params))
        if rows:
            raise ValueError("That page is already attached to another Area")

    async def create(
        self,
        *,
        name: str,
        default_cwd: str | None = None,
        instructions: str | None = None,
        page_path: str | None = None,
        autonomy: str | None = None,
    ) -> dict:
        trimmed_name = name.strip()
        if not trimmed_name:
            raise ValueError("Area name cannot be blank")
        name_key = self._name_key(trimmed_name)
        normalized_page_path = None if page_path is None else normalize_area_page_path(page_path)
        await self._assert_name_available(name_key)
        await self._assert_page_available(normalized_page_path)
        area_id = f"area_{uuid4().hex[:12]}"
        now = datetime.now(UTC).isoformat()
        normalized_cwd = self._normalize_cwd(default_cwd)
        await self._conn.execute(
            """
            INSERT INTO areas (
                area_id, area_ref, name, name_key, default_cwds, instructions,
                page_path, page_id, autonomy, created_at, updated_at, archived_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, NULL)
            """,
            (
                area_id,
                public_ref(trimmed_name, area_id, empty_slug="area"),
                trimmed_name,
                name_key,
                json.dumps([normalized_cwd] if normalized_cwd else []),
                instructions.strip() if instructions and instructions.strip() else None,
                normalized_page_path,
                autonomy,
                now,
                now,
            ),
        )
        await self._conn.commit()
        area = await self.get(area_id)
        if area is None:
            raise RuntimeError("area insert failed")
        return area

    async def get(self, area_id: str | None) -> dict | None:
        if not area_id:
            return None
        rows = await self._read_conn.execute_fetchall(
            "SELECT * FROM areas WHERE area_id = ? AND archived_at IS NULL",
            (area_id,),
        )
        return self._payload(rows[0]) if rows else None

    async def get_by_ref(self, area_ref: str) -> dict | None:
        rows = await self._read_conn.execute_fetchall(
            "SELECT * FROM areas WHERE area_ref = ? AND archived_at IS NULL",
            (area_ref,),
        )
        return self._payload(rows[0]) if rows else None

    async def find_by_page_id(self, page_id: str) -> dict | None:
        rows = await self._read_conn.execute_fetchall(
            "SELECT * FROM areas WHERE page_id = ? AND archived_at IS NULL",
            (page_id,),
        )
        return self._payload(rows[0]) if rows else None

    async def list_active(self) -> list[dict]:
        rows = await self._read_conn.execute_fetchall(
            """
            SELECT * FROM areas
            WHERE archived_at IS NULL
            ORDER BY updated_at DESC, created_at DESC
            """
        )
        return [self._payload(row) for row in rows]

    async def update(
        self,
        area_id: str,
        *,
        name: str | object = AREA_PATCH_UNSET,
        default_cwd: str | object | None = AREA_PATCH_UNSET,
        instructions: str | object | None = AREA_PATCH_UNSET,
        page_path: str | object | None = AREA_PATCH_UNSET,
        page_id: str | object | None = AREA_PATCH_UNSET,
        autonomy: str | object | None = AREA_PATCH_UNSET,
        attention: str | object = AREA_PATCH_UNSET,
        interrupts: str | object = AREA_PATCH_UNSET,
        paused: bool | object = AREA_PATCH_UNSET,
    ) -> dict | None:
        assignments = ["updated_at = ?"]
        params: list[object] = [datetime.now(UTC).isoformat()]
        if name is not AREA_PATCH_UNSET:
            trimmed_name = str(name).strip()
            if not trimmed_name:
                raise ValueError("Area name cannot be blank")
            name_key = self._name_key(trimmed_name)
            await self._assert_name_available(name_key, exclude_area_id=area_id)
            assignments.extend(("name = ?", "name_key = ?"))
            params.extend((trimmed_name, name_key))
        if default_cwd is not AREA_PATCH_UNSET:
            assignments.append("default_cwds = ?")
            cwd = self._normalize_cwd(default_cwd if isinstance(default_cwd, str) else None)
            params.append(json.dumps([cwd] if cwd else []))
        if instructions is not AREA_PATCH_UNSET:
            assignments.append("instructions = ?")
            text = instructions if isinstance(instructions, str) else None
            params.append(text.strip() if text and text.strip() else None)
        if page_path is not AREA_PATCH_UNSET:
            normalized_page_path = normalize_area_page_path(page_path) if isinstance(page_path, str) else None
            await self._assert_page_available(normalized_page_path, exclude_area_id=area_id)
            assignments.append("page_path = ?")
            params.append(normalized_page_path)
        if page_id is not AREA_PATCH_UNSET:
            if page_id is not None and (not isinstance(page_id, str) or not page_id.strip()):
                raise ValueError("page_id must be a nonempty string")
            await self._assert_page_id_available(page_id, exclude_area_id=area_id)
            assignments.append("page_id = ?")
            params.append(page_id)
        if autonomy is not AREA_PATCH_UNSET:
            assignments.append("autonomy = ?")
            params.append(autonomy if isinstance(autonomy, str) else None)
        if attention is not AREA_PATCH_UNSET:
            if attention not in ("dormant", "ambient", "active"):
                raise ValueError("attention must be dormant | ambient | active")
            assignments.append("attention = ?")
            params.append(attention)
        if interrupts is not AREA_PATCH_UNSET:
            if interrupts not in ("asks", "all", "none"):
                raise ValueError("interrupts must be asks | all | none")
            assignments.append("interrupts = ?")
            params.append(interrupts)
        if paused is not AREA_PATCH_UNSET:
            assignments.append("paused_at = ?")
            params.append(datetime.now(UTC).isoformat() if paused else None)
        params.append(area_id)
        cursor = await self._conn.execute(
            f"UPDATE areas SET {', '.join(assignments)} WHERE area_id = ? AND archived_at IS NULL",
            tuple(params),
        )
        await self._conn.commit()
        return await self.get(area_id) if cursor.rowcount else None

    async def archive(self, area_id: str) -> bool:
        now = datetime.now(UTC).isoformat()
        cursor = await self._conn.execute(
            "UPDATE areas SET archived_at = ?, updated_at = ? WHERE area_id = ? AND archived_at IS NULL",
            (now, now, area_id),
        )
        await self._conn.commit()
        return cursor.rowcount > 0

    async def restore(self, area_id: str) -> dict | None:
        rows = await self._read_conn.execute_fetchall(
            "SELECT * FROM areas WHERE area_id = ? AND archived_at IS NOT NULL",
            (area_id,),
        )
        if not rows:
            return None
        await self._assert_name_available(rows[0]["name_key"], exclude_area_id=area_id)
        now = datetime.now(UTC).isoformat()
        cursor = await self._conn.execute(
            "UPDATE areas SET archived_at = NULL, updated_at = ? WHERE area_id = ? AND archived_at IS NOT NULL",
            (now, area_id),
        )
        await self._conn.commit()
        return await self.get(area_id) if cursor.rowcount else None
