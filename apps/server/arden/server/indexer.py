import asyncio
from dataclasses import asdict, dataclass
from enum import StrEnum
from pathlib import Path

import arden.database as database
from arden.embedder import Embedder, EmbeddingConfig
from arden.logging import get_logger
from arden.search.index import SearchIndex
from arden.search.store import SearchStore

_logger = get_logger(__name__)


class IndexStatus(StrEnum):
    PENDING = "pending"
    INDEXING = "indexing"
    DONE = "done"
    SKIPPED = "skipped"
    ERROR = "error"


@dataclass
class IndexProgress:
    total: int = 0
    done: int = 0
    status: IndexStatus = IndexStatus.PENDING
    updated: int = 0
    deleted: int = 0


class Indexer:
    def __init__(self, db_path: Path, embedding: EmbeddingConfig):
        self.db_path = db_path
        self.embedding = embedding
        self.index: SearchIndex | None = None
        self._conn = None
        self._progress = IndexProgress()
        self._error: str | None = None
        self._running = False
        self._task: asyncio.Task | None = None

    async def connect(self) -> None:
        self._conn = await database.connect(self.db_path, vec=True)
        store = SearchStore(self._conn, self.embedding.dim)
        await store.init_schema()
        self.index = SearchIndex(store=store, embedder=Embedder(self.embedding))

    async def update_embedding(self, embedding: EmbeddingConfig) -> None:
        self.embedding = embedding
        store = self.index.store
        await store.clear_all()
        await store.rebuild_vec_table(embedding.dim)
        self.index = SearchIndex(store=store, embedder=Embedder(embedding))

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def progress(self) -> IndexProgress:
        return self._progress

    @property
    def error(self) -> str | None:
        return self._error

    def start(self, source: object | None) -> None:
        if self._running:
            return

        if not source or not self.index or not self.index.should_embed(source.name):
            self._progress = IndexProgress(status=IndexStatus.SKIPPED)
            return

        self._task = asyncio.create_task(self._run(source))

    async def _run(self, source) -> None:
        self._running = True
        self._progress = IndexProgress()

        try:
            self._progress.status = IndexStatus.INDEXING

            items = await source.scan()
            updated, deleted = await self.index.sync(source.name, items, progress_callback=self._on_progress)
            self._progress.updated += updated
            self._progress.deleted += deleted

            self._progress.status = IndexStatus.DONE
        except asyncio.CancelledError:
            self._progress.status = IndexStatus.ERROR
            raise
        except Exception as e:
            self._error = str(e)
            self._progress.status = IndexStatus.ERROR
        finally:
            self._running = False

    def _on_progress(self, done: int, total: int) -> None:
        self._progress.done = done
        self._progress.total = total

    async def get_status(self) -> dict:
        return {
            "indexing": self._running,
            "progress": asdict(self._progress),
            "error": self._error,
            "stats": await self.index.get_stats() if self.index else {},
        }
