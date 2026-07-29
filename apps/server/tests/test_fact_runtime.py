import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from arden.agent import Usage
from arden.automation.models import Automation
from arden.automation.triggers import TimeTrigger
from arden.config import Config
from arden.constants import (
    BUILTIN_MEMORY_RETENTION_ID,
    BUILTIN_MEMORY_STORAGE_MAINTENANCE_ID,
    BUILTIN_MEMORY_SYNTHESIZE_ID,
    BUILTIN_WIKI_MAINTENANCE_ID,
)
from arden.events.internal import RunCompleted
from arden.events.sse import MemoryChangedEvent
from arden.memory.facts.consumer_store import FactConsumerStore
from arden.memory.facts.dream import FactDreamResult
from arden.memory.facts.ledger import FactLedger
from arden.memory.facts.maintenance.runner import FactMaintenance, FactMaintenanceDecision, FactMaintenanceResult
from arden.memory.facts.maintenance.store import FactMaintenanceError
from arden.memory.facts.service import FactPrincipal
from arden.memory.facts.synthesis import (
    CONSUMER_ID as FACT_SYNTHESIS_CONSUMER_ID,
)
from arden.memory.facts.synthesis import (
    FactSynthesisResult,
)
from arden.operator.runner import RunResult
from arden.revisions.models import CollectionReport
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


def _wiki_producer() -> Automation:
    return Automation(
        task_id="producer",
        name="Producer",
        prompt="Update one feed.",
        model="test",
        triggers=[],
        enabled=True,
        created_at=MIGRATED_AT,
        next_run_at=None,
        last_run_at=None,
        last_result=None,
        running_since=None,
        auto_approve=True,
        thread_id="producer-session",
        read_history=True,
        tool_scope=["read_wiki_page", "publish_wiki_generated"],
    )


