from datetime import UTC, datetime, timedelta

import pytest

from arden.config import Config
from arden.constants import BUILTIN_MEMORY_RETENTION_ID, BUILTIN_MEMORY_SYNTHESIZE_ID, BUILTIN_WIKI_MAINTENANCE_ID
from arden.events.sse import MemoryChangedEvent
from arden.memory.facts.consumer_store import FactConsumerStore
from arden.memory.facts.dream import FactDreamResult
from arden.memory.facts.ledger import FactLedger
from arden.memory.facts.maintenance.runner import FactMaintenanceResult
from arden.memory.facts.service import FactPrincipal
from arden.memory.facts.synthesis import (
    CONSUMER_ID as FACT_SYNTHESIS_CONSUMER_ID,
)
from arden.memory.facts.synthesis import (
    FactSynthesisResult,
)
from arden.server.runtime import core as runtime_core
from arden.server.runtime.core import Runtime
from arden.tools.facts import FACT_SERVICE
from arden.wiki.maintenance.runner import WikiMaintenanceResult
from arden.wiki.maintenance.store import WikiMaintenanceStore

MIGRATED_AT = datetime(2026, 7, 28, 12, tzinfo=UTC)
USER_SCOPE = ("user", None)


def _config(tmp_path) -> Config:
    return Config(
        arden_dir=tmp_path,
        chat_model=None,
        embedding_model=None,
        model_roles={},
        web_search="none",
    )


def _seed_fact(config: Config) -> FactLedger:
    ledger = FactLedger(config.memory_artifacts_dir / "facts", clock=lambda: MIGRATED_AT)
    ledger.commit(
        ledger.plan(
            [
                {
                    "op": "create",
                    "fact_id": "seed",
                    "text": "Seed fact",
                    "kind": "fact",
                    "subjects": ["seed"],
                    "scope": {"kind": "user", "key": None},
                    "sources": [{"kind": "migration", "ref": "seed"}],
                }
            ],
            actor="migration",
            origin="offline-import",
            reason="current-memory migration",
        )
    )
    return ledger


@pytest.mark.asyncio
async def test_runtime_wires_canonical_facts_and_survives_restart(tmp_path) -> None:
    config = _config(tmp_path)
    _seed_fact(config)
    runtime = Runtime(config)
    await runtime.connect()
    plan_connection = runtime._fact_plan_conn
    consumer_store = runtime._fact_consumer_store
    maintenance_store = runtime._wiki_maintenance_store
    try:
        assert runtime.fact_service is not None
        assert runtime.wiki_service is not None
        assert runtime.fact_index_projection is not None
        assert plan_connection is not None
        assert consumer_store is not None
        assert maintenance_store is not None
        assert runtime.knowledge.tool_services()[FACT_SERVICE] is runtime.fact_service
        assert runtime.tool_services["wiki"] is runtime.wiki_service
        assert runtime.executor is not None
        names = {schema["function"]["name"] for schema in runtime.executor.get_tools()}
        assert {"search_facts", "get_fact", "plan_fact_changes", "commit_fact_changes", "list_wiki_pages"} <= names
        assert "remember" not in names

        principal = FactPrincipal("session:test", frozenset({USER_SCOPE}), frozenset({USER_SCOPE}))
        preview = await runtime.fact_service.plan(
            principal,
            [
                {
                    "op": "create",
                    "fact_id": "retained-plan",
                    "text": "Retained plan",
                    "kind": "fact",
                    "subjects": ["restart"],
                    "scope": {"kind": "user", "key": None},
                    "sources": [{"kind": "test", "ref": "restart"}],
                }
            ],
            request_key="restart-plan",
            actor="test",
            origin="test",
            reason="restart proof",
        )
    finally:
        await runtime.close()

    assert runtime.fact_service is None
    assert runtime.fact_index_projection is None
    with pytest.raises(ValueError, match="no active connection"):
        await plan_connection.execute("SELECT 1")
    with pytest.raises(ValueError, match="no active connection"):
        await consumer_store._conn.execute("SELECT 1")
    with pytest.raises(ValueError, match="no active connection"):
        await maintenance_store._conn.execute("SELECT 1")
    restarted = Runtime(config)
    await restarted.connect()
    try:
        assert restarted.fact_service is not None
        replay = await restarted.fact_service.plan(
            FactPrincipal("session:test", frozenset({USER_SCOPE}), frozenset({USER_SCOPE})),
            [
                {
                    "op": "create",
                    "fact_id": "retained-plan",
                    "text": "Retained plan",
                    "kind": "fact",
                    "subjects": ["restart"],
                    "scope": {"kind": "user", "key": None},
                    "sources": [{"kind": "test", "ref": "restart"}],
                }
            ],
            request_key="restart-plan",
            actor="test",
            origin="test",
            reason="restart proof",
        )
        assert replay.plan_id == preview.plan_id
    finally:
        await restarted.close()


