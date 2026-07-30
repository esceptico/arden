import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import numpy as np

from arden.constants import EMBEDDING_TEXT_LIMIT
from arden.llm.router import get_embedding_client

type RateLimitChangeCallback = Callable[[datetime | None], Awaitable[None]]


@dataclass
class EmbeddingConfig:
    model: str
    dim: int


class Embedder:
    def __init__(
        self,
        config: EmbeddingConfig,
        retry_at: datetime | None = None,
        on_rate_limit_change: RateLimitChangeCallback | None = None,
    ):
        self.config = config
        self.retry_at = retry_at
        self.on_rate_limit_change = on_rate_limit_change
        self._request_lock = asyncio.Lock()

    async def _set_retry_at(self, retry_at: datetime | None) -> None:
        if retry_at == self.retry_at:
            return
        self.retry_at = retry_at
        if self.on_rate_limit_change is not None:
            await self.on_rate_limit_change(retry_at)

    async def _wait_for_rate_limit(self) -> None:
        if self.retry_at is None:
            return
        delay = (self.retry_at - datetime.now(UTC)).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)
        await self._set_retry_at(None)

    async def _on_rate_limit_wait(self, delay: float | None) -> None:
        retry_at = None if delay is None else datetime.now(UTC) + timedelta(seconds=delay)
        await self._set_retry_at(retry_at)

    def _normalize(self, embeddings: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        return embeddings / np.where(norms == 0, 1, norms)

    async def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.array([])
        async with self._request_lock:
            await self._wait_for_rate_limit()
            truncated = [t[:EMBEDDING_TEXT_LIMIT] for t in texts]
            client = get_embedding_client(self.config.model)
            vectors = await client.embedding(
                model=self.config.model,
                texts=truncated,
                on_rate_limit_wait=self._on_rate_limit_wait,
            )
        embeddings = np.array(vectors)
        return self._normalize(embeddings)

    async def embed_one(self, text: str) -> np.ndarray:
        return (await self.embed([text]))[0]
