import asyncio
import json
from collections.abc import Callable
from typing import NamedTuple

from arden.constants import RRF_K
from arden.database import serialize_embedding
from arden.embedder import Embedder
from arden.logging import get_logger
from arden.search.retrieval import HybridRetriever
from arden.search.store import SearchStore
from arden.search.types import RawItem, SearchResult

_logger = get_logger(__name__)
_ITEM_HASH_VERSION = 2


def item_hash(title: str, content: str) -> str:
    """One versioned embedding identity shared by direct and batch indexing."""

    return SearchStore.hash_content(
        json.dumps(
            {"version": _ITEM_HASH_VERSION, "title": title, "content": content},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


class SyncResult(NamedTuple):
    updated: int
    deleted: int
    degraded: bool = False


type ProgressCallback = Callable[[int, int], None]


class SearchIndex:
    def __init__(
        self,
        store: SearchStore,
        embedder: Embedder | None,
        rrf_k: int = RRF_K,
        vector_weight: float = 0.5,
        fts_weight: float = 0.5,
    ):
        self.store = store
        self.embedder = embedder
        self._lock = asyncio.Lock()
        self.retriever = HybridRetriever(
            store=self.store,
            embedder=self.embedder,
            rrf_k=rrf_k,
            vector_weight=vector_weight,
            fts_weight=fts_weight,
        )

    async def upsert(
        self,
        source: str,
        source_id: str,
        title: str,
        content: str,
        metadata: dict | None = None,
    ) -> bool:
        async with self._lock:
            return await self._upsert(source, source_id, title, content, metadata)

    async def _upsert(
        self,
        source: str,
        source_id: str,
        title: str,
        content: str,
        metadata: dict | None,
    ) -> bool:
        content_hash = item_hash(title, content)

        if await self.store.exists_with_hash(source, source_id, content_hash):
            return await self.store.update_metadata(source, source_id, metadata)

        embedding_bytes = None
        if self.embedder is not None:
            try:
                embedding_bytes = serialize_embedding(await self.embedder.embed_one(f"{title}\n{content}"))
            except Exception as exc:
                _logger.warning("semantic index embedding failed; skipping update", error=str(exc))
                return False

        return await self.store.upsert(
            source,
            source_id,
            title,
            content,
            embedding_bytes,
            metadata,
            content_hash=content_hash,
        )

    async def delete(self, source: str, source_id: str) -> bool:
        async with self._lock:
            return await self.store.delete(source, source_id)

    async def sync(
        self,
        source_name: str,
        items: list[RawItem],
        progress_callback: ProgressCallback | None = None,
        batch_size: int = 50,
        *,
        force: bool = False,
        raise_on_error: bool = False,
    ) -> SyncResult:
        async with self._lock:
            indexed = await self.store.get_indexed_hashes(source_name)
            current_ids = {item.source_id for item in items}

            deleted = 0
            for source_id in set(indexed.keys()) - current_ids:
                await self.store.delete(source_name, source_id)
                deleted += 1

            total = len(items)
            done = 0
            if progress_callback:
                progress_callback(done, total)

            items_to_embed: list[RawItem] = []
            missing_embeddings = {
                source_id for source_id, (_row_id, _content_hash, has_embedding) in indexed.items() if not has_embedding
            }
            for item in items:
                content_hash = item_hash(item.title, item.content)
                unchanged = item.source_id in indexed and indexed[item.source_id][1] == content_hash
                has_embedding = unchanged and indexed[item.source_id][2]
                if not force and unchanged and (self.embedder is None or has_embedding):
                    await self.store.update_metadata(source_name, item.source_id, item.metadata)
                    done += 1
                    if progress_callback:
                        progress_callback(done, total)
                    continue
                items_to_embed.append(item)

            updated = 0
            degraded = False
            for batch_start in range(0, len(items_to_embed), batch_size):
                batch = items_to_embed[batch_start : batch_start + batch_size]
                embeddings = None
                embedding_error: Exception | None = None
                if self.embedder is not None:
                    texts = [f"{item.title}\n{item.content}" for item in batch]
                    try:
                        embeddings = await self.embedder.embed(texts)
                    except Exception as exc:
                        embedding_error = exc
                        degraded = True
                        if not raise_on_error:
                            _logger.warning(
                                "semantic index batch embedding failed; using full-text only",
                                error=str(exc),
                                size=len(batch),
                            )

                for offset, item in enumerate(batch):
                    embedding_bytes = serialize_embedding(embeddings[offset]) if embeddings is not None else None
                    await self.store.upsert(
                        source_name,
                        item.source_id,
                        item.title,
                        item.content,
                        embedding_bytes,
                        item.metadata,
                        content_hash=item_hash(item.title, item.content),
                        force=force or item.source_id in missing_embeddings,
                    )
                    updated += 1
                    done += 1
                    if progress_callback:
                        progress_callback(done, total)
                if embedding_error is not None and raise_on_error:
                    raise embedding_error

            return SyncResult(updated, deleted, degraded)

    async def search(
        self,
        query: str,
        sources: list[str] | None = None,
        limit: int = 10,
    ) -> list[SearchResult]:
        results = await self.retriever.search(query, sources, limit)

        search_results: list[SearchResult] = []
        for r in results:
            item = await self.store.get_by_id(r.row_id)
            if item:
                search_results.append(
                    SearchResult(
                        source=item.source,
                        source_id=item.source_id,
                        title=item.title,
                        snippet=item.snippet,
                        metadata=item.metadata,
                        vector_score=r.vector_score,
                        vector_rank=r.vector_rank,
                        fts_score=r.fts_score,
                        fts_rank=r.fts_rank,
                        rrf_score=r.rrf_score,
                    )
                )

        return search_results

    async def get_stats(self) -> dict[str, int]:
        return await self.store.get_stats()

    async def get_projection_checkpoint(self, source: str) -> str | None:
        return await self.store.get_projection_checkpoint(source)

    async def set_projection_checkpoint(self, source: str, checkpoint: str) -> None:
        await self.store.set_projection_checkpoint(source, checkpoint)

    async def clear_projection_checkpoint(self, source: str) -> None:
        await self.store.clear_projection_checkpoint(source)

    async def clear_source(self, source: str) -> int:
        return await self.store.clear_source(source)

    async def clear(self) -> int:
        return await self.store.clear_all()
