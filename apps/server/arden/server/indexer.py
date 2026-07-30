from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

import arden.database as database
from arden.embedder import Embedder, EmbeddingConfig
from arden.search.index import SearchIndex
from arden.search.store import SearchStore


class IndexStatus(StrEnum):
    PENDING = "idle"
    INDEXING = "reindexing"
    DONE = "ready"
    DISABLED = "disabled"
    ERROR = "error"


@dataclass
class IndexProgress:
    total: int = 0
    done: int = 0
    status: IndexStatus = IndexStatus.PENDING
    phase: str | None = None


class Indexer:
    def __init__(self, db_path: Path, embedding: EmbeddingConfig | None):
        self.db_path = db_path
        self.embedding = embedding
        self.index: SearchIndex | None = None
        self._conn = None
        self.needs_rebuild = False

    @property
    def vector_enabled(self) -> bool:
        return bool(self.index and self.index.store.has_vector_index)

    @property
    def rate_limit_retry_at(self) -> datetime | None:
        if self.index is None or self.index.embedder is None:
            return None
        return self.index.embedder.retry_at

    async def connect(self) -> None:
        self._conn = await database.connect(self.db_path, vec=self.embedding is not None)
        store = SearchStore(
            self._conn,
            self.embedding.dim if self.embedding else None,
            (self.embedding.identity or self.embedding.model) if self.embedding else None,
        )
        self.needs_rebuild = await store.init_schema()
        retry_at = await store.get_embedding_retry_at() if self.embedding is not None else None
        self.index = SearchIndex(
            store=store,
            embedder=(
                Embedder(
                    self.embedding,
                    retry_at=retry_at,
                    on_rate_limit_change=store.set_embedding_retry_at,
                )
                if self.embedding and store.has_vector_index
                else None
            ),
        )

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None
