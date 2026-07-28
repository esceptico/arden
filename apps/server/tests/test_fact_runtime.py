from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from arden.config import Config
from arden.context.models import SessionState
from arden.memory.facts import (
    MARKER_NAME,
    FactLedger,
    FactLedgerCorruptionError,
    FactPrincipal,
    fact_cutover_content,
)
from arden.server.routers.memory import InitBody, init_memory
from arden.server.routers.settings import _config_response
from arden.server.runtime.core import Runtime
from arden.tools.core.context import BackgroundTaskRegistry, IOBridge, RunContext, ToolContext, ToolExecution
from arden.tools.facts import FACT_SERVICE
from arden.tools.memory import MEMORY_RECONCILER_SERVICE, MEMORY_RECORDS_SERVICE

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
    try:
        assert runtime.fact_service is not None
        assert plan_connection is not None
        assert runtime.stores is not None and plan_connection is not runtime.stores.conn
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
        assert "memory" in runtime.get_available_integrations()
        assert _config_response(runtime)["integrations"]["memory"]["connected"] is True

        with pytest.raises(HTTPException) as init_error:
            await init_memory(InitBody(confirm=True), runtime)
        assert init_error.value.status_code == 503
        assert runtime.automation is not None
        assert (
            await runtime.automation._build_memory_synthesize_handler()(None)
            == "legacy memory writes disabled after managed wiki cutover"
        )

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
    assert runtime.fact_service is None
    assert runtime.knowledge.facts_ready is False
    assert FACT_SERVICE not in runtime.tool_services
    with pytest.raises(ValueError, match="no active connection"):
        await plan_connection.execute("SELECT 1")

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
async def test_present_cutover_with_uninitialized_ledger_fails_before_runtime_setup(tmp_path) -> None:
    config = _config(tmp_path)
    config.memory_artifacts_dir.mkdir(parents=True)
    (config.memory_artifacts_dir / MARKER_NAME).write_bytes(fact_cutover_content(MIGRATED_AT))
    runtime = Runtime(config)

    with pytest.raises(FactLedgerCorruptionError, match="not initialized"):
        await runtime.connect()

    assert runtime.stores is None
    assert not config.sessions_db_path.exists()