@pytest.mark.asyncio
async def test_failed_wiki_maintenance_store_init_closes_fact_connections(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    ledger = _seed_fact(config)
    runtime = Runtime(config)
    plan_connections = []
    consumer_stores = []
    original_connect = runtime_core.database.connect
    original_consumer_open = FactConsumerStore.open

    async def tracked_connect(*args, **kwargs):
        connection = await original_connect(*args, **kwargs)
        plan_connections.append(connection)
        return connection

    async def tracked_consumer_open(cls, path):
        store = await original_consumer_open(path)
        consumer_stores.append(store)
        return store

    async def failed_maintenance_open(cls, path):
        raise RuntimeError("maintenance schema unavailable")

    monkeypatch.setattr(runtime_core.database, "connect", tracked_connect)
    monkeypatch.setattr(FactConsumerStore, "open", classmethod(tracked_consumer_open))
    monkeypatch.setattr(WikiMaintenanceStore, "open", classmethod(failed_maintenance_open))

    with pytest.raises(RuntimeError, match="maintenance schema unavailable"):
        await runtime._init_facts(ledger)

    with pytest.raises(ValueError, match="no active connection"):
        await plan_connections[0].execute("SELECT 1")
    with pytest.raises(ValueError, match="no active connection"):
        await consumer_stores[0]._conn.execute("SELECT 1")


@pytest.mark.asyncio
async def test_fact_commit_syncs_index_and_requests_synthesis(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    _seed_fact(config)
    runtime = Runtime(config)
    await runtime.connect()
    try:
        assert runtime.automation is not None and runtime.fact_service is not None
        requests: list[tuple[str, timedelta]] = []

        async def request(task_id: str, delay: timedelta) -> bool:
            requests.append((task_id, delay))
            return True

        monkeypatch.setattr(runtime.automation.scheduler, "request_delayed_run", request)
        principal = FactPrincipal("session:test", frozenset({USER_SCOPE}), frozenset({USER_SCOPE}))
        plan = await runtime.fact_service.plan(
            principal,
            [
                {
                    "op": "create",
                    "fact_id": "notify",
                    "text": "Notify synthesis",
                    "kind": "fact",
                    "subjects": ["notify"],
                    "scope": {"kind": "user", "key": None},
                    "sources": [{"kind": "test", "ref": "notify"}],
                }
            ],
            request_key="notify-plan",
            actor="test",
            origin="test",
            reason="notify",
        )
        await runtime.fact_service.commit(principal, plan.plan_id)
        assert requests == [(BUILTIN_MEMORY_SYNTHESIZE_ID, timedelta(minutes=5))]

        class _Maintenance:
            async def run(self):
                return FactMaintenanceResult(
                    "b" * 64, reviewed_clusters=2, amended_facts=1, merged_facts=1, advanced=True
                )

        monkeypatch.setattr(runtime.automation, "get_fact_maintenance", lambda: _Maintenance())
        assert await runtime.automation._build_fact_maintenance_handler()(None) == (
            "fact maintenance: reviewed 2; amended 1; merged 1"
        )
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_wiki_maintenance_waits_for_synthesis_then_notifies_review(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    _seed_fact(config)
    runtime = Runtime(config)
    await runtime.connect()
    try:
        assert runtime.automation is not None
        retries: list[tuple[str, timedelta]] = []

        async def behind() -> bool:
            return False

        async def retry(task_id: str, delay: timedelta) -> bool:
            retries.append((task_id, delay))
            return True

        monkeypatch.setattr(runtime.automation, "synthesis_is_current", behind)
        monkeypatch.setattr(runtime.automation.scheduler, "request_delayed_run", retry)
        handler = runtime.automation._build_wiki_maintenance_handler()
        assert (
            await handler({"task_id": BUILTIN_WIKI_MAINTENANCE_ID}) == "wiki maintenance deferred: synthesis is behind"
        )
        assert retries == [(BUILTIN_WIKI_MAINTENANCE_ID, timedelta(minutes=1))]

        emitted: list[MemoryChangedEvent] = []

        async def emit(event):
            emitted.append(event)

        async def current() -> bool:
            return True

        class _Maintenance:
            async def run(self):
                return WikiMaintenanceResult("c" * 64, "b" * 64, blocked=True, reviewed_commits=1)

        monkeypatch.setattr(runtime.automation, "synthesis_is_current", current)
        monkeypatch.setattr(runtime.automation, "get_wiki_maintenance", lambda: _Maintenance())
        monkeypatch.setattr(runtime.automation.scheduler, "emit_automation_event", emit)
        assert await handler(None) == "wiki maintenance: needs user review; reviewed 1; updated 0"
        assert emitted[0].review_required is True
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_fact_synthesis_refreshes_health(tmp_path, monkeypatch) -> None:
    runtime = Runtime(_config(tmp_path))
    await runtime.connect()
    try:
        assert runtime.automation is not None
        calls: list[str] = []

        class _Synthesis:
            async def run(self) -> FactSynthesisResult:
                calls.append("synthesis")
                return FactSynthesisResult("a" * 64, published_pages=1, advanced=True)

        async def health() -> None:
            calls.append("health")

        monkeypatch.setattr(runtime.automation, "get_fact_synthesis", lambda: _Synthesis())
        monkeypatch.setattr(runtime.automation, "project_wiki_health", health)

        result = await runtime.automation._build_memory_synthesize_handler()(None)

        assert result == "fact synthesis: 1 page(s) published; archived 0; under threshold 0"
        assert calls == ["synthesis", "health"]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_memory_dream_refreshes_health(tmp_path, monkeypatch) -> None:
    runtime = Runtime(_config(tmp_path))
    await runtime.connect()
    try:
        assert runtime.automation is not None
        calls: list[str] = []

        class _Dream:
            async def run(self) -> FactDreamResult:
                calls.append("dream")
                return FactDreamResult("a" * 64, insight_count=2, published=True)

        async def health() -> None:
            calls.append("health")

        monkeypatch.setattr(runtime.automation, "get_fact_dream", lambda: _Dream())
        monkeypatch.setattr(runtime.automation, "project_wiki_health", health)

        result = await runtime.automation._build_memory_dream_handler()(None)

        assert result == "memory dream: 2 insight(s); published"
        assert calls == ["dream", "health"]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_wiki_maintenance_runs_a_second_pass_after_reload(tmp_path, monkeypatch) -> None:
    runtime = Runtime(_config(tmp_path))
    await runtime.connect()
    try:
        assert runtime.automation is not None
        calls: list[str] = []

        async def current() -> bool:
            return True

        class _Maintenance:
            def __init__(self) -> None:
                self.results = [
                    WikiMaintenanceResult("a" * 64, "a" * 64, reload_required=True),
                    WikiMaintenanceResult("b" * 64, "b" * 64, empty=True),
                ]

            async def run(self) -> WikiMaintenanceResult:
                calls.append("maintenance")
                return self.results.pop(0)

        maintenance = _Maintenance()
        monkeypatch.setattr(runtime.automation, "synthesis_is_current", current)
        monkeypatch.setattr(runtime.automation, "get_wiki_maintenance", lambda: maintenance)

        assert await runtime.automation._build_wiki_maintenance_handler()(None) == (
            "wiki maintenance: idle; reviewed 0; updated 0"
        )
        assert calls == ["maintenance", "maintenance"]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_automation_surfaces_wiki_health_projection_failure(tmp_path, monkeypatch) -> None:
    runtime = Runtime(_config(tmp_path))
    await runtime.connect()
    try:
        assert runtime.automation is not None

        async def fail_health() -> None:
            raise RuntimeError("health projection failed")

        monkeypatch.setattr(runtime.automation, "project_wiki_health", fail_health)
        with pytest.raises(RuntimeError, match="health projection failed"):
            await runtime.automation._refresh_wiki_health()
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_synthesis_current_gate_uses_the_fact_watermark(tmp_path) -> None:
    config = _config(tmp_path)
    _seed_fact(config)
    runtime = Runtime(config)
    await runtime.connect()
    try:
        assert runtime.fact_service is not None
        assert runtime._fact_consumer_store is not None
        assert runtime._fact_ledger is not None
        assert await runtime._synthesis_is_current() is False
        feed = await runtime.fact_service.changes_since(None)
        await runtime._fact_consumer_store.advance(FACT_SYNTHESIS_CONSUMER_ID, feed=feed, ledger=runtime._fact_ledger)
        assert await runtime._synthesis_is_current() is True
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_retention_checkpoint_requires_a_clean_due_review_snapshot(tmp_path) -> None:
    config = _config(tmp_path)
    _seed_fact(config)
    runtime = Runtime(config)
    await runtime.connect()
    try:
        assert runtime.fact_service is not None
        assert runtime._fact_consumer_store is not None
        await runtime._after_automation_finished(BUILTIN_MEMORY_RETENTION_ID, True)
        checkpoint = await runtime._fact_consumer_store.get_retention_checkpoint(BUILTIN_MEMORY_RETENTION_ID)
        assert checkpoint is not None

        principal = FactPrincipal("session:test", frozenset({USER_SCOPE}), frozenset({USER_SCOPE}))
        due = await runtime.fact_service.plan(
            principal,
            [
                {
                    "op": "create",
                    "fact_id": "due",
                    "text": "Due review",
                    "kind": "fact",
                    "subjects": ["due"],
                    "scope": {"kind": "user", "key": None},
                    "lifecycle": "temporary",
                    "review_at": "2026-07-01T00:00:00Z",
                    "review_basis": "explicit",
                    "sources": [{"kind": "test", "ref": "due"}],
                }
            ],
            request_key="due-retention-checkpoint",
            actor="test",
            origin="test",
            reason="prove retention cannot acknowledge outstanding work",
        )
        await runtime.fact_service.commit(principal, due.plan_id)

        await runtime._after_automation_finished(BUILTIN_MEMORY_RETENTION_ID, True)
        unchanged = await runtime._fact_consumer_store.get_retention_checkpoint(BUILTIN_MEMORY_RETENTION_ID)
        assert unchanged == checkpoint
    finally:
        await runtime.close()
