from __future__ import annotations

from typing import cast

import pytest

from arden.memory.facts import FACT_SEARCH_SOURCE, FactIndexProjection, FactIndexState, FactLedger, FactService
from arden.memory.facts.plan_store import FactPlanStore
from arden.search.types import SearchResult


class _Store:
    def __init__(self) -> None:
        self.items: dict[str, tuple[str, str, dict]] = {}

    async def get_indexed_hashes(self, source: str):
        assert source == FACT_SEARCH_SOURCE
        return {fact_id: (index, text) for index, (fact_id, (_title, text, _meta)) in enumerate(self.items.items())}


class _Index:
    def __init__(self) -> None:
        self.store = _Store()

    async def upsert(self, source, source_id, title, content, metadata):
        assert source == FACT_SEARCH_SOURCE
        self.store.items[source_id] = (title, content, metadata)
        return True

    async def delete(self, source, source_id):
        assert source == FACT_SEARCH_SOURCE
        self.store.items.pop(source_id, None)
        return True

    async def search(self, _query, *, sources, limit):
        assert sources == [FACT_SEARCH_SOURCE]
        return [
            SearchResult(source=FACT_SEARCH_SOURCE, source_id=fact_id, title=title, snippet=text)
            for fact_id, (title, text, _metadata) in self.store.items.items()
        ][:limit]


def _service(tmp_path) -> FactService:
    ledger = FactLedger(tmp_path / "facts")
    plan = ledger.plan(
        [
            {
                "op": "create",
                "fact_id": "first",
                "text": "The user prefers tea.",
                "kind": "preference",
                "subjects": ["User"],
                "scope": {"kind": "user", "key": None},
                "sources": [{"kind": "test", "ref": "first"}],
            },
            {
                "op": "create",
                "fact_id": "second",
                "text": "Tea is preferred by the user.",
                "kind": "preference",
                "subjects": ["User"],
                "scope": {"kind": "user", "key": None},
                "sources": [{"kind": "test", "ref": "second"}],
            },
        ],
        actor="test",
        origin="test",
        reason="seed",
    )
    ledger.commit(plan)
    return FactService(ledger, cast("FactPlanStore", object()))


@pytest.mark.asyncio
async def test_fact_index_tracks_active_revision_and_supplies_semantic_candidates(tmp_path) -> None:
    service = _service(tmp_path)
    index = _Index()
    projection = FactIndexProjection(service, lambda: index)

    assert await projection.sync() is index
    assert set(index.store.items) == {"first", "second"}
    assert projection.last_state == FactIndexState(service.ledger.revision, "ready")
    assert index.store.items["first"][2]["version"] == service.ledger.get("first").version

    candidates = await projection.semantic_candidates(service.ledger.get("first"), limit=3)
    assert candidates == ("second",)

    plan = service.ledger.plan(
        [{"op": "retract", "fact_id": "second", "reason": "test cleanup"}],
        actor="test",
        origin="test",
        reason="retract",
    )
    service.ledger.commit(plan)
    await projection.sync()
    assert set(index.store.items) == {"first"}


@pytest.mark.asyncio
async def test_fact_index_is_truthful_when_search_is_unavailable(tmp_path) -> None:
    service = _service(tmp_path)
    projection = FactIndexProjection(service, lambda: None)

    assert await projection.sync() is None
    assert projection.last_state.status == "not_ready"
    assert await projection.semantic_candidates(service.ledger.get("first"), limit=3) == ()
