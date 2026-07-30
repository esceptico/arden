from collections import defaultdict

from arden.constants import RRF_K, RRF_OVERFETCH_FACTOR
from arden.database import serialize_embedding
from arden.embedder import Embedder
from arden.logging import get_logger
from arden.search.store import SearchStore
from arden.search.types import RankedResult, ScoredRow

_logger = get_logger(__name__)


def rrf_merge(
    rankings: list[list[tuple[int, float]]],
    k: int = 60,
) -> dict[int, float]:
    """Reciprocal Rank Fusion to merge multiple ranked lists.

    Each ranking is a list of (item_id, score) tuples ordered by relevance.
    Returns a dict of item_id -> fused RRF score.
    """
    scores: dict[int, float] = defaultdict(float)
    for ranking in rankings:
        for rank, (item_id, _) in enumerate(ranking):
            scores[item_id] += 1 / (k + rank + 1)
    return dict(scores)


class HybridRetriever:
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
        self.rrf_k = rrf_k
        self.vector_weight = vector_weight
        self.fts_weight = fts_weight

    async def _vector_search(
        self,
        query_embedding: bytes,
        sources: list[str] | None = None,
        limit: int = 10,
    ) -> list[ScoredRow]:
        results = await self.store.vector_search(query_embedding, sources, limit * RRF_OVERFETCH_FACTOR)
        return [ScoredRow(row_id=row_id, score=score, rank=i + 1) for i, (row_id, score) in enumerate(results)]

    async def _fts_search(
        self,
        query: str,
        sources: list[str] | None = None,
        limit: int = 10,
    ) -> list[ScoredRow]:
        results = await self.store.fts_search(query, sources, limit * RRF_OVERFETCH_FACTOR)
        return [ScoredRow(row_id=row_id, score=score, rank=i + 1) for i, (row_id, score) in enumerate(results)]

    def _rrf_merge(
        self,
        vector_results: list[ScoredRow],
        fts_results: list[ScoredRow],
    ) -> list[RankedResult]:
        vector_lookup = {r.row_id: (r.rank, r.score) for r in vector_results}
        fts_lookup = {r.row_id: (r.rank, r.score) for r in fts_results}

        all_ids = set(vector_lookup.keys()) | set(fts_lookup.keys())
        results: list[RankedResult] = []

        for row_id in all_ids:
            rrf_score = 0.0
            vector_rank, vector_score = vector_lookup.get(row_id, (None, None))
            fts_rank, fts_score = fts_lookup.get(row_id, (None, None))

            if vector_rank is not None:
                rrf_score += self.vector_weight * (1 / (self.rrf_k + vector_rank))
            if fts_rank is not None:
                rrf_score += self.fts_weight * (1 / (self.rrf_k + fts_rank))

            results.append(
                RankedResult(
                    row_id=row_id,
                    rrf_score=rrf_score,
                    vector_rank=vector_rank,
                    vector_score=vector_score,
                    fts_rank=fts_rank,
                    fts_score=fts_score,
                )
            )

        return results

    async def search(
        self,
        query: str,
        sources: list[str] | None = None,
        limit: int = 10,
    ) -> list[RankedResult]:
        vector_results: list[ScoredRow] = []
        if self.embedder is not None:
            try:
                query_embedding = await self.embedder.embed_one(query)
                query_bytes = serialize_embedding(query_embedding)
                vector_results = await self._vector_search(query_bytes, sources, limit)
            except Exception as e:
                _logger.warning("Vector search failed, using FTS only: %s", e)

        fts_results = await self._fts_search(query, sources, limit)

        if not vector_results and not fts_results:
            return []

        merged = self._rrf_merge(vector_results, fts_results)
        merged.sort(key=lambda r: r.rrf_score, reverse=True)

        return merged[:limit]
