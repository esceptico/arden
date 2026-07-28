from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import HTTPException, Request

import arden.database as database
from arden.config import Config, get_config
from arden.core.factory import AgentConfig
from arden.integrations import ALL_INTEGRATIONS, IntegrationRegistry
from arden.integrations.slack.client import SlackClient
from arden.llm.openai_codex_catalog import refresh_codex_models
from arden.llm.router import close as llm_close
from arden.llm.router import get_completion_client
from arden.llm.router import init as llm_init
from arden.logging import get_logger
from arden.mcp.manager import MCPManager
from arden.memory.facts import (
    LEDGER_DIRECTORY,
    CompletionFactSynthesisRenderer,
    FactConsumerStore,
    FactLedger,
    FactPlanStore,
    FactService,
    FactSynthesis,
    load_fact_cutover,
)
from arden.monitor.slack import SlackMonitor
from arden.notifiers.base import NotifierContext
from arden.notifiers.service import NotifierService
from arden.observability import init_tracing, shutdown_tracing
from arden.operator.runner import OperatorDeps
from arden.revisions import ManagedFileRepository
from arden.server.app_control import AppControlService
from arden.server.runtime.automation import AutomationRuntime
from arden.server.runtime.config import RuntimeConfig
from arden.server.runtime.knowledge import KnowledgeRuntime
from arden.server.state import RunRegistry
from arden.server.stores import Stores
from arden.services.session import SessionService
from arden.skills.registry import SkillRegistry
from arden.skills.service import SkillService, get_skills_dirs
from arden.tools.connections import ConnectionService
from arden.tools.executor import ToolExecutor
from arden.wiki import WikiRenameApprovalCoordinator, WikiRenameApprovalStore, WikiService

_logger = get_logger(__name__)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime


class Runtime:
    def __init__(self, config: Config | None = None):
        initial_config = config or get_config()
        self.integrations = IntegrationRegistry(ALL_INTEGRATIONS)
        self.integrations.sync(initial_config)
        self.connection_service = ConnectionService(self.integrations)
        self.run_registry = RunRegistry()
        self.knowledge = KnowledgeRuntime(initial_config)

        self.stores: Stores | None = None
        self.automation: AutomationRuntime | None = None
        self.mcp_manager: MCPManager | None = None
        self.executor: ToolExecutor | None = None
        self.skill_registry: SkillRegistry | None = None
        self.skill_service: SkillService | None = None
        self.notifier_service: NotifierService | None = None
        self.dispatch_session_message: (
            Callable[
                [str, str, str | None, bool | None, list[dict] | None],
                Awaitable[object],
            ]
            | None
        ) = None
        self.resume_suspended_chat_run: Callable[[str, str], Awaitable[object]] | None = None
        self.app_control: AppControlService | None = None
        self.wiki_repository: ManagedFileRepository | None = None
        self.wiki_service: WikiService | None = None
        self.wiki_rename_coordinator: WikiRenameApprovalCoordinator | None = None
        self._wiki_approval_conn: database.aiosqlite.Connection | None = None
        self.fact_service: FactService | None = None
        self._fact_plan_conn: database.aiosqlite.Connection | None = None
        self._fact_consumer_store: FactConsumerStore | None = None
        self._fact_ledger: FactLedger | None = None

        self._connected = False
        self._closing = False

        self.config_runtime = RuntimeConfig(
            initial_config,
            get_integrations=lambda: self.integrations,
            get_knowledge=lambda: self.knowledge,
            get_stores=lambda: self.stores,
            sync_mcp=lambda config: self.sync_mcp(config),
            is_closing=lambda: self._closing,
            refresh_models=refresh_codex_models,
            after_reload=self._after_config_reload,
        )

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def config(self) -> Config:
        return self.config_runtime.config

    @property
    def config_service(self):
        return self.config_runtime.service

    def config_status(self) -> dict[str, int | str]:
        return self.config_runtime.status()

    @property
    def session_service(self) -> SessionService | None:
        return self.stores.sessions if self.stores else None

    @property
    def embedding(self):
        return self.knowledge.embedding

    @property
    def indexer(self):
        return self.knowledge.indexer

    @property
    def memory_curator(self):
        return self.knowledge.memory_curator

    @property
    def memory_records(self):
        return self.knowledge._record_store

    @property
    def search_index(self):
        return self.knowledge.search_index

    @property
    def scheduler(self):
        return self.automation.scheduler if self.automation else None

    @property
    def automation_service(self):
        return self.automation.automation_service if self.automation else None

    @property
    def monitor(self):
        return self.automation.monitor if self.automation else None

    @property
    def outbox_runtime(self):
        return self.automation.outbox_runtime if self.automation else None

    @property
    def tool_services(self) -> dict[str, object]:
        services: dict[str, object] = dict(self.integrations.clients)
        services["connections"] = self.connection_service
        services["area_pages"] = self.config.memory_artifacts_dir
        if self.automation:
            services["area_custodians"] = self.automation.custodians
        services.update(self.knowledge.tool_services())
        if self.automation_service:
            services["automation"] = self.automation_service
        if self.session_service:
            # Exposed so tools (read-only `list_recent_sessions` / `read_session`)
            # can query session history. Used by the propose-* skills when
            # running inside a scheduled automation that needs cross-session
            # pattern detection.
            services["session"] = self.session_service
        if self.app_control:
            services["app_control"] = self.app_control
        if self.skill_registry:
            services["skill_registry"] = self.skill_registry
        if self.skill_service:
            services["skill_service"] = self.skill_service
        if self.mcp_manager and self.mcp_manager.tools:
            services["mcp"] = self.mcp_manager
        if self.notifier_service and self.notifier_service.notifiers:
            services["notifiers"] = self.notifier_service
        return services

    def _create_executor(self, config: Config | None = None) -> ToolExecutor:
        config = config or self.config
        mcp_tools = list(self.mcp_manager.tools) if self.mcp_manager else None
        return ToolExecutor(
            mcp_tools=mcp_tools,
            get_services=lambda: self.tool_services,
            tool_overrides=config.tool_overrides,
        )

    # --- Subsystem lifecycle ---

    async def reload_config(self) -> None:
        await self.config_runtime.reload()

    async def _after_config_reload(self) -> None:
        return

    async def sync_mcp(self, config: Config | None = None) -> None:
        config = config or self.config
        if self.mcp_manager:
            await self.mcp_manager.close()
            self.mcp_manager = None

        if config.mcp_servers:
            self.mcp_manager = MCPManager()
            await self.mcp_manager.connect(config.mcp_servers)

        if self.executor:
            self.executor = self._create_executor(config)

    # --- Connect / close ---

    async def connect(self) -> None:
        if self._connected:
            return

        fact_ledger = self._load_fact_ledger()
        init_tracing()
        llm_init(self.config)
        self.stores = await Stores.connect(self.config)
        await self._init_wiki()
        await self._init_facts(fact_ledger)
        self.knowledge.set_memory_write_guard(self._require_legacy_page_writes)
        await self.knowledge.connect(self.stores)
        self._init_skills()
        await self._init_notifiers()
        self._init_automation()
        await self._init_mcp()
        self._init_tools()

        self._connected = True
        _logger.info(
            "Runtime ready",
            integrations=len(self.integrations.clients),
            tools=len(self.executor.registry),
        )

    def _require_legacy_page_writes(self) -> None:
        # The only empty -> managed transition is the supervised, offline
        # migration. Once a managed head exists, legacy canonical writes stay
        # disabled globally; path-level coexistence would be dual-write.
        if self.fact_service is not None:
            raise PermissionError("legacy memory writes are disabled after canonical fact cutover")
        if self.wiki_repository is not None and self.wiki_repository.head is not None:
            raise PermissionError("legacy memory page writes are disabled after managed wiki cutover")

    def _load_fact_ledger(self) -> FactLedger | None:
        if not self.config.memory or load_fact_cutover(self.config.memory_artifacts_dir) is None:
            return None
        ledger = FactLedger(self.config.memory_artifacts_dir / LEDGER_DIRECTORY)
        ledger.validate_initialized()
        return ledger

    def _init_skills(self) -> None:
        self.skill_registry = SkillRegistry()
        self.skill_registry.load(get_skills_dirs())
        self.skill_service = SkillService(self.skill_registry)

    async def _init_wiki(self) -> None:
        wiki_root = self.config.memory_artifacts_dir / "wiki"
        self.wiki_repository = ManagedFileRepository(
            wiki_root / "pages",
            history_root=wiki_root / ".wiki-history",
        )
        self.wiki_service = WikiService(self.wiki_repository)
        # The wiki approval store must not share Stores' writer connection: it
        # owns its own lock/transactions while persisting in sessions.db.
        self._wiki_approval_conn = await database.connect(self.config.sessions_db_path)
        approval_store = WikiRenameApprovalStore(self._wiki_approval_conn)
        await approval_store.init_schema()
        self.wiki_rename_coordinator = WikiRenameApprovalCoordinator(self.wiki_service, approval_store)

    async def _init_facts(self, ledger: FactLedger | None) -> None:
        self.fact_service = None
        self._fact_ledger = None
        self.knowledge.set_fact_service(None)
        if ledger is None:
            return
        connection = await database.connect(self.config.memory_db_path)
        consumers: FactConsumerStore | None = None
        try:
            plans = FactPlanStore(connection)
            await plans.init_schema()
            consumers = await FactConsumerStore.open(self.config.memory_db_path)
        except BaseException:
            if consumers is not None:
                await consumers.close()
            await connection.close()
            raise
        self._fact_plan_conn = connection
        self._fact_consumer_store = consumers
        self._fact_ledger = ledger
        self.fact_service = FactService(ledger, plans, post_commit=self._request_fact_synthesis)
        self.knowledge.set_fact_service(self.fact_service)

    async def _request_fact_synthesis(self) -> None:
        if self.automation is not None:
            await self.automation.request_fact_synthesis()

    def _get_fact_synthesis(self) -> FactSynthesis | None:
        model = self.config.memory_model
        if self._fact_ledger is None or self._fact_consumer_store is None or self.wiki_service is None or not model:
            return None
        return FactSynthesis(
            self._fact_ledger,
            self._fact_consumer_store,
            self.wiki_service,
            CompletionFactSynthesisRenderer(
                get_completion_client(model),
                model,
                reasoning_effort=self.knowledge._memory_reasoning_effort(model),
            ),
        )

    async def _init_notifiers(self) -> None:
        self.notifier_service = NotifierService(
            store=self.stores.notifiers,
            ctx=NotifierContext(
                get_source=lambda name: self.tool_services.get(name),
                get_config_value=lambda key: self.config.model_dump().get(key),
            ),
        )
        await self.notifier_service.seed_defaults()
        await self.notifier_service.rebuild()

    def _init_automation(self) -> None:
        self.automation = AutomationRuntime(
            stores=self.stores,
            config=self.config,
            build_operator_deps=self.build_operator_deps,
            get_records=lambda: self.memory_records,
            get_chat_connector=lambda: self.knowledge.chat_connector,
            get_calendar_source=lambda: self.integrations.get_client("calendar"),
            get_slack_client=lambda: self.integrations.get_client("slack"),
            get_cheap_llm=lambda: get_completion_client(self.config.memory_model) if self.config.memory_model else None,
            cheap_model=self.config.memory_model,
            indexer=self.indexer,
            get_consolidate=lambda: self.knowledge._consolidate,
            get_fact_synthesis=self._get_fact_synthesis,
            get_knowledge=lambda: self.knowledge,
            get_integration_clients=lambda: self.integrations.clients,
            get_notifiers=lambda: self.notifier_service,
        )

    async def _init_mcp(self) -> None:
        if self.config.mcp_servers:
            self.mcp_manager = MCPManager()
            await self.mcp_manager.connect(self.config.mcp_servers)

    def _init_tools(self) -> None:
        self.executor = self._create_executor()

    async def close(self) -> None:
        self._closing = True

        # Phase 1: stop accepting new work
        cancelled = await self.run_registry.cancel_all()
        if cancelled:
            _logger.info("Cancelled %d active run(s)", cancelled)

        # Phase 2: stop background services
        if self.automation:
            await self.automation.stop()
        await self.knowledge.stop()

        # Phase 3: close resources
        if self.mcp_manager:
            await self.mcp_manager.close()
        await self.knowledge.close()
        if self._fact_consumer_store:
            await self._fact_consumer_store.close()
            self._fact_consumer_store = None
        if self._fact_plan_conn:
            await self._fact_plan_conn.close()
            self._fact_plan_conn = None
        self.fact_service = None
        self._fact_ledger = None
        self.knowledge.set_fact_service(None)
        if self._wiki_approval_conn:
            await self._wiki_approval_conn.close()
            self._wiki_approval_conn = None
        if self.stores:
            await self.stores.close()
        await llm_close()
        shutdown_tracing()

    # --- Queries ---

    def get_available_integrations(self) -> list[str]:
        ids = list(self.integrations.clients.keys())
        if self.memory_records is not None or self.fact_service is not None:
            ids.append("memory")
        return ids

    def get_integration_errors(self) -> dict[str, str]:
        errors = dict(self.integrations.errors)
        if self.indexer and self.indexer.error:
            errors["index"] = self.indexer.error
        return errors

    def build_chat_deps(self, chat_model: str | None = None):
        if not self.executor or not self.session_service:
            raise RuntimeError("Chat dependencies are not initialized")
        from arden.services.chat import ChatDeps

        resolved_model = chat_model or self.config.chat_model
        return ChatDeps(
            chat_model=resolved_model,
            agent_config=AgentConfig.from_config(self.config, model=resolved_model),
            executor=self.executor,
            session_service=self.session_service,
            run_registry=self.run_registry,
            available_integrations=self.get_available_integrations(),
            integration_errors=self.get_integration_errors(),
            connection_catalog=tuple(self.integrations.list_connections()),
            enqueue_run_completed=self.stores.outbox.enqueue_run_completed if self.stores else None,
            enqueue_run_failed=self.stores.outbox.enqueue_run_failed if self.stores else None,
            dispatch_session_message=self.dispatch_session_message,
            memory_curator=self.memory_curator,
            memory_records=self.memory_records,
            skill_registry=self.skill_registry,
            notifier_service=self.notifier_service,
        )

    async def resolve_session_chat_model(self, session_id: str | None) -> str | None:
        """The session's per-chat model override, or None to fall back to the
        global default. Resolve this before building deps so a sync
        deps_factory can close over the result."""
        if session_id and self.session_service:
            existing = await self.session_service.load(session_id)
            if existing is not None:
                return existing.state.chat_model
        return None

    # --- Background tasks ---

    def build_operator_deps(self) -> OperatorDeps:
        return OperatorDeps(
            executor=self.executor,
            memory_records=self.memory_records,
            config=AgentConfig.from_config(self.config),
            source_details={},
            create_session=self.stores.sessions.create,
            notifiers=self.notifier_service.list_summary() if self.notifier_service else [],
            enqueue_run_completed=self.stores.outbox.enqueue_run_completed,
            skill_registry=self.skill_registry,
        )

    async def start_scheduler(self) -> None:
        if not self.automation:
            raise RuntimeError("Automation runtime is not initialized")
        await self.automation.start_scheduler()

    def start_monitor(self) -> None:
        if not self.automation:
            raise RuntimeError("Automation runtime is not initialized")
        self.automation.start_monitor()
        self._register_slack_monitor()

    async def sync_google_sources(self) -> None:
        self.integrations.sync(self.config)
        await self.restart_monitor()

    async def restart_monitor(self) -> None:
        if not self.automation:
            return
        await self.automation.restart_monitor()
        self._register_slack_monitor()

    def _register_slack_monitor(self) -> None:
        slack = self.integrations.get_client("slack")
        if not isinstance(slack, SlackClient) or not self.monitor or not self.stores or not self.stores.monitor:
            return
        self.monitor.register(
            SlackMonitor(slack, state_store=self.stores.monitor, automation_store=self.stores.automations)
        )
        self.monitor.start()

    def start_indexing(self) -> None:
        self.knowledge.start_indexing()

    async def get_index_status(self) -> dict:
        return await self.knowledge.get_index_status()

    async def get_scheduler_status(self) -> dict:
        if not self.automation:
            return {"status": "disabled", "running_tasks": 0, "registered_handlers": []}
        return await self.automation.get_scheduler_status()

    async def get_outbox_status(self) -> dict:
        if not self.automation:
            return {"status": "disabled"}
        return await self.automation.get_outbox_status()

    async def get_outbox_health(self) -> dict:
        if not self.automation:
            return {"worker_running": False, "pending": 0, "ready": 0, "running": 0, "dead": 0}
        return await self.automation.get_outbox_health()

    async def replay_outbox_dead_events(self, event_ids: list[int]) -> dict:
        if not self.automation:
            return {"status": "disabled", "requested": event_ids, "replayed": [], "missing": event_ids, "skipped": []}
        return await self.automation.replay_outbox_dead_events(event_ids)

    async def prune_outbox_completed(self, *, before: datetime, limit: int) -> dict:
        if not self.automation:
            return {"status": "disabled", "deleted": 0, "before": before.isoformat(), "limit": limit}
        return await self.automation.prune_outbox_completed(before=before, limit=limit)


def get_runtime(request: Request) -> Runtime:
    try:
        runtime: Runtime | None = request.app.state.runtime
    except AttributeError:
        runtime = None
    if runtime is None or not runtime.connected:
        raise HTTPException(status_code=503, detail="Server is initializing")
    return runtime
