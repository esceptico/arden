import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException, Request

import arden.database as database
from arden.config import Config, get_config
from arden.core.factory import AgentConfig
from arden.execution.backend import ClientExecutionBackend
from arden.execution.gateway import ExecutorGateway
from arden.integrations import ALL_INTEGRATIONS, IntegrationRegistry
from arden.integrations.mutations import IDEMPOTENCY_LEDGER_SERVICE, IdempotencyLedger
from arden.integrations.slack.client import SlackClient
from arden.llm.base import CompletionClient
from arden.llm.openai_codex_catalog import refresh_codex_models
from arden.llm.router import close as llm_close
from arden.llm.router import get_completion_client
from arden.llm.router import init as llm_init
from arden.logging import get_logger
from arden.mcp.manager import MCPManager
from arden.memory.facts.capture.runner import FactCapture
from arden.memory.facts.capture.store import SessionConsumerWatermarkStore
from arden.memory.facts.consumer_store import FactConsumerStore
from arden.memory.facts.index import FactIndexProjection
from arden.memory.facts.ledger import FactLedger
from arden.memory.facts.maintenance.runner import (
    FactMaintenance,
    FactMaintenanceReviewer,
)
from arden.memory.facts.plan_store import FactPlanStore
from arden.memory.facts.service import FactService
from arden.memory.facts.synthesis import FactSynthesis
from arden.monitor.slack import SlackMonitor
from arden.notifiers.base import NotifierContext
from arden.notifiers.service import NotifierService
from arden.observability import init_tracing, shutdown_tracing
from arden.operator.runner import OperatorDeps
from arden.revisions import CollectionReport, ManagedFileRepository
from arden.server.app_control import AppControlService
from arden.server.runtime.automation import AutomationRuntime
from arden.server.runtime.config import RuntimeConfig
from arden.server.runtime.indexing import SearchIndexRuntime
from arden.server.runtime.knowledge import KnowledgeRuntime
from arden.server.runtime.memory_automation import MemoryAutomationRuntime
from arden.server.runtime.storage import StorageRuntime
from arden.server.runtime.wiki_projection import WikiProjectionRuntime
from arden.server.startup import startup_phase
from arden.server.state import RunRegistry
from arden.server.stores import Stores
from arden.services.session import SessionService
from arden.skills.registry import SkillMeta, SkillRegistry
from arden.skills.service import SkillService, get_skills_dirs
from arden.tools.connections import ConnectionService
from arden.tools.executor import ToolExecutor
from arden.wiki.approval_store import WikiRenameApprovalStore
from arden.wiki.approvals import WikiRenameApprovalCoordinator
from arden.wiki.constants import (
    WIKI_POST_COMMIT_SERVICE,
)
from arden.wiki.context import WikiContextBuilder, WikiPageIndexProjection
from arden.wiki.curation.completion import CompletionWikiEditCuratorReviewer
from arden.wiki.curation.engine import WikiEditCurator
from arden.wiki.curation.queue import WikiEditCuratorQueueStore
from arden.wiki.curation.worker import WikiEditCuratorWorker
from arden.wiki.maintenance.runner import (
    WikiMaintenance,
    WikiMaintenanceReviewer,
)
from arden.wiki.maintenance.store import WikiMaintenanceStore
from arden.wiki.service import WikiService

_logger = get_logger(__name__)

