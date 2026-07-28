from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from arden.config import Config
from arden.constants import BUILTIN_MEMORY_SYNTHESIZE_ID, BUILTIN_WIKI_MAINTENANCE_ID
from arden.context.models import SessionState
from arden.events.sse import MemoryChangedEvent
from arden.memory.facts import (
    MARKER_NAME,
    FactConsumerStore,
    FactLedger,
    FactLedgerCorruptionError,
    FactPrincipal,
    FactSynthesisResult,
    fact_cutover_content,
)
from arden.memory.facts.synthesis import CONSUMER_ID as FACT_SYNTHESIS_CONSUMER_ID
from arden.server.routers.memory import InitBody, init_memory
from arden.server.routers.memory import router as memory_router
from arden.server.routers.settings import _config_response
from arden.server.runtime import core as runtime_core
from arden.server.runtime.core import Runtime
from arden.tools.core.context import BackgroundTaskRegistry, IOBridge, RunContext, ToolContext, ToolExecution
from arden.tools.facts import FACT_SERVICE
from arden.tools.memory import MEMORY_RECONCILER_SERVICE, MEMORY_RECORDS_SERVICE
from arden.wiki import WikiMaintenanceResult, WikiMaintenanceStore

MIGRATED_AT = datetime(2026, 7, 28, 12, tzinfo=UTC)
USER_SCOPE = ("user", None)


def _config(tmp_path) -> Config:
    config = Config(
        arden_dir=tmp_path,
        chat_model=None,
        embedding_model=None,
        model_roles={},
        web_search="none",
    )
    config.chat_model = None
    config.model_roles = {}
    return config