@pytest.mark.asyncio
async def test_runtime_does_not_initialize_canonical_memory_when_disabled(tmp_path) -> None:
    config = _config(tmp_path).model_copy(update={"memory": False})
    runtime = Runtime(config)

    await runtime.connect()
    try:
        assert runtime.fact_service is None
        assert runtime.wiki_repository is None
        assert runtime.wiki_service is None
        assert runtime.wiki_rename_coordinator is None
        assert "wiki" not in runtime.tool_services

        assert runtime.executor is not None
        names = {schema["function"]["name"] for schema in runtime.executor.get_tools()}
        assert "search_facts" not in names
        assert "list_wiki_pages" not in names
    finally:
        await runtime.close()


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
async def test_wiki_producer_completion_requires_a_successful_page_read(tmp_path) -> None:
    runtime = Runtime(_config(tmp_path))
    await runtime.connect()
    try:
        assert runtime.automation is not None
        assert runtime.wiki_service is not None
        runtime.wiki_service.create_page(
            path="feeds/email-updates.md",
            title="Email Updates",
            body=b"",
            page_id="feed-email-updates",
            expected_head=runtime.wiki_service.repository.head,
            actor="test",
            origin="test",
            reason="seed producer page",
            metadata={"producer_automation_id": "producer"},
        )
        event = RunCompleted(
            run_id="producer-run",
            session_id="producer-session",
            messages=(),
            usage=Usage(),
            result="No rewrite.",
        )

        with pytest.raises(RuntimeError, match="without successfully reading"):
            await runtime.automation._validate_completed_run(_wiki_producer(), event.run_id)

        await runtime.stores.sessions.store.record_tool_call_started(
            run_id=event.run_id,
            session_id=event.session_id,
            tool_call_id="read-page",
            tool_name="read_wiki_page",
            action="read",
            scope="internal",
        )
        await runtime.stores.sessions.store.record_tool_call_finished(
            run_id=event.run_id,
            tool_call_id="read-page",
            status="success",
            result_preview="Another Page",
        )
        with pytest.raises(RuntimeError, match="owned page"):
            await runtime.automation._validate_completed_run(_wiki_producer(), event.run_id)

        await runtime.stores.sessions.store.record_tool_call_started(
            run_id=event.run_id,
            session_id=event.session_id,
            tool_call_id="read-owned-page",
            tool_name="read_wiki_page",
            action="read",
            scope="internal",
        )
        await runtime.stores.sessions.store.record_tool_call_finished(
            run_id=event.run_id,
            tool_call_id="read-owned-page",
            status="success",
            result_preview="Email Updates",
        )

        await runtime.automation._validate_completed_run(_wiki_producer(), event.run_id)
    finally:
        await runtime.close()


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

        monkeypatch.setattr(runtime.automation, "get_fact_maintenance", lambda _reviewer: _Maintenance())

        requests = []

        async def run_maintenance_agent(_deps, request):
            requests.append(request)
            assert runtime.automation.fact_maintenance_review is not None
            await runtime.automation.fact_maintenance_review.next()
            return RunResult(run_id="maintenance-run", output=None, usage=Usage())

        monkeypatch.setattr("arden.server.runtime.automation.run_agent", run_maintenance_agent)
        result = await runtime.automation._run_fact_maintenance(None)
        assert result.run_id == "maintenance-run"
        assert result.result == "fact maintenance: reviewed 2; amended 1; merged 1"
        assert requests[0].model == config.memory_model
        assert requests[0].automation_id == "builtin-memory-consolidate"
        assert requests[0].tool_scope == ("fact_maintenance_review",)

        async def stop_early(*_args, **_kwargs):
            return RunResult(run_id="maintenance-run", output=None, usage=Usage())

        monkeypatch.setattr("arden.server.runtime.automation.run_agent", stop_early)
        with pytest.raises(FactMaintenanceError, match="before completing"):
            await runtime.automation._run_fact_maintenance(None)
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_fact_maintenance_continues_in_a_fresh_agent_after_progress(tmp_path, monkeypatch) -> None:
    config = _config(tmp_path)
    _seed_fact(config)
    runtime = Runtime(config)
    await runtime.connect()
    try:
        assert runtime.automation is not None

        class _Maintenance:
            def __init__(self, reviewer) -> None:
                self.reviewer = reviewer

            async def run(self) -> FactMaintenanceResult:
                cluster = SimpleNamespace(
                    target_token="F000",
                    markdown="cluster",
                    fact_tokens={"F000": SimpleNamespace(fact_id="seed")},
                )
                await self.reviewer(cluster)
                await self.reviewer(cluster)
                return FactMaintenanceResult("b" * 64, reviewed_clusters=2, advanced=True)

        monkeypatch.setattr(
            runtime.automation,
            "get_fact_maintenance",
            lambda reviewer: _Maintenance(reviewer),
        )
        monkeypatch.setattr(FactMaintenance, "validate_cluster_decision", lambda *_args: None)
        run_ids: list[str] = []

        async def run_one_segment(_deps, _request):
            review = runtime.automation.fact_maintenance_review
            assert review is not None
            await review.next()
            await review.decide(FactMaintenanceDecision(outcome="no_change", reason="No duplicate."))
            run_id = f"maintenance-run-{len(run_ids) + 1}"
            run_ids.append(run_id)
            return RunResult(run_id=run_id, output=None, usage=Usage())

        monkeypatch.setattr("arden.server.runtime.automation.run_agent", run_one_segment)

        result = await runtime.automation._run_fact_maintenance(None)

        assert run_ids == ["maintenance-run-1", "maintenance-run-2"]
        assert result.run_id == "maintenance-run-2"
        assert result.result == "fact maintenance: reviewed 2; amended 0; merged 0"
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_wiki_state_projection_syncs_index_before_health(tmp_path, monkeypatch) -> None:
    runtime = Runtime(_config(tmp_path))
    calls: list[str] = []

    class _Projection:
        last_state = SimpleNamespace(wiki_head=None)

        async def sync(self) -> None:
            calls.append("index")

    async def health() -> None:
        calls.append("health")

    runtime.wiki_service = SimpleNamespace(repository=SimpleNamespace(head=None))
    runtime.wiki_page_projection = _Projection()
    runtime._wiki_maintenance_store = SimpleNamespace(record_projection_revision=None)
    monkeypatch.setattr(runtime, "project_wiki_health", health)

    await runtime.project_wiki_state()

    assert calls == ["index", "health", "index", "health"]