class Runtime:
    dispatch_session_message: (
        Callable[
            [str, str, str | None, bool | None, list[dict] | None],
            Awaitable[object],
        ]
        | None
    )
    resume_suspended_chat_run: Callable[[str, str], Awaitable[object]] | None
    respawn_background_agent: Callable[[str, str], Awaitable[None]] | None

    def __init__(self, config: Config | None = None):
        initial_config = config or get_config()
        self.integrations = IntegrationRegistry(ALL_INTEGRATIONS)
        self.integrations.sync(initial_config)
        self.connection_service = ConnectionService(self.integrations)
        self.idempotency_ledger = IdempotencyLedger(initial_config.arden_dir / "idempotency.sqlite3")
        self.run_registry = RunRegistry()
        self.knowledge = KnowledgeRuntime(initial_config)

        self.stores: Stores | None = None
        self.executor_gateway: ExecutorGateway | None = None
        self.automation: AutomationRuntime | None = None
        self.mcp_manager: MCPManager | None = None
        self.executor: ToolExecutor | None = None
        self.skill_registry: SkillRegistry | None = None
        self.skill_service: SkillService | None = None
        self.notifier_service: NotifierService | None = None
        self.dispatch_session_message = None
        self.resume_suspended_chat_run = None
        self.respawn_background_agent = None
        self.app_control: AppControlService | None = None
        self.wiki_repository: ManagedFileRepository | None = None
        self.wiki_service: WikiService | None = None
        self.wiki_context: WikiContextBuilder | None = None
        self.wiki_page_projection: WikiPageIndexProjection | None = None
        self.fact_index_projection: FactIndexProjection | None = None
        self.wiki_rename_coordinator: WikiRenameApprovalCoordinator | None = None
        self._wiki_approval_conn: database.aiosqlite.Connection | None = None
        self._wiki_maintenance_store: WikiMaintenanceStore | None = None
        self._wiki_curator_store: WikiEditCuratorQueueStore | None = None
        self.wiki_curator_worker: WikiEditCuratorWorker | None = None
        self.fact_service: FactService | None = None
        self._fact_plan_conn: database.aiosqlite.Connection | None = None
        self._fact_consumer_store: FactConsumerStore | None = None
        self._session_watermark_store: SessionConsumerWatermarkStore | None = None
        self._fact_ledger: FactLedger | None = None

        self._connected = False
        self._closing = False
        self._warmup_status = "pending"
        self._warmup_phase: str | None = None
        self._warmup_error: str | None = None
        self._warmup_capabilities = {
            "core": False,
            "model_catalog": False,
            "mcp": False,
            "search": False,
            "wiki_health": False,
            "automations": False,
            "storage": False,
        }

        self.config_runtime = RuntimeConfig(
            initial_config,
            get_integrations=lambda: self.integrations,
            get_knowledge=lambda: self.knowledge,
            get_stores=lambda: self.stores,
            sync_mcp=lambda config: self.sync_mcp(config),
            is_closing=lambda: self._closing,
            refresh_models=refresh_codex_models,
        )
        self.search = SearchIndexRuntime(
            get_config=lambda: self.config,
            config_service=self.config_runtime.service,
            knowledge=self.knowledge,
            reload_config=lambda: self.config_runtime.reload(),
            get_fact_projection=lambda: self.fact_index_projection,
            get_wiki_projection=lambda: self.wiki_page_projection,
        )
        self.storage = StorageRuntime(
            get_config=lambda: self.config,
            get_session_service=lambda: self.session_service,
            run_registry=self.run_registry,
            mark_ready=lambda: self.set_warmup_capability("storage"),
        )
        self.wiki_projection = WikiProjectionRuntime(self)
        self.memory_automation = MemoryAutomationRuntime(self)

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

    def warmup_status(self) -> dict:
        return {
            "status": self._warmup_status,
            "phase": self._warmup_phase,
            "error": self._warmup_error,
            "capabilities": dict(self._warmup_capabilities),
        }

    def storage_status(self) -> dict:
        return self.storage.status()

    def begin_warmup(self) -> None:
        self._warmup_status = "running"
        self._warmup_phase = "starting"
        self._warmup_error = None

    def set_warmup_phase(self, phase: str) -> None:
        self._warmup_phase = phase

    def set_warmup_capability(self, capability: str, ready: bool = True) -> None:
        self._warmup_capabilities[capability] = ready

    def complete_warmup(self) -> None:
        self._warmup_status = "ready"
        self._warmup_phase = None
        self._warmup_error = None

    def fail_warmup(self, error: BaseException) -> None:
        self._warmup_status = "error"
        self._warmup_phase = None
        self._warmup_error = f"{type(error).__name__}: {error}"

    def auxiliary_completion(self) -> tuple[CompletionClient, str, str | None]:
        model = self.config.auxiliary_model
        if model is None:
            raise RuntimeError("Auxiliary model is not configured")
        return (
            get_completion_client(model),
            model,
            self.config.reasoning_effort_for_role("auxiliary", model),
        )

    @property
    def session_service(self) -> SessionService | None:
        return self.stores.sessions if self.stores else None

    @property
    def wiki_maintenance_store(self) -> WikiMaintenanceStore | None:
        """Durable review state exposed read-only to the admin API."""

        return self._wiki_maintenance_store

    @property
    def embedding(self):
        return self.knowledge.embedding

    @property
    def indexer(self):
        return self.knowledge.indexer

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
        services[IDEMPOTENCY_LEDGER_SERVICE] = self.idempotency_ledger
        if self.wiki_service is not None:
            services["wiki"] = self.wiki_service
            services[WIKI_POST_COMMIT_SERVICE] = self.project_wiki_change_after_commit
        if self.automation:
            services["area_custodians"] = self.automation.custodians
            if self.automation.fact_capture_review is not None:
                services["fact_capture"] = self.automation.fact_capture_review
            if self.automation.fact_maintenance_review is not None:
                services["fact_maintenance"] = self.automation.fact_maintenance_review
            if self.automation.wiki_maintenance_review is not None:
                services["wiki_maintenance"] = self.automation.wiki_maintenance_review
        if self.stores is not None:
            services["area_work"] = self.stores.area_work
            # The workflow tool journals its agent spawns here so a
            # re-executed workflow replays its completed prefix.
            services["invocations"] = self.stores.invocations
        services.update(self.knowledge.tool_services())
        if self.automation_service:
            services["automation"] = self.automation_service
        if self.session_service:
            # Exposed so tools (read-only `session_list` / `session_read`)
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
        executor = ToolExecutor(
            mcp_tools=mcp_tools,
            get_services=lambda: self.tool_services,
            tool_overrides=config.tool_overrides,
        )
        if self.executor_gateway and self.stores:
            executor.router.set_client_backend(ClientExecutionBackend(self.executor_gateway, self.stores.invocations))
        return executor

    # --- Subsystem lifecycle ---

    async def reload_config(self) -> None:
        await self.search.reload()

    def embedding_model_available(self, model_id: str) -> bool:
        return self.search.model_available(model_id)

    async def update_embedding_model(self, model_id: str | None, confirmed: bool) -> None:
        await self.search.update_model(model_id, confirmed)

    async def start_indexing(self) -> bool:
        return await self.search.start()

    async def get_index_status(self) -> dict:
        return self.search.status()

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

    async def connect(self, *, defer_warmup: bool = False) -> None:
        if self._connected:
            return

        with startup_phase(_logger, "runtime.tracing_llm"):
            fact_ledger = FactLedger(self.config.memory_artifacts_dir / "facts") if self.config.memory else None
            init_tracing()
            llm_init(self.config)

        with startup_phase(_logger, "runtime.stores_schema"):
            self.stores = await Stores.connect(self.config, defer_recovery=defer_warmup)

        with startup_phase(_logger, "runtime.invocation_recovery"):
            self.executor_gateway = ExecutorGateway(
                self.stores.executor_devices,
                self.stores.executor_leases,
                self.stores.executor_commands,
                self.stores.invocations,
            )
            await self.executor_gateway.reconcile_open_invocations()

        with startup_phase(_logger, "runtime.wiki_facts"):
            if self.config.memory:
                await self._init_wiki()
            await self._init_facts(fact_ledger)
            await self._init_wiki_curator(reconcile_history=not defer_warmup)
            await self.knowledge.connect(self.stores)
            self._init_wiki_projection()

        with startup_phase(_logger, "runtime.index_sync"):
            rebuild_pending = await self.search.synchronize(deferred=defer_warmup)
            if self._wiki_maintenance_store is not None:
                self.wiki_projection.head = await self._wiki_maintenance_store.get_projection_revision()

        with startup_phase(_logger, "runtime.skills"):
            self._init_skills()
            await self.hydrate_device_skills()

        with startup_phase(_logger, "runtime.notifiers_automation"):
            await self._init_notifiers()
            self._init_automation()

        if not defer_warmup:
            with startup_phase(_logger, "runtime.wiki_health"):
                await self._project_initial_wiki_health()

        with startup_phase(_logger, "runtime.mcp_tools"):
            if not defer_warmup:
                await self._init_mcp()
            self._init_tools()
            if not defer_warmup:
                self.set_warmup_capability("mcp")

        if not defer_warmup:
            with startup_phase(_logger, "runtime.workers"):
                if self.wiki_curator_worker is not None:
                    self.wiki_curator_worker.start()

        self._connected = True
        self.set_warmup_capability("core")
        with startup_phase(_logger, "runtime.index_rebuild_start"):
            if rebuild_pending and not defer_warmup:
                await self.start_indexing()
        if not defer_warmup:
            self.set_warmup_capability("search")
            self.set_warmup_capability("wiki_health")
        _logger.info(
            "Runtime ready",
            integrations=len(self.integrations.clients),
            tools=len(self.executor.registry),
        )

    def _init_skills(self) -> None:
        self.skill_registry = SkillRegistry()
        self.skill_registry.load(get_skills_dirs())
        self.skill_service = SkillService(self.skill_registry)

    async def hydrate_device_skills(self) -> None:
        """Load cached device-advertised skills into the registry so they
        survive server restarts and stay usable while the device is offline."""
        if self.stores is None or self.skill_registry is None:
            raise RuntimeError("Device skills require initialized stores and skill registry")
        entries = []
        for skill in await self.stores.device_skills.all_loaded():
            meta = SkillMeta(
                name=skill.name,
                description=skill.description,
                path=Path("~/.agents/skills") / skill.name,
                location="device",
            )
            entries.append((meta, skill.body or ""))
        self.skill_registry.set_device_skills(entries)

    def _init_wiki_projection(self) -> None:
        if self.fact_service is None or self.wiki_service is None:
            return
        projection = WikiPageIndexProjection(
            self.wiki_service,
            lambda: self.search_index,
            self.fact_service.revision,
            on_state_change=lambda _state: self.project_wiki_health(),
            fact_revision_hint=self.fact_service.current_revision,
        )
        self.wiki_page_projection = projection
        self.wiki_context = WikiContextBuilder(self.wiki_service, projection, self.fact_service.revision)

    async def _init_wiki(self) -> None:
        wiki_root = self.config.memory_artifacts_dir / "wiki"
        self.wiki_repository = ManagedFileRepository(
            wiki_root / "pages",
            history_root=wiki_root / ".wiki-history",
        )
        self.wiki_service = WikiService(self.wiki_repository)
        # The wiki approval store must not share Stores' writer connection: it
        # owns its own lock/transactions while persisting in sessions.db.
        approval_conn = await database.connect(self.config.sessions_db_path)
        try:
            approval_store = WikiRenameApprovalStore(approval_conn)
            await approval_store.init_schema()
        except BaseException:
            await approval_conn.close()
            raise
        self._wiki_approval_conn = approval_conn
        self.wiki_rename_coordinator = WikiRenameApprovalCoordinator(self.wiki_service, approval_store)

    async def _init_facts(self, ledger: FactLedger | None) -> None:
        self.fact_service = None
        self._fact_ledger = None
        self.knowledge.set_fact_service(None)
        if ledger is None:
            return
        connection = await database.connect(self.config.memory_db_path)
        consumers: FactConsumerStore | None = None
        maintenance: WikiMaintenanceStore | None = None
        session_watermarks: SessionConsumerWatermarkStore | None = None
        try:
            plans = FactPlanStore(connection)
            await plans.init_schema()
            consumers = await FactConsumerStore.open(self.config.memory_db_path)
            maintenance = await WikiMaintenanceStore.open(self.config.memory_db_path)
            session_watermarks = await SessionConsumerWatermarkStore.open(self.config.memory_db_path)
        except BaseException:
            if session_watermarks is not None:
                await session_watermarks.close()
            if maintenance is not None:
                await maintenance.close()
            if consumers is not None:
                await consumers.close()
            await connection.close()
            raise
        self._fact_plan_conn = connection
        self._fact_consumer_store = consumers
        self._wiki_maintenance_store = maintenance
        self._session_watermark_store = session_watermarks
        self._fact_ledger = ledger
        self.fact_index_projection = FactIndexProjection(ledger, lambda: self.search_index)
        self.fact_service = FactService(
            ledger,
            plans,
            post_commit=self._after_fact_commit,
            ranked_search=self.fact_index_projection.ranked_fact_ids,
        )
        self.knowledge.set_fact_service(self.fact_service)

    async def _init_wiki_curator(self, *, reconcile_history: bool = True) -> None:
        if self.fact_service is None or self.wiki_service is None:
            return
        store = await WikiEditCuratorQueueStore.open(self.config.memory_db_path)
        self._wiki_curator_store = store
        try:
            if reconcile_history:
                await self._reconcile_wiki_curator_history()

            model = self.config.memory_model
            if not model:
                return
            curator = WikiEditCurator(
                self.wiki_service,
                self.fact_service,
                CompletionWikiEditCuratorReviewer(
                    get_completion_client(model),
                    model,
                    reasoning_effort=self.knowledge._memory_reasoning_effort(model),
                ),
            )
            self.wiki_curator_worker = WikiEditCuratorWorker(store, curator, self.fact_service)
        except BaseException:
            await store.close()
            self._wiki_curator_store = None
            raise

    async def _reconcile_wiki_curator_history(self) -> None:
        if self._wiki_curator_store is None or self.wiki_service is None:
            return
        watermark = await self._wiki_curator_store.get_reconciliation_revision()
        expected_watermark = watermark
        head = self.wiki_service.repository.head
        if head is None or head == watermark:
            return
        try:
            commits = await asyncio.to_thread(
                self.wiki_service.repository.history,
                start=head,
                stop_before=watermark,
            )
        except KeyError:
            _logger.warning("wiki curator watermark is unreachable; replaying reachable history", watermark=watermark)
            commits = await asyncio.to_thread(self.wiki_service.repository.history, start=head)
        for commit in reversed(commits):
            if (commit.actor, commit.origin) == ("user:desktop", "desktop"):
                await self._wiki_curator_store.enqueue(commit.commit_id)
        await self._wiki_curator_store.record_reconciliation_revision(expected=expected_watermark, revision=head)

    async def _project_initial_wiki_health(self) -> None:
        await self.project_wiki_health()
        if (
            self.wiki_service is not None
            and self._wiki_maintenance_store is not None
            and self.wiki_projection.head is None
            and self.wiki_service.repository.head is not None
        ):
            self.wiki_projection.head = self.wiki_service.repository.head
            await self._wiki_maintenance_store.record_projection_revision(
                expected_revision=None,
                revision=self.wiki_projection.head,
            )

    async def complete_deferred_runtime_warmup(self) -> None:
        """Finish rebuildable initialization after the API is already live."""

        self.set_warmup_phase("runtime.interrupted_state_recovery")
        with startup_phase(_logger, "warmup.runtime.interrupted_state_recovery"):
            await self.stores.reconcile_interrupted_state()

        self.set_warmup_phase("runtime.curator_reconciliation")
        with startup_phase(_logger, "warmup.runtime.curator_reconciliation"):
            await self._reconcile_wiki_curator_history()

        self.set_warmup_phase("runtime.index_sync")
        with startup_phase(_logger, "warmup.runtime.index_sync"):
            rebuild_pending = await self.search.synchronize()
        self.set_warmup_capability("search")

        self.set_warmup_phase("runtime.wiki_health")
        with startup_phase(_logger, "warmup.runtime.wiki_health"):
            await self._project_initial_wiki_health()
        self.set_warmup_capability("wiki_health")

        if rebuild_pending:
            self.set_warmup_phase("runtime.index_rebuild_start")
            with startup_phase(_logger, "warmup.runtime.index_rebuild_start"):
                await self.start_indexing()

        self.set_warmup_phase("runtime.workers")
        with startup_phase(_logger, "warmup.runtime.workers"):
            if self.wiki_curator_worker is not None:
                self.wiki_curator_worker.start()

        self.set_warmup_phase("runtime.mcp")
        with startup_phase(_logger, "warmup.runtime.mcp"):
            await self.sync_mcp()
        self.set_warmup_capability("mcp")

    async def run_storage_maintenance_once(self) -> dict:
        return await self.storage.run_once()

    async def plan_storage_cleanup(self, request) -> dict:
        return await self.storage.plan(request)

    async def execute_storage_cleanup(self, request) -> dict:
        return await self.storage.execute(request)

    async def list_storage_backups(self) -> list[dict]:
        return await self.storage.backups()

    async def set_storage_backup_keep(self, relative_path: str, *, keep: bool) -> list[dict]:
        return await self.storage.set_backup_keep(relative_path, keep=keep)

    def start_storage_maintenance(self, *, interval_seconds: float = 3600) -> None:
        self.storage.start(interval_seconds=interval_seconds)

    async def enqueue_wiki_user_edit(self, commit_id: str) -> None:
        """Durably enqueue one exact user wiki commit for background curation."""

        if self.wiki_curator_worker is not None:
            await self.wiki_curator_worker.enqueue_user_commit(commit_id)
        elif self._wiki_curator_store is not None:
            await self._wiki_curator_store.enqueue(commit_id)
        else:
            raise RuntimeError("wiki edit curator queue is unavailable")

    def require_wiki_user_edit_queue(self) -> None:
        """Reject edits before commit when their durable curation queue is unavailable."""

        if self._wiki_curator_store is None:
            raise RuntimeError("wiki edit curator queue is unavailable")

    async def _request_fact_synthesis(self) -> None:
        if self.automation is not None:
            await self.automation.request_fact_synthesis()

    async def request_fact_maintenance(self) -> None:
        if self.automation is not None:
            await self.automation.request_fact_maintenance()

    async def _after_fact_commit(self) -> None:
        try:
            if self.fact_index_projection is not None:
                await self.fact_index_projection.sync()
        finally:
            try:
                await self._request_fact_synthesis()
            finally:
                await self.project_wiki_health()

    async def _after_automation_finished(self, task_id: str, success: bool) -> None:
        await self.wiki_projection.after_automation_finished(task_id, success, self._record_retention_checkpoint)

    async def _record_retention_checkpoint(self) -> None:
        await self.memory_automation.record_retention_checkpoint()

    def _get_fact_synthesis(self) -> FactSynthesis | None:
        return self.memory_automation.fact_synthesis()

    def _create_fact_capture(self) -> FactCapture | None:
        return self.memory_automation.fact_capture()

    def _create_fact_maintenance(self, reviewer: FactMaintenanceReviewer) -> FactMaintenance | None:
        return self.memory_automation.fact_maintenance(reviewer)

    def _create_wiki_maintenance(self, reviewer: WikiMaintenanceReviewer) -> WikiMaintenance | None:
        return self.memory_automation.wiki_maintenance(reviewer)

    async def _synthesis_is_current(self) -> bool:
        return await self.memory_automation.synthesis_is_current()

    async def project_wiki_health(self) -> str | None:
        return await self.wiki_projection.project_health()

    async def project_wiki_state(self) -> None:
        await self.wiki_projection.project_change()

    async def project_wiki_change_after_commit(self) -> bool:
        return await self.wiki_projection.enqueue_after_commit()

    async def enqueue_startup_wiki_projection(self) -> bool:
        return await self.wiki_projection.enqueue_startup()

    async def project_wiki_change(self) -> None:
        await self.wiki_projection.project_change()

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
        collect_managed_history = (
            self._collect_managed_history if self._fact_ledger is not None and self.wiki_service is not None else None
        )
        self.automation = AutomationRuntime(
            stores=self.stores,
            config=self.config,
            build_operator_deps=self.build_operator_deps,
            get_calendar_source=lambda: self.integrations.get_client("calendar"),
            get_slack_client=lambda: self.integrations.get_client("slack"),
            resolve_auxiliary_completion=self.auxiliary_completion,
            project_wiki_state=self.project_wiki_state,
            get_fact_capture=self._create_fact_capture,
            get_fact_maintenance=self._create_fact_maintenance,
            get_fact_synthesis=self._get_fact_synthesis,
            get_wiki_maintenance=self._create_wiki_maintenance,
            synthesis_is_current=self._synthesis_is_current,
            on_automation_finished=self._after_automation_finished,
            get_wiki_service=lambda: self.wiki_service,
            get_notifiers=lambda: self.notifier_service,
            collect_managed_history=collect_managed_history,
        )

    def _collect_managed_history(self) -> tuple[CollectionReport, CollectionReport]:
        return self.memory_automation.collect_managed_history()

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
        await self.search.cancel()
        await self.storage.stop()
        if self.automation:
            await self.automation.stop()
        if self.wiki_curator_worker:
            await self.wiki_curator_worker.stop()
            self.wiki_curator_worker = None
        await self.knowledge.stop()

        # Phase 3: close resources
        if self.mcp_manager:
            await self.mcp_manager.close()
        await self.knowledge.close()
        if self._wiki_maintenance_store:
            await self._wiki_maintenance_store.close()
            self._wiki_maintenance_store = None
        if self._wiki_curator_store:
            await self._wiki_curator_store.close()
            self._wiki_curator_store = None
        if self._session_watermark_store:
            await self._session_watermark_store.close()
            self._session_watermark_store = None
        if self._fact_consumer_store:
            await self._fact_consumer_store.close()
            self._fact_consumer_store = None
        if self._fact_plan_conn:
            await self._fact_plan_conn.close()
            self._fact_plan_conn = None
        self.fact_service = None
        self._fact_ledger = None
        self.fact_index_projection = None
        self.wiki_context = None
        self.wiki_page_projection = None
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
        if self.fact_service is not None:
            ids.append("memory")
        return ids

    def get_integration_errors(self) -> dict[str, str]:
        errors = dict(self.integrations.errors)
        if self.search.error:
            errors["index"] = self.search.error
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
            dispatch_session_message=self.dispatch_session_message,
            wiki_context=self.wiki_context,
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
            wiki_context=self.wiki_context,
            config=AgentConfig.from_config(self.config),
            source_details={},
            session_create=self.stores.sessions.create,
            notifiers=self.notifier_service.list_summary() if self.notifier_service else [],
            enqueue_run_completed=self.stores.outbox.enqueue_run_completed,
            skill_registry=self.skill_registry,
        )

    async def start_scheduler(self) -> None:
        if not self.automation:
            raise RuntimeError("Automation runtime is not initialized")
        await self.automation.start_scheduler()
        self.set_warmup_capability("automations")

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
