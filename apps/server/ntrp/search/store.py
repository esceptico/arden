import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

import aiosqlite

from ntrp.logging import get_logger
from ntrp.search.fts import build_fts_or_query
from ntrp.search.migrations import run_migrations

_logger = get_logger(__name__)

# Bump when the items_vec schema changes (forces a rebuild + full re-embed).
# v2: added a `source` partition key so per-source KNN doesn't starve small
# partitions when another source contains many more vectors.
_VEC_SCHEMA_VERSION = "2"

SNIPPET_DISPLAY_LIMIT = 500


@dataclass
class Item:
    id: int
    source: str
    source_id: str
    title: str
    content: str | None
    snippet: str | None
    content_hash: str
    embedding: bytes | None
    metadata: dict | None
    indexed_at: str


class SearchStore:
    def __init__(self, conn: aiosqlite.Connection, embedding_dim: int):
        self.conn = conn
        self.embedding_dim = embedding_dim
        self._has_fts = False
        self._has_vec = False

    async def init_schema(self) -> None:
        await self._check_integrity()

        await self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS items (
                id INTEGER PRIMARY KEY,
                source TEXT NOT NULL,
                source_id TEXT NOT NULL,
                title TEXT,
                content TEXT,
                snippet TEXT,
                content_hash TEXT,
                metadata TEXT,
                indexed_at TEXT,
                UNIQUE(source, source_id)
            );

            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_items_source ON items(source);
            CREATE INDEX IF NOT EXISTS idx_items_hash ON items(source, content_hash);
        """)

        # Rebuild the vec table (and force a full re-embed) when the embedding dim
        # OR the vec schema version changed. v2 adds the `source` partition key.
        stored_dim = await self._get_meta("embedding_dim")
        stored_vec_ver = await self._get_meta("vec_schema_version")
        dim_changed = stored_dim is not None and int(stored_dim) != self.embedding_dim
        ver_changed = stored_vec_ver != _VEC_SCHEMA_VERSION
        if dim_changed or ver_changed:
            _logger.info("rebuilding vec table (dim_changed=%s ver_changed=%s) — full re-embed", dim_changed, ver_changed)
            await self.conn.execute("DROP TABLE IF EXISTS items_vec")
            await self.conn.execute("DELETE FROM items")
            await self.conn.commit()

        try:
            await self.conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS items_fts USING fts5(
                    title, content,
                    content='items',
                    content_rowid='id'
                );
            """)
            await self.conn.executescript("""
                CREATE TRIGGER IF NOT EXISTS items_ai AFTER INSERT ON items BEGIN
                    INSERT INTO items_fts(rowid, title, content)
                    VALUES (new.id, new.title, new.content);
                END;

                CREATE TRIGGER IF NOT EXISTS items_ad AFTER DELETE ON items BEGIN
                    INSERT INTO items_fts(items_fts, rowid, title, content)
                    VALUES ('delete', old.id, old.title, old.content);
                END;

                CREATE TRIGGER IF NOT EXISTS items_au AFTER UPDATE ON items BEGIN
                    INSERT INTO items_fts(items_fts, rowid, title, content)
                    VALUES ('delete', old.id, old.title, old.content);
                    INSERT INTO items_fts(rowid, title, content)
                    VALUES (new.id, new.title, new.content);
                END;
            """)
            self._has_fts = True
        except Exception:
            self._has_fts = False

        try:
            await self.conn.execute(f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS items_vec USING vec0(
                    item_id INTEGER PRIMARY KEY,
                    embedding float[{self.embedding_dim}] distance_metric=cosine,
                    source text partition key
                );
            """)
            self._has_vec = True
        except Exception as e:
            _logger.warning("Failed to create vec0 table: %s", e)
            self._has_vec = False

        await self._set_meta("embedding_dim", str(self.embedding_dim))
        await self._set_meta("vec_schema_version", _VEC_SCHEMA_VERSION)
        await run_migrations(self.conn)
        await self.conn.commit()

    async def _get_meta(self, key: str) -> str | None:
        try:
            rows = await self.conn.execute_fetchall("SELECT value FROM meta WHERE key = ?", (key,))
            return rows[0][0] if rows else None
        except Exception:
            return None

    async def _set_meta(self, key: str, value: str) -> None:
        await self.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))

    async def rebuild_vec_table(self, new_dim: int) -> None:
        self.embedding_dim = new_dim
        await self.conn.execute("DROP TABLE IF EXISTS items_vec")
        try:
            await self.conn.execute(f"""
                CREATE VIRTUAL TABLE items_vec USING vec0(
                    item_id INTEGER PRIMARY KEY,
                    embedding float[{self.embedding_dim}] distance_metric=cosine,
                    source text partition key
                );
            """)
            self._has_vec = True
        except Exception as e:
            _logger.warning("Failed to recreate vec0 table: %s", e)
            self._has_vec = False

        await self._set_meta("embedding_dim", str(self.embedding_dim))
        await self.conn.commit()

    async def _check_integrity(self) -> None:
        try:
            rows = await self.conn.execute_fetchall("PRAGMA integrity_check;")
            if not rows or rows[0][0] != "ok":
                raise RuntimeError("Integrity check failed")
        except Exception:
            _logger.warning("Search DB integrity check failed")
            raise

    @staticmethod
    def hash_content(content: str) -> str:
        return hashlib.md5(content.encode()).hexdigest()

    @staticmethod
    def make_snippet(content: str) -> str:
        return content.replace("\n", " ").strip()[:SNIPPET_DISPLAY_LIMIT]

    async def get_by_id(self, row_id: int) -> Item | None:
        rows = await self.conn.execute_fetchall(
            """SELECT id, source, source_id, title, content, snippet,
                      content_hash, metadata, indexed_at
               FROM items WHERE id = ?""",
            (row_id,),
        )
        if not rows:
            return None

        row = rows[0]
        return Item(
            id=row["id"],
            source=row["source"],
            source_id=row["source_id"],
            title=row["title"] or "",
            content=row["content"],
            snippet=row["snippet"],
            content_hash=row["content_hash"],
            embedding=None,
            metadata=json.loads(row["metadata"]) if row["metadata"] else None,
            indexed_at=row["indexed_at"],
        )

    async def exists_with_hash(self, source: str, source_id: str, content_hash: str) -> bool:
        rows = await self.conn.execute_fetchall(
            "SELECT content_hash FROM items WHERE source = ? AND source_id = ?",
            (source, source_id),
        )
        return bool(rows) and rows[0]["content_hash"] == content_hash

    async def get_indexed_hashes(self, source: str) -> dict[str, tuple[int, str]]:
        rows = await self.conn.execute_fetchall(
            "SELECT id, source_id, content_hash FROM items WHERE source = ?", (source,)
        )
        return {row["source_id"]: (row["id"], row["content_hash"]) for row in rows}

    async def upsert(
        self,
        source: str,
        source_id: str,
        title: str,
        content: str,
        embedding: bytes,
        metadata: dict | None = None,
    ) -> bool:
        content_hash = self.hash_content(content)
        snippet = self.make_snippet(content)
        now = datetime.now(UTC).isoformat()
        metadata_json = json.dumps(metadata) if metadata else None

        existing = await self.conn.execute_fetchall(
            "SELECT id, content_hash FROM items WHERE source = ? AND source_id = ?",
            (source, source_id),
        )

        if existing and existing[0]["content_hash"] == content_hash:
            return False

        if existing:
            item_id = existing[0]["id"]
            await self.conn.execute(
                """
                UPDATE items
                SET title = ?, content = ?, snippet = ?, content_hash = ?,
                    metadata = ?, indexed_at = ?
                WHERE id = ?
                """,
                (title, content, snippet, content_hash, metadata_json, now, item_id),
            )
            if self._has_vec:
                await self.conn.execute("DELETE FROM items_vec WHERE item_id = ?", (item_id,))
                await self.conn.execute(
                    "INSERT INTO items_vec(item_id, embedding, source) VALUES (?, ?, ?)",
                    (item_id, embedding, source),
                )
        else:
            cursor = await self.conn.execute(
                """
                INSERT INTO items (source, source_id, title, content, snippet, content_hash,
                                   metadata, indexed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (source, source_id, title, content, snippet, content_hash, metadata_json, now),
            )
            item_id = cursor.lastrowid
            if self._has_vec:
                await self.conn.execute(
                    "INSERT INTO items_vec(item_id, embedding, source) VALUES (?, ?, ?)",
                    (item_id, embedding, source),
                )

        await self.conn.commit()
        return True

    async def delete(self, source: str, source_id: str) -> bool:
        rows = await self.conn.execute_fetchall(
            "SELECT id FROM items WHERE source = ? AND source_id = ?",
            (source, source_id),
        )
        if not rows:
            return False

        item_id = rows[0]["id"]

        if self._has_vec:
            await self.conn.execute("DELETE FROM items_vec WHERE item_id = ?", (item_id,))

        await self.conn.execute("DELETE FROM items WHERE id = ?", (item_id,))
        await self.conn.commit()
        return True

    async def clear_source(self, source: str) -> int:
        rows = await self.conn.execute_fetchall("SELECT id FROM items WHERE source = ?", (source,))
        if not rows:
            return 0

        item_ids = [row["id"] for row in rows]

        if self._has_vec:
            placeholders = ",".join("?" * len(item_ids))
            await self.conn.execute(
                f"DELETE FROM items_vec WHERE item_id IN ({placeholders})",
                item_ids,
            )

        cursor = await self.conn.execute("DELETE FROM items WHERE source = ?", (source,))
        await self.conn.commit()
        return cursor.rowcount

    async def get_stats(self) -> dict[str, int]:
        rows = await self.conn.execute_fetchall("SELECT source, COUNT(*) as cnt FROM items GROUP BY source")
        return {row["source"]: row["cnt"] for row in rows}

    async def clear_all(self) -> int:
        if self._has_vec:
            await self.conn.execute("DELETE FROM items_vec")
        cursor = await self.conn.execute("DELETE FROM items")
        await self.conn.commit()
        return cursor.rowcount

    async def vector_search(
        self,
        query_embedding: bytes,
        sources: list[str] | None = None,
        limit: int = 20,
    ) -> list[tuple[int, float]]:
        if not self._has_vec:
            return []

        try:
            if sources:
                placeholders = ",".join("?" * len(sources))
                # Filter by the `source` partition key INSIDE the KNN so a small
                # partition (memory_line) isn't starved by a large one (transcript).
                rows = await self.conn.execute_fetchall(
                    f"""
                    SELECT item_id, distance
                    FROM items_vec
                    WHERE embedding MATCH ? AND k = ?
                      AND source IN ({placeholders})
                    ORDER BY distance
                    """,
                    [query_embedding, limit * 2, *sources],
                )
            else:
                rows = await self.conn.execute_fetchall(
                    """
                    SELECT item_id, distance
                    FROM items_vec
                    WHERE embedding MATCH ? AND k = ?
                    ORDER BY distance
                    """,
                    (query_embedding, limit * 2),
                )

            return [(row[0], 1 - row[1]) for row in rows]
        except Exception as e:
            _logger.warning("Vector search failed: %s", e)
            return []

    async def fts_search(
        self,
        query: str,
        sources: list[str] | None = None,
        limit: int = 20,
    ) -> list[tuple[int, float]]:
        if not self._has_fts:
            return []

        fts_query = build_fts_or_query(query)
        if not fts_query:
            return []

        try:
            if sources:
                placeholders = ",".join("?" * len(sources))
                rows = await self.conn.execute_fetchall(
                    f"""
                    SELECT items.id, items_fts.rank
                    FROM items_fts
                    JOIN items ON items_fts.rowid = items.id
                    WHERE items_fts MATCH ? AND items.source IN ({placeholders})
                    ORDER BY items_fts.rank
                    LIMIT ?
                    """,
                    [fts_query, *sources, limit],
                )
            else:
                rows = await self.conn.execute_fetchall(
                    """
                    SELECT items.id, items_fts.rank
                    FROM items_fts
                    JOIN items ON items_fts.rowid = items.id
                    WHERE items_fts MATCH ?
                    ORDER BY items_fts.rank
                    LIMIT ?
                    """,
                    (fts_query, limit),
                )

            return [(row[0], -row[1]) for row in rows]
        except Exception as e:
            _logger.warning("FTS search failed for query '%s': %s", query, e)
            return []
