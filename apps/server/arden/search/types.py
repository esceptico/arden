from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


@dataclass(frozen=True)
class RawItem:
    source: str
    source_id: str
    title: str
    content: str
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


class SearchResult(BaseModel):
    source: str
    source_id: str
    title: str
    snippet: str | None
    metadata: dict | None = None

    vector_score: float | None = None
    vector_rank: int | None = None
    fts_score: float | None = None
    fts_rank: int | None = None

    rrf_score: float = 0.0


class ScoredRow(BaseModel):
    model_config = ConfigDict(frozen=True)

    row_id: int
    score: float
    rank: int = 0


class RankedResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    row_id: int
    rrf_score: float
    vector_rank: int | None = None
    vector_score: float | None = None
    fts_rank: int | None = None
    fts_score: float | None = None
