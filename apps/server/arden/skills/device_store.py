from dataclasses import dataclass
from datetime import UTC, datetime

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS device_skills (
    executor_id TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    body TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (executor_id, name)
);
"""


@dataclass(frozen=True)
class DeviceSkill:
    executor_id: str
    name: str
    description: str
    sha256: str
    body: str | None
    updated_at: datetime


def _now() -> str:
    return datetime.now(UTC).isoformat()


class DeviceSkillStore:
    """Server-side cache of skills that live on an executor device.

    The device advertises {name, description, sha256} snapshots; bodies sync
    lazily for entries whose hash the server has not seen. The cache is what
    keeps device-authored skills usable while the device is offline.
    """

    def __init__(self, conn: aiosqlite.Connection):
        self._conn = conn

    async def init_schema(self) -> None:
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()

    async def replace_snapshot(self, executor_id: str, skills: list[dict]) -> list[str]:
        """Apply an advertised snapshot; returns names whose body is needed.

        The snapshot is authoritative for this executor: names absent from it
        are deleted (deletions propagate, unlike upload-style syncs). A body
        is kept only while its sha256 still matches.
        """
        advertised = {skill["name"]: skill for skill in skills}
        cursor = await self._conn.execute(
            "SELECT name, sha256, body FROM device_skills WHERE executor_id = ?",
            (executor_id,),
        )
        existing = {row[0]: (row[1], row[2]) for row in await cursor.fetchall()}

        await self._conn.execute(
            f"""
            DELETE FROM device_skills
            WHERE executor_id = ? AND name NOT IN ({",".join("?" * len(advertised)) or "''"})
            """,
            (executor_id, *advertised),
        )
        needed = []
        for name, skill in advertised.items():
            current = existing.get(name)
            body = current[1] if current and current[0] == skill["sha256"] else None
            if body is None:
                needed.append(name)
            await self._conn.execute(
                """
                INSERT INTO device_skills (executor_id, name, description, sha256, body, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (executor_id, name) DO UPDATE
                SET description = excluded.description, sha256 = excluded.sha256,
                    body = excluded.body, updated_at = excluded.updated_at
                """,
                (executor_id, name, skill["description"], skill["sha256"], body, _now()),
            )
        await self._conn.commit()
        return sorted(needed)

    async def save_bodies(self, executor_id: str, bodies: list[dict]) -> None:
        """Store full SKILL.md contents; ignored unless the sha still matches
        the advertised snapshot (a stale upload must not clobber a newer one)."""
        for entry in bodies:
            await self._conn.execute(
                """
                UPDATE device_skills
                SET body = ?, updated_at = ?
                WHERE executor_id = ? AND name = ? AND sha256 = ?
                """,
                (entry["body"], _now(), executor_id, entry["name"], entry["sha256"]),
            )
        await self._conn.commit()

    async def all_loaded(self) -> list[DeviceSkill]:
        """Every cached skill with a body, newest first per name across executors."""
        cursor = await self._conn.execute(
            "SELECT * FROM device_skills WHERE body IS NOT NULL ORDER BY updated_at DESC",
        )
        cursor.row_factory = aiosqlite.Row
        rows = await cursor.fetchall()
        seen: set[str] = set()
        skills = []
        for row in rows:
            if row["name"] in seen:
                continue
            seen.add(row["name"])
            skills.append(
                DeviceSkill(
                    executor_id=row["executor_id"],
                    name=row["name"],
                    description=row["description"],
                    sha256=row["sha256"],
                    body=row["body"],
                    updated_at=datetime.fromisoformat(row["updated_at"]),
                )
            )
        return skills

    async def drop_executor(self, executor_id: str) -> None:
        await self._conn.execute("DELETE FROM device_skills WHERE executor_id = ?", (executor_id,))
        await self._conn.commit()