@pytest.mark.asyncio
async def test_concurrent_wiki_projection_notifies_one_head_once(tmp_path, monkeypatch) -> None:
    runtime = Runtime(_config(tmp_path))
    change = SimpleNamespace(before=None, after=SimpleNamespace(path="topics/dex.md"))
    commit = SimpleNamespace(
        actor="automation:area:dex",
        origin="area.page",
        changes=(change,),
    )

    class _Repository:
        head = "new-head"

        def history(self, *, start, stop_before):
            assert (start, stop_before) == ("new-head", "old-head")
            return (commit,)

    class _Projection:
        def __init__(self) -> None:
            self.last_state = SimpleNamespace(wiki_head="old-head")

        async def sync(self) -> None:
            await asyncio.sleep(0)
            self.last_state = SimpleNamespace(wiki_head="new-head")

    notifications: list[tuple[list[str], str | None, dict[str, set[str]]]] = []

    class _Automation:
        async def notify_wiki_changed(self, paths, revision, *, source_areas_by_path) -> None:
            notifications.append((paths, revision, source_areas_by_path))
            await asyncio.sleep(0)

    async def health() -> str:
        return "new-head"

    runtime.wiki_service = SimpleNamespace(repository=_Repository())
    runtime.wiki_page_projection = _Projection()
    runtime.automation = _Automation()
    runtime._wiki_change_head = "old-head"
    revisions: list[tuple[str | None, str]] = []

    class _Watermarks:
        async def record_projection_revision(self, *, expected_revision, revision) -> None:
            revisions.append((expected_revision, revision))

    runtime._wiki_maintenance_store = _Watermarks()
    monkeypatch.setattr(runtime, "project_wiki_health", health)

    await asyncio.gather(
        runtime.project_wiki_change(),
        runtime.project_wiki_change(),
    )

    assert notifications == [(["topics/dex.md"], "new-head", {"topics/dex.md": {"dex"}})]
    assert revisions == [("old-head", "new-head")]


@pytest.mark.asyncio
async def test_latest_external_page_edit_is_not_suppressed_by_older_self_edit(
    tmp_path,
    monkeypatch,
) -> None:
    runtime = Runtime(_config(tmp_path))
    change = SimpleNamespace(
        before=SimpleNamespace(path="topics/dex.md"),
        after=SimpleNamespace(path="topics/dex.md"),
    )
    external = SimpleNamespace(actor="user", origin="desktop", changes=(change,))
    older_self_edit = SimpleNamespace(
        actor="automation:area:dex",
        origin="area.page",
        changes=(change,),
    )

    class _Repository:
        head = "new-head"

        def history(self, *, start, stop_before):
            assert (start, stop_before) == ("new-head", "old-head")
            return (external, older_self_edit)

    class _Projection:
        last_state = SimpleNamespace(wiki_head="new-head")

        async def sync(self) -> None:
            return None

    notifications: list[dict[str, set[str]]] = []

    class _Automation:
        async def notify_wiki_changed(self, paths, revision, *, source_areas_by_path) -> None:
            notifications.append(source_areas_by_path)

    class _Watermarks:
        async def record_projection_revision(self, *, expected_revision, revision) -> None:
            return None

    async def health() -> str:
        return "new-head"

    runtime.wiki_service = SimpleNamespace(repository=_Repository())
    runtime.wiki_page_projection = _Projection()
    runtime.automation = _Automation()
    runtime._wiki_change_head = "old-head"
    runtime._wiki_maintenance_store = _Watermarks()
    monkeypatch.setattr(runtime, "project_wiki_health", health)

    await runtime.project_wiki_change()

    assert notifications == [{}]


@pytest.mark.asyncio
async def test_failed_wiki_projection_keeps_watermark_for_retry(tmp_path, monkeypatch) -> None:
    runtime = Runtime(_config(tmp_path))
    change = SimpleNamespace(before=None, after=SimpleNamespace(path="topics/dex.md"))
    commit = SimpleNamespace(actor="user", origin="user", changes=(change,))

    class _Repository:
        head = "new-head"

        def history(self, *, start, stop_before):
            assert (start, stop_before) == ("new-head", "old-head")
            return (commit,)

    class _Projection:
        last_state = SimpleNamespace(wiki_head="old-head")

        async def sync(self) -> None:
            self.last_state = SimpleNamespace(wiki_head="new-head")

    notifications: list[list[str]] = []

    class _Automation:
        async def notify_wiki_changed(self, paths, revision, *, source_areas_by_path) -> None:
            notifications.append(paths)

    revisions: list[tuple[str | None, str]] = []

    class _Watermarks:
        async def record_projection_revision(self, *, expected_revision, revision) -> None:
            revisions.append((expected_revision, revision))

    async def failed_health() -> str:
        raise RuntimeError("health projection failed")

    runtime.wiki_service = SimpleNamespace(repository=_Repository())
    runtime.wiki_page_projection = _Projection()
    runtime.automation = _Automation()
    runtime._wiki_change_head = "old-head"
    runtime._wiki_maintenance_store = _Watermarks()
    monkeypatch.setattr(runtime, "project_wiki_health", failed_health)

    with pytest.raises(RuntimeError, match="health projection failed"):
        await runtime.project_wiki_change()

    assert runtime._wiki_change_head == "old-head"
    assert notifications == []
    assert revisions == []

    async def healthy() -> str:
        return "new-head"

    monkeypatch.setattr(runtime, "project_wiki_health", healthy)
    await runtime.project_wiki_change()

    assert notifications == [["topics/dex.md"]]
    assert revisions == [("old-head", "new-head")]
    assert runtime._wiki_change_head == "new-head"


