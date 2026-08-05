from typing import cast

import pytest

from arden.memory.facts.index import FACT_SEARCH_SOURCE, FactIndexProjection, FactIndexState
from arden.memory.facts.ledger import FactLedger
from arden.memory.facts.plan_store import FactPlanStore
from arden.memory.facts.service import FactService
from arden.search.index import SyncResult
from arden.search.types import SearchResult


class _Store:
    def __init__(self) -> None:
        self.items: dict[str, tuple[str, str, dict]] = {}
        self.checkpoints: dict[str, str] = {}

    async def get_indexed_hashes(self, source: str):
        assert source == FACT_SEARCH_SOURCE
        return {fact_id: (index, text) for index, (fact_id, (_title, text, _meta)) in enumerate(self.items.items())}


class _Index:
    def __init__(self) -> None:
        self.store = _Store()
        self.sync_calls = 0

    async def get_projection_checkpoint(self, source: str) -> str | None:
        return self.store.checkpoints.get(source)

    async def set_projection_checkpoint(self, source: str, checkpoint: str) -> None:
        self.store.checkpoints[source] = checkpoint

    async def clear_projection_checkpoint(self, source: str) -> None:
        self.store.checkpoints.pop(source, None)

    async def upsert(self, source, source_id, title, content, metadata):
        assert source == FACT_SEARCH_SOURCE
        self.store.items[source_id] = (title, content, metadata)
        return True

    async def delete(self, source, source_id):
        assert source == FACT_SEARCH_SOURCE
        self.store.items.pop(source_id, None)
        return True

    async def sync(self, source, items, **_kwargs):
        self.sync_calls += 1
        deleted = 0
        current_ids = {item.source_id for item in items}
        for source_id in set(self.store.items) - current_ids:
            await self.delete(source, source_id)
            deleted += 1
        for item in items:
            await self.upsert(source, item.source_id, item.title, item.content, item.metadata)
        return SyncResult(len(items), deleted)

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
    projection = FactIndexProjection(service.ledger, lambda: index)

    assert await projection.sync() is index
    assert set(index.store.items) == {"first", "second"}
    assert projection.last_state == FactIndexState(service.ledger.revision, "ready")
    assert index.store.items["first"][2]["version"] == service.ledger.get("first").version

    candidates = await projection.semantic_candidates(service.ledger.get("first"), limit=3)
    assert candidates == ("second",)
    assert index.sync_calls == 1

    plan = service.ledger.plan(
        [{"op": "retract", "fact_id": "second", "reason": "test cleanup"}],
        actor="test",
        origin="test",
        reason="retract",
    )
    service.ledger.commit(plan)
    await projection.sync()
    assert set(index.store.items) == {"first"}
    assert index.sync_calls == 2


@pytest.mark.asyncio
async def test_fact_index_restores_checkpoint_without_replaying_facts(tmp_path, monkeypatch) -> None:
    service = _service(tmp_path)
    index = _Index()
    await FactIndexProjection(service.ledger, lambda: index).sync()

    def fail_replay(_revision):
        raise AssertionError("matching checkpoint replayed canonical facts")

    monkeypatch.setattr(service.ledger, "facts_at", fail_replay)
    restored = FactIndexProjection(service.ledger, lambda: index)

    assert await restored.sync() is index
    assert restored.last_state == FactIndexState(service.ledger.current_revision, "ready")
    assert index.sync_calls == 1


@pytest.mark.asyncio
async def test_fact_index_force_bypasses_current_checkpoint(tmp_path) -> None:
    service = _service(tmp_path)
    index = _Index()
    projection = FactIndexProjection(service.ledger, lambda: index)

    await projection.sync()
    await projection.sync(force=True)

    assert index.sync_calls == 2