def _seed_fact(config: Config) -> FactLedger:
    ledger = FactLedger(config.memory_artifacts_dir / "facts", clock=lambda: MIGRATED_AT)
    plan = ledger.plan(
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
    ledger.commit(plan)
    config.memory_artifacts_dir.mkdir(parents=True, exist_ok=True)
    (config.memory_artifacts_dir / MARKER_NAME).write_bytes(fact_cutover_content(MIGRATED_AT))
    return ledger


def _execution(runtime: Runtime, tool_name: str, tool_id: str) -> ToolExecution:
    assert runtime.executor is not None
    ctx = ToolContext(
        session_state=SessionState(session_id="test", started_at=MIGRATED_AT),
        registry=runtime.executor.registry,
        run=RunContext(run_id="run"),
        io=IOBridge(),
        services=runtime.tool_services,
        background_tasks=BackgroundTaskRegistry(session_id="test"),
    )
    return ToolExecution(tool_id=tool_id, tool_name=tool_name, ctx=ctx)


@pytest.mark.asyncio
async def test_fact_cutover_wires_only_canonical_fact_memory_and_survives_restart(tmp_path) -> None:
    config = _config(tmp_path)
    _seed_fact(config)
    runtime = Runtime(config)
    await runtime.connect()
    plan_connection = runtime._fact_plan_conn
    consumer_store = runtime._fact_consumer_store
    maintenance_store = runtime._wiki_maintenance_store
    try:
        assert runtime.fact_service is not None
        assert plan_connection is not None
        assert consumer_store is not None
        assert maintenance_store is not None
        assert runtime.stores is not None and plan_connection is not runtime.stores.conn
        assert consumer_store._conn is not plan_connection
        assert maintenance_store._conn is not plan_connection
        assert maintenance_store._conn is not consumer_store._conn
        assert runtime.knowledge.memory_ready is False
        assert runtime.knowledge.facts_ready is True
        assert runtime.knowledge.memory_writes_enabled is False
        assert runtime.knowledge.record_store is None
        assert runtime.knowledge.memory_curator is None
        assert runtime.knowledge.page_edit_service is None
        assert runtime.knowledge._consolidate is None
        assert runtime.knowledge._vault_index is None
        assert runtime.knowledge._daily_projection is None
        assert runtime.knowledge._link_index is None

        services = runtime.knowledge.tool_services()
        assert services[FACT_SERVICE] is runtime.fact_service
        assert MEMORY_RECORDS_SERVICE not in services
        assert MEMORY_RECONCILER_SERVICE not in services
        assert "area_pages" not in runtime.tool_services
        assert "memory" in runtime.get_available_integrations()
        assert _config_response(runtime)["integrations"]["memory"]["connected"] is True

        with pytest.raises(HTTPException) as init_error:
            await init_memory(InitBody(confirm=True), runtime)
        assert init_error.value.status_code == 503
        assert runtime.automation is not None
        assert (await runtime.automation._build_memory_synthesize_handler()(None)).startswith("fact synthesis:")

        assert runtime.executor is not None
        fact_tool_names = {
            schema["function"]["name"]
            for schema in runtime.executor.get_tools()
            if schema["function"]["name"].endswith("_fact") or "fact" in schema["function"]["name"]
        }
        assert fact_tool_names == {
            "search_facts",
            "get_fact",
            "get_fact_history",
            "get_due_fact_reviews",
            "plan_fact_changes",
            "commit_fact_changes",
        }
        assert not {
            schema["function"]["name"]
            for schema in runtime.executor.get_tools()
            if schema["function"]["name"].startswith("area_page_")
        }

        blocked = await runtime.executor.execute(
            "remember",
            {"text": "Must not reach legacy memory", "kind": "fact"},
            _execution(runtime, "remember", "legacy-write"),
        )
        assert blocked.is_error
        assert blocked.outcome is not None
        assert blocked.outcome.error is not None
        assert blocked.outcome.error.code == "permission_denied"

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

    assert runtime._fact_plan_conn is None
    assert runtime._fact_consumer_store is None
    assert runtime._wiki_maintenance_store is None
    assert runtime.fact_service is None
    assert runtime.knowledge.facts_ready is False
    assert FACT_SERVICE not in runtime.tool_services
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
        assert replay.plan_id == preview.plan_id
    finally:
        await restarted.close()


@pytest.mark.asyncio
async def test_fact_mode_memory_router_reads_managed_wiki_without_legacy_artifacts(tmp_path) -> None:
    config = _config(tmp_path)
    _seed_fact(config)
    runtime = Runtime(config)
    await runtime.connect()
    try:
        assert runtime.knowledge.artifact_store is None
        assert runtime.wiki_service is not None
        runtime.wiki_service.create_page(
            page_id="router-target",
            path="topics/target.md",
            title="Router target",
            body=b"Target body.\n",
            metadata={"kind": "topic"},
        )
        runtime.wiki_service.create_page(
            page_id="router-source",
            path="topics/source.md",
            title="Router source",
            body=b"Source body [[Router target]].\n",
            metadata={"kind": "topic"},
        )
        app = FastAPI()
        app.include_router(memory_router)
        app.state.runtime = runtime

        with TestClient(app) as client:
            items = client.get("/admin/memory/items")
            assert items.status_code == 200
            assert items.json()["items"] == [
                {
                    "id": "seed",
                    "content": "Seed fact",
                    "kind": "fact",
                    "canonical_subject": "seed",
                    "labels": [],
                    "scope": {"kind": "user", "key": None},
                    "provenance": "recorded",
                    "pinned": False,
                    "status": "active",
                    "standing": "durable",
                    "depth": 0,
                    "valid_from": MIGRATED_AT.isoformat(),
                    "invalid_at": None,
                    "source_refs": [
                        {
                            "kind": "migration",
                            "ref": "seed",
                            "captured_at": MIGRATED_AT.isoformat(),
                        }
                    ],
                    "corroboration": 1,
                    "last_relevant_at": MIGRATED_AT.isoformat(),
                    "feedback": "confirmed",
                    "created_at": MIGRATED_AT.isoformat(),
                    "updated_at": MIGRATED_AT.isoformat(),
                }
            ]
            assert client.get("/admin/memory/items", params={"q": ""}).status_code == 200

            listed = client.get("/admin/memory/artifacts")
            assert listed.status_code == 200
            assert {item["path"] for item in listed.json()["artifacts"]} >= {
                "topics/source.md",
                "topics/target.md",
            }

            rebuilt = client.post("/admin/memory/artifacts/rebuild")
            assert rebuilt.status_code == 200
            assert {item["path"] for item in rebuilt.json()["artifacts"]} >= {
                "topics/source.md",
                "topics/target.md",
            }

            detail = client.get("/admin/memory/artifacts/topics/source.md")
            assert detail.status_code == 200
            assert detail.json()["artifact"]["source"] == "wiki"
            assert detail.json()["artifact"]["content"] == "Source body [[Router target]].\n"

            links = client.get("/admin/memory/links", params={"path": "topics/source.md"})
            assert links.status_code == 200
            assert links.json()["outgoing"][0]["resolved_path"] == "topics/target.md"

            assert client.get("/admin/memory/artifacts/missing.md").status_code == 404
            assert client.get("/admin/memory/links", params={"path": "missing.md"}).status_code == 404
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_fact_mode_memory_items_scope_kind_without_key_is_exact(tmp_path) -> None:
    config = _config(tmp_path)
    ledger = _seed_fact(config)
    plan = ledger.plan(
        [
            {
                "op": "create",
                "fact_id": "keyed",
                "text": "Keyed project fact",
                "kind": "fact",
                "subjects": ["scope"],
                "scope": {"kind": "project", "key": "project"},
                "sources": [{"kind": "test", "ref": "scope"}],
            }
        ],
        actor="test",
        origin="test",
        reason="scope filter regression",
    )
    ledger.commit(plan)
    runtime = Runtime(config)
    await runtime.connect()
    try:
        app = FastAPI()
        app.include_router(memory_router)
        app.state.runtime = runtime
        with TestClient(app) as client:
            unkeyed = client.get("/admin/memory/items", params={"scope_kind": "project"})
            keyed = client.get(
                "/admin/memory/items",
                params={"scope_kind": "project", "scope_key": "project"},
            )

        assert unkeyed.json()["items"] == []
        assert [item["id"] for item in keyed.json()["items"]] == ["keyed"]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_present_cutover_with_uninitialized_ledger_fails_before_runtime_setup(tmp_path) -> None:
    config = _config(tmp_path)
    config.memory_artifacts_dir.mkdir(parents=True)
    (config.memory_artifacts_dir / MARKER_NAME).write_bytes(fact_cutover_content(MIGRATED_AT))
    runtime = Runtime(config)

    with pytest.raises(FactLedgerCorruptionError, match="not initialized"):
        await runtime.connect()

    assert runtime.stores is None
    assert not config.sessions_db_path.exists()


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

    assert len(plan_connections) == 1
    assert len(consumer_stores) == 1
    with pytest.raises(ValueError, match="no active connection"):
        await plan_connections[0].execute("SELECT 1")
    with pytest.raises(ValueError, match="no active connection"):
        await consumer_stores[0]._conn.execute("SELECT 1")


@pytest.mark.asyncio
async def test_fact_commit_requests_debounced_synthesis_and_handler_uses_canonical_runner(
    tmp_path, monkeypatch
) -> None:
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
        monkeypatch.setattr(runtime.knowledge, "_memory_reasoning_effort", lambda model: "medium")
        configured = runtime._get_fact_synthesis()
        assert configured is not None
        assert configured._renderer._reasoning_effort == "medium"
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

        class _Synthesis:
            async def run(self):
                return FactSynthesisResult("a" * 64, published_pages=1, advanced=True)

        monkeypatch.setattr(runtime.automation, "get_fact_synthesis", lambda: _Synthesis())
        assert await runtime.automation._build_memory_synthesize_handler()(None) == (
            "fact synthesis: 1 page(s) published; archived 0; under threshold 0"
        )
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_fact_synthesis_without_model_keeps_feed_unadvanced(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    _seed_fact(config)
    runtime = Runtime(config)
    await runtime.connect()
    try:
        assert runtime.automation is not None
        consumers = runtime._fact_consumer_store
        assert consumers is not None
        monkeypatch.setattr(runtime.automation, "get_fact_synthesis", lambda: None)

        assert await runtime.automation._build_memory_synthesize_handler()(None) == (
            "fact synthesis unavailable (no memory model configured)"
        )
        assert await consumers.get("memory.synthesis") is None
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_wiki_maintenance_waits_for_the_durable_synthesis_checkpoint_and_reloads_once(
    tmp_path, monkeypatch
) -> None:
    config = _config(tmp_path)
    _seed_fact(config)
    runtime = Runtime(config)
    await runtime.connect()
    try:
        assert runtime.automation is not None
        health_calls = 0

        async def project_health() -> None:
            nonlocal health_calls
            health_calls += 1

        async def behind() -> bool:
            return False

        monkeypatch.setattr(runtime.automation, "project_wiki_health", project_health)
        monkeypatch.setattr(runtime.automation, "synthesis_is_current", behind)
        retries: list[tuple[str, timedelta]] = []

        async def retry(task_id: str, delay: timedelta) -> bool:
            retries.append((task_id, delay))
            return True

        monkeypatch.setattr(runtime.automation.scheduler, "request_delayed_run", retry)

        class _Maintenance:
            calls = 0

            async def run(self):
                self.calls += 1
                return WikiMaintenanceResult(
                    "a" * 64,
                    "a" * 64,
                    advanced=True,
                    complete=self.calls == 2,
                    reviewed_commits=1,
                    updated_pages=1,
                    reload_required=self.calls == 1,
                )

        maintenance = _Maintenance()
        monkeypatch.setattr(runtime.automation, "get_wiki_maintenance", lambda: maintenance)
        handler = runtime.automation._build_wiki_maintenance_handler()

        assert await handler({"task_id": BUILTIN_WIKI_MAINTENANCE_ID}) == (
            "wiki maintenance deferred: synthesis is behind"
        )
        assert maintenance.calls == 0
        assert retries == [(BUILTIN_WIKI_MAINTENANCE_ID, timedelta(minutes=1))]

        async def current() -> bool:
            return True

        monkeypatch.setattr(runtime.automation, "synthesis_is_current", current)
        assert await handler(None) == "wiki maintenance: current; reviewed 2; updated 2"
        assert maintenance.calls == 2
        assert health_calls == 1
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_wiki_maintenance_second_write_stops_the_bounded_handler(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    _seed_fact(config)
    runtime = Runtime(config)
    await runtime.connect()
    try:
        assert runtime.automation is not None

        async def current() -> bool:
            return True

        class _Maintenance:
            calls = 0

            async def run(self):
                self.calls += 1
                return WikiMaintenanceResult(
                    "b" * 64,
                    "b" * 64,
                    advanced=True,
                    reviewed_commits=1,
                    updated_pages=1,
                    reload_required=True,
                )

        maintenance = _Maintenance()
        monkeypatch.setattr(runtime.automation, "synthesis_is_current", current)
        monkeypatch.setattr(runtime.automation, "get_wiki_maintenance", lambda: maintenance)

        assert await runtime.automation._build_wiki_maintenance_handler()(None) == (
            "wiki maintenance: fresh-feed continuation deferred; reviewed 2; updated 2"
        )
        assert maintenance.calls == 2
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_blocked_wiki_maintenance_notifies_open_desktops(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    _seed_fact(config)
    runtime = Runtime(config)
    await runtime.connect()
    try:
        assert runtime.automation is not None

        async def current() -> bool:
            return True

        class _Maintenance:
            async def run(self):
                return WikiMaintenanceResult(
                    "c" * 64,
                    "b" * 64,
                    blocked=True,
                    reviewed_commits=1,
                )

        emitted: list[MemoryChangedEvent] = []

        async def emit(event):
            emitted.append(event)

        async def reject_health() -> None:
            raise AssertionError("a blocked bounded pass must not start a full health audit")

        monkeypatch.setattr(runtime.automation, "synthesis_is_current", current)
        monkeypatch.setattr(runtime.automation, "get_wiki_maintenance", lambda: _Maintenance())
        monkeypatch.setattr(runtime.automation.scheduler, "emit_automation_event", emit)
        monkeypatch.setattr(runtime.automation, "project_wiki_health", reject_health)

        assert await runtime.automation._build_wiki_maintenance_handler()(None) == (
            "wiki maintenance: needs user review; reviewed 1; updated 0"
        )
        assert len(emitted) == 1
        assert emitted[0].paths == []
        assert emitted[0].revision == "c" * 64
        assert emitted[0].review_required is True
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_synthesis_current_gate_reads_the_canonical_fact_watermark(tmp_path) -> None:
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
        await runtime._fact_consumer_store.advance(
            FACT_SYNTHESIS_CONSUMER_ID,
            feed=feed,
            ledger=runtime._fact_ledger,
        )

        assert await runtime._synthesis_is_current() is True
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_synthesis_current_gate_accepts_a_stable_empty_ledger(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    _seed_fact(config)
    runtime = Runtime(config)
    await runtime.connect()
    try:
        assert runtime.fact_service is not None
        assert runtime._fact_consumer_store is not None

        async def empty_revision() -> None:
            return None

        async def no_watermark(_consumer_id: str) -> None:
            return None

        monkeypatch.setattr(runtime.fact_service, "revision", empty_revision)
        monkeypatch.setattr(runtime._fact_consumer_store, "get", no_watermark)

        assert await runtime._synthesis_is_current() is True
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_fact_mode_scheduler_registers_the_wiki_maintenance_handler(tmp_path) -> None:
    from arden.automation.builtins import seed_builtins
    from arden.automation.models import Automation
    from arden.automation.suggestions import AutomationSuggestion
    from arden.automation.triggers import TimeTrigger
    from arden.constants import (
        BUILTIN_AREA_SUGGESTER_ID,
        BUILTIN_AUTOMATION_SUGGESTER_DAILY_ID,
        BUILTIN_MEMORY_CONSOLIDATE_ID,
        BUILTIN_MEMORY_DREAM_ID,
        BUILTIN_MEMORY_RETENTION_ID,
    )

    config = _config(tmp_path)
    _seed_fact(config)
    runtime = Runtime(config)
    await runtime.connect()
    try:
        assert runtime.automation is not None and runtime.stores is not None
        await seed_builtins(runtime.stores.automations)
        await runtime.stores.automations.save(
            Automation(
                task_id="area:test",
                name="Legacy Area agent",
                prompt="Write the legacy Area page.",
                model=None,
                triggers=[TimeTrigger(every="6h")],
                enabled=True,
                created_at=MIGRATED_AT,
                next_run_at=MIGRATED_AT,
                last_run_at=None,
                last_result=None,
                running_since=None,
                auto_approve=True,
                thread_id="legacy-area-channel",
            )
        )
        runtime.automation.area_suggestions.replace_suggestions(
            [{"id": "area-stale", "key": "stale", "title": "Stale", "page_path": "topics/stale.md"}]
        )
        await runtime.stores.automations.replace_active_suggestions(
            [
                AutomationSuggestion(
                    id="automation-stale",
                    name="Stale suggestion",
                    description="Uses legacy records.",
                    prompt="Do stale work.",
                    triggers=[TimeTrigger(every="6h")],
                    rationale="Legacy evidence.",
                    category="memory",
                    created_at=MIGRATED_AT,
                )
            ]
        )

        await runtime.start_scheduler()

        automation = await runtime.stores.automations.get(BUILTIN_WIKI_MAINTENANCE_ID)
        assert automation is not None
        assert automation.handler == "wiki_maintenance"
        assert "wiki_maintenance" in runtime.automation.scheduler._handlers
        assert await runtime.stores.automations.get(BUILTIN_MEMORY_RETENTION_ID) is not None
        assert await runtime.stores.automations.get(BUILTIN_MEMORY_SYNTHESIZE_ID) is not None
        for task_id in (
            BUILTIN_AREA_SUGGESTER_ID,
            BUILTIN_AUTOMATION_SUGGESTER_DAILY_ID,
            BUILTIN_MEMORY_CONSOLIDATE_ID,
            BUILTIN_MEMORY_DREAM_ID,
        ):
            assert await runtime.stores.automations.get(task_id) is None
        assert await runtime.stores.automations.get("area:test") is None
        with pytest.raises(KeyError):
            await runtime.automation.automation_service.toggle_enabled("area:test")
        await runtime.automation.sync_area_custodian(
            {
                "area_id": "test",
                "name": "Legacy Area",
                "page_path": "topics/stale.md",
                "autonomy": "observe",
            }
        )
        assert await runtime.stores.automations.get("area:test") is None
        assert runtime.automation.area_suggestions.list(set()) == []
        assert await runtime.stores.automations.list_active_suggestions() == []
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_runtime_health_tracks_synthesis_and_index_checkpoints(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    _seed_fact(config)
    runtime = Runtime(config)
    await runtime.connect()
    try:
        assert runtime.fact_service is not None
        assert runtime._fact_consumer_store is not None
        assert runtime._fact_ledger is not None
        assert runtime.wiki_service is not None
        assert runtime.wiki_page_projection is not None
        assert runtime.wiki_repository is not None

        health = runtime.wiki_service.read_page("health").page.body.decode()
        assert "| Synthesis | behind | `none` | never |" in health

        def reject_full_diff(*_args, **_kwargs):
            raise AssertionError("health projection must not materialize wiki diffs")

        monkeypatch.setattr(runtime.wiki_repository, "diff", reject_full_diff)
        feed = await runtime.fact_service.changes_since(None)
        await runtime._fact_consumer_store.advance(
            FACT_SYNTHESIS_CONSUMER_ID,
            feed=feed,
            ledger=runtime._fact_ledger,
        )
        await runtime.project_wiki_health()
        health = runtime.wiki_service.read_page("health").page.body.decode()
        assert "| Synthesis | current |" in health

        class _IndexStore:
            items: dict[str, tuple[str, str, dict]] = {}

            async def get_indexed_hashes(self, source):
                return {key: (index, value[1]) for index, (key, value) in enumerate(self.items.items())}

        class _Index:
            store = _IndexStore()

            async def upsert(self, source, source_id, title, content, metadata):
                self.store.items[source_id] = (title, content, metadata)

            async def delete(self, source, source_id):
                self.store.items.pop(source_id, None)

        runtime.knowledge.search_index = _Index()
        await runtime.wiki_page_projection.sync()

        runtime.wiki_service.create_page(
            page_id="new-page",
            path="new-page.md",
            title="New page",
            body=b"New searchable page.\n",
        )
        await runtime.project_wiki_health()
        health = runtime.wiki_service.read_page("health").page.body.decode()
        assert "**index_behind** — `wiki_page`:" in health

        await runtime.wiki_page_projection.sync()
        health = runtime.wiki_service.read_page("health").page.body.decode()
        assert "**index_behind** — `wiki_page`:" not in health
    finally:
        await runtime.close()