@pytest.mark.asyncio
async def test_committed_wiki_projection_failure_is_durably_queued(tmp_path, monkeypatch) -> None:
    runtime = Runtime(_config(tmp_path))
    revision = "a" * 64
    queued: list[str] = []

    class _Outbox:
        async def enqueue_wiki_projection(self, value: str) -> bool:
            queued.append(value)
            return True

    runtime.stores = SimpleNamespace(outbox=_Outbox())
    runtime.wiki_service = SimpleNamespace(repository=SimpleNamespace(head=revision))

    async def fail_projection() -> None:
        raise RuntimeError("notifier unavailable")

    monkeypatch.setattr(runtime, "project_wiki_change", fail_projection)

    assert await runtime.project_wiki_change_after_commit() is True
    assert queued == [revision]


@pytest.mark.asyncio
async def test_any_automation_reconciles_a_wiki_head_it_changed(tmp_path, monkeypatch) -> None:
    runtime = Runtime(_config(tmp_path))
    runtime.wiki_service = SimpleNamespace(repository=SimpleNamespace(head="new"))
    runtime._wiki_change_head = "old"
    calls: list[str] = []

    async def project() -> None:
        calls.append("project")

    monkeypatch.setattr(runtime, "project_wiki_state", project)

    await runtime._after_automation_finished("email-feed", True)

    assert calls == ["project"]


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
        handler = runtime.automation._run_wiki_maintenance
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
            def __init__(self, reviewer) -> None:
                self.reviewer = reviewer

            async def run(self):
                return WikiMaintenanceResult("c" * 64, "b" * 64, blocked=True, reviewed_commits=1)

        monkeypatch.setattr(runtime.automation, "synthesis_is_current", current)
        monkeypatch.setattr(runtime.automation, "get_wiki_maintenance", _Maintenance)
        monkeypatch.setattr(runtime.automation.scheduler, "emit_automation_event", emit)

        async def run_review_agent(_deps, _request):
            assert runtime.automation.wiki_maintenance_review is not None
            await runtime.automation.wiki_maintenance_review.next()
            return RunResult(run_id="wiki-maintenance-run", output=None, usage=Usage())

        monkeypatch.setattr("arden.server.runtime.automation.run_agent", run_review_agent)
        result = await handler(None)
        assert result.run_id == "wiki-maintenance-run"
        assert result.result == "wiki maintenance: needs user review; reviewed 1; updated 0"
        assert emitted[0].review_required is True
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_fact_synthesis_leaves_health_projection_to_completion_callback(tmp_path, monkeypatch) -> None:
    runtime = Runtime(_config(tmp_path))
    await runtime.connect()
    try:
        assert runtime.automation is not None
        calls: list[str] = []

        class _Synthesis:
            async def run(self) -> FactSynthesisResult:
                calls.append("synthesis")
                return FactSynthesisResult("a" * 64, published_pages=1, advanced=True)

        monkeypatch.setattr(runtime.automation, "get_fact_synthesis", lambda: _Synthesis())

        result = await runtime.automation._run_memory_synthesis(None)

        assert result == "fact synthesis: 1 page(s) published; archived 0; under threshold 0"
        assert calls == ["synthesis"]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_memory_dream_leaves_wiki_projection_to_completion_callback(tmp_path, monkeypatch) -> None:
    runtime = Runtime(_config(tmp_path))
    await runtime.connect()
    try:
        assert runtime.automation is not None
        calls: list[str] = []

        class _Dream:
            async def run(self) -> FactDreamResult:
                calls.append("dream")
                return FactDreamResult("a" * 64, insight_count=2, published=True)

        monkeypatch.setattr(runtime.automation, "get_fact_dream", lambda: _Dream())

        result = await runtime.automation._run_memory_dream(None)

        assert result == "memory dream: 2 insight(s); published"
        assert calls == ["dream"]
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_managed_history_collection_reports_both_repositories(tmp_path, monkeypatch) -> None:
    runtime = Runtime(_config(tmp_path))
    await runtime.connect()
    try:
        assert runtime.automation is not None
        assert runtime._fact_ledger is not None
        assert runtime.wiki_service is not None
        callback = runtime.automation.collect_managed_history
        assert callback is not None
        assert callback.__self__ is runtime

        calls: list[str] = []
        facts = CollectionReport(scanned=5, removed=2, retained=3, bytes_removed=17)
        wiki = CollectionReport(scanned=7, removed=1, retained=6, bytes_removed=19)
        monkeypatch.setattr(
            runtime._fact_ledger,
            "collect_history",
            lambda: calls.append("facts") or facts,
        )
        monkeypatch.setattr(
            runtime.wiki_service.repository,
            "collect",
            lambda: calls.append("wiki") or wiki,
        )

        result = await runtime.automation._run_managed_history_collection(None)

        assert calls == ["facts", "wiki"]
        assert result == (
            "managed history collection: facts scanned 5, removed 2, retained 3, bytes removed 17; "
            "wiki scanned 7, removed 1, retained 6, bytes removed 19"
        )
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_managed_history_collection_attempts_both_and_propagates_partial_failure(
    tmp_path,
    monkeypatch,
) -> None:
    runtime = Runtime(_config(tmp_path))
    await runtime.connect()
    try:
        assert runtime.automation is not None
        assert runtime._fact_ledger is not None
        assert runtime.wiki_service is not None
        calls: list[str] = []
        wiki = CollectionReport(scanned=7, removed=1, retained=6, bytes_removed=19)

        def fail_facts() -> CollectionReport:
            calls.append("facts")
            raise OSError("fact history is unavailable")

        monkeypatch.setattr(runtime._fact_ledger, "collect_history", fail_facts)
        monkeypatch.setattr(
            runtime.wiki_service.repository,
            "collect",
            lambda: calls.append("wiki") or wiki,
        )

        with pytest.raises(ExceptionGroup, match="managed-history collection failed: facts") as caught:
            await runtime.automation._run_managed_history_collection(None)

        assert calls == ["facts", "wiki"]
        assert len(caught.value.exceptions) == 1
        assert isinstance(caught.value.exceptions[0], OSError)
        assert str(caught.value.exceptions[0]) == "fact history is unavailable"
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_scheduler_without_memory_model_seeds_non_llm_history_collection(tmp_path) -> None:
    config = _config(tmp_path).model_copy(update={"model_roles": {}})
    assert config.memory_model is None
    runtime = Runtime(config)
    await runtime.connect()
    try:
        assert runtime.automation is not None
        assert runtime.automation.collect_managed_history is not None

        await runtime.start_scheduler()

        storage = await runtime.stores.automations.get(BUILTIN_MEMORY_STORAGE_MAINTENANCE_ID)
        assert storage is not None
        assert storage.model is None
        assert storage.handler == "managed_history_collection"
        assert storage.triggers == [TimeTrigger(every="7d")]
        status = await runtime.automation.scheduler.get_status()
        assert "managed_history_collection" in status["registered_handlers"]
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

        results = [
            WikiMaintenanceResult("a" * 64, "a" * 64, reload_required=True),
            WikiMaintenanceResult("b" * 64, "b" * 64, empty=True),
        ]

        class _Maintenance:
            def __init__(self, reviewer) -> None:
                self.reviewer = reviewer

            async def run(self) -> WikiMaintenanceResult:
                calls.append("maintenance")
                return results.pop(0)

        monkeypatch.setattr(runtime.automation, "synthesis_is_current", current)
        monkeypatch.setattr(runtime.automation, "get_wiki_maintenance", _Maintenance)

        async def run_review_agent(_deps, _request):
            assert runtime.automation.wiki_maintenance_review is not None
            await runtime.automation.wiki_maintenance_review.next()
            return RunResult(run_id=f"wiki-maintenance-run-{len(calls)}", output=None, usage=Usage())

        monkeypatch.setattr("arden.server.runtime.automation.run_agent", run_review_agent)

        result = await runtime.automation._run_wiki_maintenance(None)
        assert result.run_id == "wiki-maintenance-run-2"
        assert result.result == "wiki maintenance: idle; reviewed 0; updated 0"
        assert calls == ["maintenance", "maintenance"]
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
