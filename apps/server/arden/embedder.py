from dataclasses import dataclass

import numpy as np

from arden.constants import EMBEDDING_TEXT_LIMIT
from arden.llm.router import get_embedding_client


@dataclass
class EmbeddingConfig:
    model: str
    dim: int


class Embedder:
    def __init__(self, config: EmbeddingConfig):
        self.config = config

    def _normalize(self, embeddings: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        return embeddings / np.where(norms == 0, 1, norms)

    async def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.array([])
        truncated = [t[:EMBEDDING_TEXT_LIMIT] for t in texts]
        client = get_embedding_client(self.config.model)
        vectors = await client.embedding(model=self.config.model, texts=truncated)
        embeddings = np.array(vectors)
        return self._normalize(embeddings)

    async def embed_one(self, text: str) -> np.ndarray:
        return (await self.embed([text]))[0]
