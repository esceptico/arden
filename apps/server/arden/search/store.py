import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime

import aiosqlite

from arden.logging import get_logger
from arden.search.fts import build_fts_or_query
from arden.search.migrations import run_migrations

_logger = get_logger(__name__)

# Bump when the items_vec schema changes (forces a rebuild + full re-embed).
# v2: added a `source` partition key so per-source KNN doesn't starve small
# partitions when another source contains many more vectors.
_VEC_SCHEMA_VERSION = "2"
_EMBEDDINGS_DISABLED = "disabled"
_EMBEDDING_READY = "ready"
_EMBEDDING_PENDING = "pending"
_EMBEDDING_RETRY_AT = "embedding_retry_at"

SNIPPET_DISPLAY_LIMIT = 500


def _metadata_json(metadata: dict | None) -> str | None:
    return json.dumps(metadata, sort_keys=True, separators=(",", ":")) if metadata else None


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
    def __init__(self, conn: aiosqlite.Connection, embedding_dim: int | None, embedding_model: str | None = None):
        self.conn = conn
        self.embedding_dim = embedding_dim
        self.embedding_model = embedding_model
        self._has_fts = False
        self._has_vec = False

    @property
    def has_vector_index(self) -> bool:
        return self._has_vec

    async def init_schema(self) -> bool:
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

        # Vectors are derived. Text remains available through FTS while
        # embeddings are disabled or a new model is being rebuilt.
        stored_dim = await self._get_meta("embedding_dim")
        stored_model = await self._get_meta("embedding_model")
        stored_state = await self._get_meta("embedding_state")
        stored_vec_ver = await self._get_meta("vec_schema_version")
        rows = await self.conn.execute_fetchall("SELECT EXISTS(SELECT 1 FROM items)")
        has_items = bool(rows[0][0])
        rows = await self.conn.execute_fetchall(
            "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'items_vec')"
        )
        has_vec_table = bool(rows[0][0])
        dim_changed = (
            self.embedding_dim is not None and stored_dim not in (None, "") and int(stored_dim) != self.embedding_dim
        )
        embedding_identity = self.embedding_model or _EMBEDDINGS_DISABLED
        model_changed = stored_model is not None and stored_model != embedding_identity
        ver_changed = stored_vec_ver != _VEC_SCHEMA_VERSION and (has_items or has_vec_table)
        needs_rebuild = False
        vectors_invalid = False
        if self.embedding_dim is None:
            # The dormant table is never queried without an embedder. Keeping it
            # lets disabled mode open an existing index without loading sqlite-vec;
            # enabling embeddings again replaces it through model_changed below.
            embedding_state = _EMBEDDINGS_DISABLED
        else:
            vectors_invalid = dim_changed or model_changed or ver_changed
            needs_rebuild = vectors_invalid or stored_state == _EMBEDDING_PENDING
            embedding_state = _EMBEDDING_PENDING if needs_rebuild else _EMBEDDING_READY
        if vectors_invalid:
            _logger.info(
                "rebuilding vec table (model_changed=%s dim_changed=%s ver_changed=%s)",
                model_changed,
                dim_changed,
                ver_changed,
            )
            await self.conn.execute("DROP TABLE IF EXISTS items_vec")

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

        if self.embedding_dim is not None:
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

        await self._set_meta("embedding_dim", str(self.embedding_dim) if self.embedding_dim is not None else "")
        await self._set_meta("embedding_model", embedding_identity)
        await self._set_meta("embedding_state", embedding_state)
        await self._set_meta("vec_schema_version", _VEC_SCHEMA_VERSION)
        if model_changed:
            await self._set_meta(_EMBEDDING_RETRY_AT, "")
        await run_migrations(self.conn)
        await self.conn.commit()
        return needs_rebuild

    async def _get_meta(self, key: str) -> str | None:
        try:
            rows = await self.conn.execute_fetchall("SELECT value FROM meta WHERE key = ?", (key,))
            return rows[0][0] if rows else None
        except Exception:
            return None

    async def _set_meta(self, key: str, value: str) -> None:
        await self.conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))

    async def mark_embedding_ready(self) -> None:
        if self.embedding_dim is None:
            return
        await self._set_meta("embedding_state", _EMBEDDING_READY)
        await self.conn.commit()

    async def get_embedding_retry_at(self) -> datetime | None:
        value = await self._get_meta(_EMBEDDING_RETRY_AT)
        return datetime.fromisoformat(value) if value else None

    async def set_embedding_retry_at(self, retry_at: datetime | None) -> None:
        value = retry_at.isoformat() if retry_at is not None else ""
        await self._set_meta(_EMBEDDING_RETRY_AT, value)
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

    async def get_indexed_hashes(self, source: str) -> dict[str, tuple[int, str, bool]]:
        rows = await self.conn.execute_fetchall(
            """
            SELECT items.id, items.source_id, items.content_hash,
                   EXISTS(SELECT 1 FROM items_vec WHERE items_vec.item_id = items.id) AS has_embedding
            FROM items
            WHERE items.source = ?
            """
            if self._has_vec
            else "SELECT id, source_id, content_hash, 0 AS has_embedding FROM items WHERE source = ?",
            (source,),
        )
        return {row["source_id"]: (row["id"], row["content_hash"], bool(row["has_embedding"])) for row in rows}

    async def update_metadata(self, source: str, source_id: str, metadata: dict | None) -> bool:
        """Refresh non-embedding identity without invalidating the vector."""

        metadata_json = _metadata_json(metadata)
        rows = await self.conn.execute_fetchall(
            "SELECT metadata FROM items WHERE source = ? AND source_id = ?",
            (source, source_id),
        )
        if not rows or rows[0]["metadata"] == metadata_json:
            return False
        await self.conn.execute(
            "UPDATE items SET metadata = ?, indexed_at = ? WHERE source = ? AND source_id = ?",
            (metadata_json, datetime.now(UTC).isoformat(), source, source_id),
        )
        await self.conn.commit()
        return True

    async def upsert(
        self,
        source: str,
        source_id: str,
        title: str,
        content: str,
        embedding: bytes | None,
        metadata: dict | None = None,
        *,
        content_hash: str | None = None,
        force: bool = False,
    ) -> bool:
        content_hash = content_hash or self.hash_content(content)
        snippet = self.make_snippet(content)
        now = datetime.now(UTC).isoformat()
        metadata_json = _metadata_json(metadata)

        existing = await self.conn.execute_fetchall(
            "SELECT id, content_hash FROM items WHERE source = ? AND source_id = ?",
            (source, source_id),
        )

        if existing and existing[0]["content_hash"] == content_hash and not force:
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
                if embedding is not None:
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
            if self._has_vec and embedding is not None:
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
                # partition isn't starved by a large one.
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