@pytest.mark.asyncio
async def test_fact_index_rebuilds_after_its_checkpoint_is_cleared(tmp_path) -> None:
    service = _service(tmp_path)
    index = _Index()
    projection = FactIndexProjection(service.ledger, lambda: index)

    await projection.sync()
    index.store.checkpoints.clear()
    await projection.sync()

    assert index.sync_calls == 2


@pytest.mark.asyncio
async def test_fact_index_reports_degraded_embeddings_without_checkpointing(tmp_path) -> None:
    service = _service(tmp_path)

    class _DegradedIndex(_Index):
        async def sync(self, source, items, **kwargs):
            await super().sync(source, items, **kwargs)
            return SyncResult(len(items), 0, semantic_status="degraded", detail="TimeoutError: provider unavailable")

    index = _DegradedIndex()
    projection = FactIndexProjection(service.ledger, lambda: index)

    assert await projection.sync() is index
    assert await projection.sync() is index
    assert index.sync_calls == 2
    assert index.store.checkpoints == {}
    assert projection.last_state == FactIndexState(
        service.ledger.revision,
        "degraded",
        "TimeoutError: provider unavailable",
    )


@pytest.mark.asyncio
async def test_fact_index_checkpoints_explicit_fts_only_mode(tmp_path) -> None:
    service = _service(tmp_path)

    class _FtsOnlyIndex(_Index):
        async def sync(self, source, items, **kwargs):
            await super().sync(source, items, **kwargs)
            return SyncResult(len(items), 0, semantic_status="fts_only")

    index = _FtsOnlyIndex()
    projection = FactIndexProjection(service.ledger, lambda: index)

    assert await projection.sync() is index
    assert projection.last_state == FactIndexState(service.ledger.revision, "fts_only")

    restored = FactIndexProjection(service.ledger, lambda: index)
    assert await restored.sync() is index
    assert restored.last_state == FactIndexState(service.ledger.revision, "fts_only")
    assert index.sync_calls == 1


@pytest.mark.asyncio
async def test_fact_index_never_checkpoints_a_repeatedly_changing_revision(tmp_path) -> None:
    service = _service(tmp_path)

    class _ChangingIndex(_Index):
        async def sync(self, source, items, **kwargs):
            result = await super().sync(source, items, **kwargs)
            fact_id = f"concurrent-{self.sync_calls}"
            service.ledger.commit(
                service.ledger.plan(
                    [
                        {
                            "op": "create",
                            "fact_id": fact_id,
                            "text": f"Concurrent fact {self.sync_calls}.",
                            "kind": "fact",
                            "subjects": ["concurrency"],
                            "scope": {"kind": "user", "key": None},
                            "sources": [{"kind": "test", "ref": fact_id}],
                        }
                    ],
                    actor="test",
                    origin="test",
                    reason="change during indexing",
                )
            )
            return result

    index = _ChangingIndex()
    projection = FactIndexProjection(service.ledger, lambda: index)

    assert await projection.sync() is None
    assert projection.last_state == FactIndexState(None, "not_ready", "fact revision changed during index sync")
    assert index.store.checkpoints == {}


@pytest.mark.asyncio
async def test_fact_index_is_truthful_when_search_is_unavailable(tmp_path) -> None:
    service = _service(tmp_path)
    projection = FactIndexProjection(service.ledger, lambda: None)

    assert await projection.sync() is None
    assert projection.last_state.status == "not_ready"
    assert await projection.semantic_candidates(service.ledger.get("first"), limit=3) == ()


@pytest.mark.asyncio
async def test_ranked_fact_ids_serve_queries_and_report_unavailability(tmp_path) -> None:
    service = _service(tmp_path)
    index = _Index()
    projection = FactIndexProjection(service.ledger, lambda: index)

    ranked = await projection.ranked_fact_ids("tea preference", limit=5)
    assert ranked == ("first", "second")

    unavailable = FactIndexProjection(service.ledger, lambda: None)
    assert await unavailable.ranked_fact_ids("tea preference", limit=5) is None
