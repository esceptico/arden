"""Rebuildable semantic index over canonical facts."""

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from arden.logging import get_logger
from arden.memory.facts.models import Fact
from arden.memory.facts.service import FactService
from arden.search.types import RawItem

FACT_SEARCH_SOURCE = "fact"
_logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class FactIndexState:
    fact_revision: str | None
    status: Literal["ready", "error", "not_ready"]
    detail: str | None = None


class FactIndexProjection:
    """Keep one replaceable search partition aligned with a fact revision."""

    def __init__(self, service: FactService, get_search_index: Callable[[], object | None]) -> None:
        self._service = service
        self._get_search_index = get_search_index
        self._lock = asyncio.Lock()
        self._last_state = FactIndexState(None, "not_ready")

    @property
    def last_state(self) -> FactIndexState:
        return self._last_state

    async def sync(
        self,
        *,
        force: bool = False,
        progress_callback: Callable[[int, int], None] | None = None,
        raise_on_error: bool = False,
    ) -> object | None:
        async with self._lock:
            for attempt in range(2):
                index = self._get_search_index()
                if index is None:
                    self._last_state = FactIndexState(
                        self._last_state.fact_revision,
                        "not_ready",
                        "search index is unavailable",
                    )
                    return None
                try:
                    revision = await self._service.revision()
                    facts = await asyncio.to_thread(self._service.ledger.facts_at, revision)
                    active = tuple(fact for fact in facts.values() if fact.status == "active")
                    now = datetime.now(UTC)
                    items = [
                        RawItem(
                            source=FACT_SEARCH_SOURCE,
                            source_id=fact.fact_id,
                            title=" · ".join(fact.subjects),
                            content=fact.text,
                            created_at=now,
                            updated_at=now,
                            metadata={
                                "version": fact.version,
                                "kind": fact.kind,
                                "scope_kind": fact.scope["kind"],
                                "scope_key": fact.scope["key"],
                                "evidence_class": fact.evidence_class,
                            },
                        )
                        for fact in active
                    ]
                    await index.sync(
                        FACT_SEARCH_SOURCE,
                        items,
                        progress_callback=progress_callback,
                        force=force,
                        raise_on_error=raise_on_error,
                    )
                    if self._get_search_index() is not index:
                        if attempt == 0:
                            continue
                        break
                    if await self._service.revision() != revision:
                        if attempt == 0:
                            continue
                        self._last_state = FactIndexState(None, "not_ready", "fact revision changed during index sync")
                        return None
                    self._last_state = FactIndexState(revision, "ready")
                    return index
                except Exception as error:
                    if attempt == 0 and self._get_search_index() is not index:
                        continue
                    _logger.warning("fact index sync failed", exc_info=True)
                    self._last_state = FactIndexState(None, "error", f"{type(error).__name__}: {error}".rstrip(": "))
                    if raise_on_error:
                        raise
                    return None
            self._last_state = FactIndexState(None, "not_ready", "search index changed during fact sync")
            return None

    async def semantic_candidates(self, fact: Fact, *, limit: int) -> Sequence[str]:
        """Return opaque fact IDs; the maintenance worker revalidates every hit."""

        index = await self.sync()
        if index is None:
            return ()
        try:
            results = await index.search(fact.text, sources=[FACT_SEARCH_SOURCE], limit=limit + 1)
        except Exception:
            _logger.warning("fact semantic search failed", exc_info=True)
            return ()
        if self._get_search_index() is not index:
            return ()
        return tuple(result.source_id for result in results if result.source_id != fact.fact_id)[:limit]
