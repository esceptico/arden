import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime
from pathlib import Path

from fastapi import HTTPException, Request

import arden.database as database
from arden.areas.agent import CUSTODIAN_ACTOR_PREFIX
from arden.automation.builtins import BUILTINS
from arden.config import Config, get_config
from arden.constants import (
    BUILTIN_MEMORY_CONSOLIDATE_ID,
    BUILTIN_MEMORY_RETENTION_ID,
    BUILTIN_MEMORY_SYNTHESIZE_ID,
    BUILTIN_WIKI_MAINTENANCE_ID,
)
from arden.core.factory import AgentConfig
from arden.execution.backend import ClientExecutionBackend
from arden.execution.gateway import ExecutorGateway
from arden.integrations import ALL_INTEGRATIONS, IntegrationRegistry
from arden.integrations.slack.client import SlackClient
from arden.llm.base import CompletionClient
from arden.llm.models import Provider, get_embedding_model
from arden.llm.openai_codex_catalog import refresh_codex_models
from arden.llm.router import close as llm_close
from arden.llm.router import get_completion_client
from arden.llm.router import init as llm_init
from arden.logging import get_logger
from arden.mcp.manager import MCPManager
from arden.memory.facts.completion_dream import CompletionFactDreamRenderer
from arden.memory.facts.completion_renderer import CompletionFactSynthesisRenderer
from arden.memory.facts.consumer_store import FactConsumerStore
from arden.memory.facts.dream import FactDream
from arden.memory.facts.index import FACT_SEARCH_SOURCE, FactIndexProjection
from arden.memory.facts.ledger import FactLedger
from arden.memory.facts.maintenance.runner import (
    CONSUMER_ID as FACT_MAINTENANCE_CONSUMER_ID,
)
from arden.memory.facts.maintenance.runner import (
    FactMaintenance,
    FactMaintenanceReviewer,
)
from arden.memory.facts.plan_store import FactPlanStore
from arden.memory.facts.service import FactService
from arden.memory.facts.synthesis import CONSUMER_ID as FACT_SYNTHESIS_CONSUMER_ID
from arden.memory.facts.synthesis import FactSynthesis
from arden.monitor.slack import SlackMonitor
from arden.notifiers.base import NotifierContext
from arden.notifiers.service import NotifierService
from arden.observability import init_tracing, shutdown_tracing
from arden.operator.runner import OperatorDeps
from arden.revisions import CollectionReport, ManagedFileRepository, RevisionConflictError
from arden.server.app_control import AppControlService
from arden.server.indexer import IndexProgress, IndexStatus
from arden.server.runtime.automation import AutomationRuntime
from arden.server.runtime.config import RuntimeConfig
from arden.server.runtime.knowledge import KnowledgeRuntime
from arden.server.state import RunRegistry
from arden.server.stores import Stores
from arden.server.wiki_health import dangling_fact_citation_issues
from arden.services.session import SessionService
from arden.skills.registry import SkillMeta, SkillRegistry
from arden.skills.service import SkillService, get_skills_dirs
from arden.tools.connections import ConnectionService
from arden.tools.executor import ToolExecutor
from arden.wiki.approval_store import WikiRenameApprovalStore
from arden.wiki.approvals import WikiRenameApprovalCoordinator
from arden.wiki.constants import (
    AUTOMATIONS_PATH_PREFIX,
    README_FILENAME,
    WIKI_HEALTH_ORIGIN,
    WIKI_MAINTENANCE_ACTOR,
    WIKI_POST_COMMIT_SERVICE,
)
from arden.wiki.context import WIKI_PAGE_SOURCE, WikiContextBuilder, WikiPageIndexProjection
from arden.wiki.curation.completion import CompletionWikiEditCuratorReviewer
from arden.wiki.curation.engine import WikiEditCurator
from arden.wiki.curation.queue import WikiEditCuratorQueueStore
from arden.wiki.curation.worker import WikiEditCuratorWorker
from arden.wiki.health import (
    WikiHealthIndex,
    WikiHealthInput,
    WikiHealthIssue,
    WikiHealthIssueCode,
    WikiHealthIssueOwner,
    WikiHealthProjector,
    WikiHealthWorker,
)
from arden.wiki.maintenance.runner import (
    WikiMaintenance,
    WikiMaintenanceReviewer,
)
from arden.wiki.maintenance.store import WikiMaintenanceStore
from arden.wiki.models import WikiChangesReport
from arden.wiki.service import WIKI_RENAME_ORIGIN, WikiService

_logger = get_logger(__name__)

_HEALTH_PHASE_IDS = frozenset(
    {
        BUILTIN_MEMORY_CONSOLIDATE_ID,
        BUILTIN_MEMORY_RETENTION_ID,
        BUILTIN_MEMORY_SYNTHESIZE_ID,
        BUILTIN_WIKI_MAINTENANCE_ID,
    }
)
_HEALTH_PHASES = tuple(spec for spec in BUILTINS if spec.task_id in _HEALTH_PHASE_IDS)


class Runtime:
    dispatch_session_message: (
        Callable[
            [str, str, str | None, bool | None, list[dict] | None],
            Awaitable[object],
        ]
        | None
    )
    resume_suspended_chat_run: Callable[[str, str], Awaitable[object]] | None

    def __init__(self, config: Config | None = None):
        initial_config = config or get_config()
        self.integrations = IntegrationRegistry(ALL_INTEGRATIONS)
        self.integrations.sync(initial_config)
        self.connection_service = ConnectionService(self.integrations)
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
        self._wiki_health_lock = asyncio.Lock()
        self._wiki_projection_lock = asyncio.Lock()
        self._wiki_change_head: str | None = None
        self.fact_service: FactService | None = None
        self._fact_plan_conn: database.aiosqlite.Connection | None = None
        self._fact_consumer_store: FactConsumerStore | None = None
        self._fact_ledger: FactLedger | None = None

        self._connected = False
        self._closing = False
        self._index_lock = asyncio.Lock()
        self._index_task: asyncio.Task[None] | None = None
        self._index_progress = IndexProgress(status=IndexStatus.PENDING)
        self._index_error: str | None = None

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
        if self.wiki_service is not None:
            services["wiki"] = self.wiki_service
            services[WIKI_POST_COMMIT_SERVICE] = self.project_wiki_change_after_commit
        if self.automation:
            services["area_custodians"] = self.automation.custodians
            if self.automation.fact_maintenance_review is not None:
                services["fact_maintenance"] = self.automation.fact_maintenance_review
            if self.automation.wiki_maintenance_review is not None:
                services["wiki_maintenance"] = self.automation.wiki_maintenance_review
        if self.stores is not None:
            services["area_work"] = self.stores.area_work
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
        async with self._index_lock:
            was_indexing = self._index_is_running()
            await self._cancel_indexing()
            previous_embedding = self.config.embedding
            await self.config_runtime.reload()

            if self.config.embedding is None:
                self._index_progress = IndexProgress(status=IndexStatus.DISABLED)
                self._index_error = None
            elif not self.indexer.vector_enabled:
                self._index_progress = IndexProgress(status=IndexStatus.ERROR)
                self._index_error = "Vector index is unavailable; full-text search remains available"
            elif (
                was_indexing
                or self.config.embedding != previous_embedding
                or self.indexer.needs_rebuild
                or self._index_progress.status is IndexStatus.ERROR
            ):
                self._start_indexing_locked()

    async def _after_config_reload(self) -> None:
        return

    def embedding_model_available(self, model_id: str) -> bool:
        model = get_embedding_model(model_id)
        if model.provider is Provider.OPENAI:
            return bool(self.config.openai_api_key)
        if model.provider is Provider.GOOGLE:
            return bool(self.config.gemini_api_key)
        return model.provider is Provider.CUSTOM

    async def update_embedding_model(self, model_id: str | None, confirmed: bool) -> None:
        async with self._index_lock:
            if model_id == self.config.embedding_model:
                return
            if self._index_is_running():
                raise HTTPException(status_code=409, detail="Embedding reindex is already running")
            if not confirmed:
                raise HTTPException(
                    status_code=409,
                    detail="Changing the embedding model re-embeds all facts and wiki pages; resend with confirmed=true",
                )
            if model_id is not None:
                try:
                    available = self.embedding_model_available(model_id)
                except ValueError as error:
                    raise HTTPException(status_code=400, detail=str(error)) from error
                if not available:
                    raise HTTPException(status_code=400, detail=f"Embedding model {model_id!r} is not available")
            await self.config_service.update(embedding_model=model_id)
            if self.config.embedding is None:
                self._index_progress = IndexProgress(status=IndexStatus.DISABLED)
                self._index_error = None
                return
            if not self.indexer.vector_enabled:
                self._index_progress = IndexProgress(status=IndexStatus.ERROR)
                self._index_error = "Vector index is unavailable; full-text search remains available"
                return
            self._start_indexing_locked()

    async def start_indexing(self) -> bool:
        async with self._index_lock:
            if self._index_is_running():
                raise HTTPException(status_code=409, detail="Embedding reindex is already running")
            if self.config.embedding is None:
                self._index_progress = IndexProgress(status=IndexStatus.DISABLED)
                self._index_error = None
                return False
            if not self.indexer.vector_enabled:
                self._index_progress = IndexProgress(status=IndexStatus.ERROR)
                self._index_error = "Vector index is unavailable; full-text search remains available"
                return False
            self._start_indexing_locked()
            return True

    def _start_indexing_locked(self) -> None:
        self._index_progress = IndexProgress(status=IndexStatus.INDEXING, phase="facts")
        self._index_error = None
        self._index_task = asyncio.create_task(self._run_indexing())

    def _index_is_running(self) -> bool:
        return self._index_task is not None and not self._index_task.done()

    async def _run_indexing(self) -> None:
        try:
            rebuilt_sources: set[str] = set()
            if self.fact_index_projection is not None:
                if (
                    await self.fact_index_projection.sync(
                        progress_callback=self._index_progress_callback("facts"),
                        raise_on_error=True,
                    )
                    is None
                ):
                    raise RuntimeError("fact index rebuild did not complete")
                rebuilt_sources.add(FACT_SEARCH_SOURCE)
            if self.wiki_page_projection is not None:
                if (
                    await self.wiki_page_projection.sync(
                        progress_callback=self._index_progress_callback("wiki"),
                        raise_on_error=True,
                    )
                    is None
                ):
                    raise RuntimeError("wiki index rebuild did not complete")
                rebuilt_sources.add(WIKI_PAGE_SOURCE)
            if self.search_index is not None:
                existing_sources = set(await self.search_index.store.get_stats())
                unavailable = {FACT_SEARCH_SOURCE, WIKI_PAGE_SOURCE}.intersection(existing_sources).difference(
                    rebuilt_sources
                )
                if unavailable:
                    raise RuntimeError(f"cannot rebuild unavailable sources: {', '.join(sorted(unavailable))}")
                await self.search_index.store.mark_embedding_ready()
                self.indexer.needs_rebuild = False
            self._index_progress.status = IndexStatus.DONE
            self._index_progress.phase = None
        except asyncio.CancelledError:
            self._index_progress.status = IndexStatus.PENDING
            self._index_progress.phase = None
            raise
        except Exception as error:
            self._index_error = f"{type(error).__name__}: {error}"
            self._index_progress.status = IndexStatus.ERROR
            self._index_progress.phase = None

    def _index_progress_callback(self, phase: str) -> Callable[[int, int], None]:
        def update(done: int, total: int) -> None:
            self._index_progress.phase = phase
            self._index_progress.done = done
            self._index_progress.total = total

        return update

    async def get_index_status(self) -> dict:
        retry_at = self.indexer.rate_limit_retry_at
        return {
            "state": self._index_progress.status.value,
            "model": self.config.embedding_model,
            "phase": self._index_progress.phase,
            "done": self._index_progress.done,
            "total": self._index_progress.total,
            "retry_at": retry_at.isoformat() if retry_at is not None else None,
            "error": self._index_error,
        }

    async def _cancel_indexing(self) -> None:
        if self._index_task is None or self._index_task.done():
            return
        self._index_task.cancel()
        try:
            await self._index_task
        except asyncio.CancelledError:
            pass

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

        fact_ledger = FactLedger(self.config.memory_artifacts_dir / "facts") if self.config.memory else None
        init_tracing()
        llm_init(self.config)
        self.stores = await Stores.connect(self.config)
        self.executor_gateway = ExecutorGateway(
            self.stores.executor_devices,
            self.stores.executor_leases,
            self.stores.executor_commands,
            self.stores.invocations,
        )
        if self.config.memory:
            await self._init_wiki()
        await self._init_facts(fact_ledger)
        await self._init_wiki_curator()
        await self.knowledge.connect(self.stores)
        rebuild_pending = (
            self.indexer.needs_rebuild and self.config.embedding is not None and self.indexer.vector_enabled
        )
        if self.fact_service is not None:
            fact_projection = FactIndexProjection(self.fact_service, lambda: self.search_index)
            self.fact_index_projection = fact_projection
            if not rebuild_pending:
                try:
                    await fact_projection.sync(raise_on_error=self.config.embedding is not None)
                except Exception:
                    _logger.warning("initial fact index sync failed", exc_info=True)
        if self.fact_service is not None and self.wiki_service is not None:
            projection = WikiPageIndexProjection(
                self.wiki_service,
                lambda: self.search_index,
                self.fact_service.revision,
                on_state_change=lambda _state: self.project_wiki_health(),
            )
            self.wiki_page_projection = projection
            self.wiki_context = WikiContextBuilder(self.wiki_service, projection, self.fact_service.revision)
            if not rebuild_pending:
                try:
                    await projection.sync(raise_on_error=self.config.embedding is not None)
                except Exception:
                    _logger.warning("initial wiki index sync failed", exc_info=True)
        if self.config.embedding is None:
            self._index_progress = IndexProgress(status=IndexStatus.DISABLED)
        elif rebuild_pending:
            self._index_progress = IndexProgress(status=IndexStatus.PENDING)
        elif self.indexer.vector_enabled:
            failed_projection = next(
                (
                    projection.last_state
                    for projection in (self.fact_index_projection, self.wiki_page_projection)
                    if projection is not None and projection.last_state.status != "ready"
                ),
                None,
            )
            if failed_projection is None:
                self._index_progress = IndexProgress(status=IndexStatus.DONE)
            else:
                self._index_progress = IndexProgress(status=IndexStatus.ERROR)
                self._index_error = failed_projection.detail or "Initial index rebuild did not complete"
        else:
            self._index_progress = IndexProgress(status=IndexStatus.ERROR)
            self._index_error = "Vector index is unavailable; full-text search remains available"
        if self._wiki_maintenance_store is not None:
            self._wiki_change_head = await self._wiki_maintenance_store.get_projection_revision()
        self._init_skills()
        if self.stores:
            await self.hydrate_device_skills()
        await self._init_notifiers()
        self._init_automation()
        await self.project_wiki_health()
        if (
            self.wiki_service is not None
            and self._wiki_maintenance_store is not None
            and self._wiki_change_head is None
            and self.wiki_service.repository.head is not None
        ):
            self._wiki_change_head = self.wiki_service.repository.head
            await self._wiki_maintenance_store.record_projection_revision(
                expected_revision=None,
                revision=self._wiki_change_head,
            )
        await self._init_mcp()
        self._init_tools()
        if self.wiki_curator_worker is not None:
            self.wiki_curator_worker.start()

        self._connected = True
        if rebuild_pending:
            await self.start_indexing()
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
        assert self.stores and self.skill_registry
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
        try:
            plans = FactPlanStore(connection)
            await plans.init_schema()
            consumers = await FactConsumerStore.open(self.config.memory_db_path)
            maintenance = await WikiMaintenanceStore.open(self.config.memory_db_path)
        except BaseException:
            if maintenance is not None:
                await maintenance.close()
            if consumers is not None:
                await consumers.close()
            await connection.close()
            raise
        self._fact_plan_conn = connection
        self._fact_consumer_store = consumers
        self._wiki_maintenance_store = maintenance
        self._fact_ledger = ledger
        self.fact_service = FactService(ledger, plans, post_commit=self._after_fact_commit)
        self.knowledge.set_fact_service(self.fact_service)

    async def _init_wiki_curator(self) -> None:
        if self.fact_service is None or self.wiki_service is None:
            return
        store = await WikiEditCuratorQueueStore.open(self.config.memory_db_path)
        self._wiki_curator_store = store
        try:
            commits = await asyncio.to_thread(self.wiki_service.repository.history)
            for commit in reversed(commits):
                if (commit.actor, commit.origin) == ("user:desktop", "desktop"):
                    await store.enqueue(commit.commit_id)

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
        wiki_changed = self.wiki_service is not None and self.wiki_service.repository.head != self._wiki_change_head
        if task_id not in _HEALTH_PHASE_IDS and not wiki_changed:
            return
        if success and task_id == BUILTIN_MEMORY_RETENTION_ID:
            await self._record_retention_checkpoint()
        if wiki_changed:
            await self.project_wiki_state()
        else:
            await self.project_wiki_health()

    async def _record_retention_checkpoint(self) -> None:
        """Persist Retention coverage only after the backend proves no review remains due."""

        if self._fact_ledger is None or self.fact_service is None or self._fact_consumer_store is None:
            return
        revision, evaluated_at, due_reviews = await asyncio.to_thread(self._fact_ledger.due_review_snapshot)
        if due_reviews or await self.fact_service.revision() != revision:
            return
        await self._fact_consumer_store.record_retention_checkpoint(
            BUILTIN_MEMORY_RETENTION_ID,
            revision=revision,
            evaluated_at=evaluated_at,
        )

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

    def _get_fact_dream(self) -> FactDream | None:
        model = self.config.memory_model
        if self._fact_ledger is None or self.wiki_service is None or not model:
            return None
        return FactDream(
            self._fact_ledger,
            self.wiki_service,
            CompletionFactDreamRenderer(
                get_completion_client(model),
                model,
                reasoning_effort=self.knowledge._memory_reasoning_effort(model),
            ),
            timezone_name=self.config.memory_timezone,
        )

    def _create_fact_maintenance(self, reviewer: FactMaintenanceReviewer) -> FactMaintenance | None:
        model = self.config.memory_model
        if (
            self.fact_service is None
            or self._fact_consumer_store is None
            or self.wiki_service is None
            or self._wiki_maintenance_store is None
            or not model
        ):
            return None
        return FactMaintenance(
            self.fact_service,
            self._fact_consumer_store,
            reviewer,
            wiki=self.wiki_service,
            candidate_provider=self.fact_index_projection,
        )

    def _create_wiki_maintenance(self, reviewer: WikiMaintenanceReviewer) -> WikiMaintenance | None:
        if self._wiki_maintenance_store is None or self.wiki_service is None or not self.config.memory_model:
            return None
        return WikiMaintenance(self._wiki_maintenance_store, self.wiki_service, reviewer)

    async def _synthesis_is_current(self) -> bool:
        if self.fact_service is None or self._fact_consumer_store is None:
            return False
        before = await self.fact_service.revision()
        watermark = await self._fact_consumer_store.get(FACT_SYNTHESIS_CONSUMER_ID)
        after = await self.fact_service.revision()
        if before != after:
            return False
        if before is None:
            return watermark is None
        return watermark is not None and watermark.revision == before

    async def project_wiki_health(self) -> str | None:
        """Refresh the derived health page and surface projection failures."""

        if (
            self._fact_ledger is None
            or self.fact_service is None
            or self._fact_consumer_store is None
            or self._wiki_maintenance_store is None
            or self.wiki_service is None
            or self.wiki_page_projection is None
        ):
            return None if self.wiki_service is None else self.wiki_service.repository.head
        async with self._wiki_health_lock:
            for attempt in range(2):
                try:
                    fact_revision, _evaluated_at, due_reviews = await asyncio.to_thread(
                        self._fact_ledger.due_review_snapshot
                    )
                    fact_maintenance = await self._fact_consumer_store.get(FACT_MAINTENANCE_CONSUMER_ID)
                    synthesis = await self._fact_consumer_store.get(FACT_SYNTHESIS_CONSUMER_ID)
                    retention = await self._fact_consumer_store.get_retention_checkpoint(BUILTIN_MEMORY_RETENTION_ID)
                    maintenance = await self._wiki_maintenance_store.get_watermark()
                    completed_runs = await self.stores.automations.latest_completed_runs(
                        tuple(spec.task_id for spec in _HEALTH_PHASES)
                    )
                    report = await asyncio.to_thread(
                        self.wiki_service.changes_since,
                        None,
                        include_diffs=False,
                    )
                    citation_issues = await asyncio.to_thread(
                        dangling_fact_citation_issues,
                        self._fact_ledger,
                        report,
                    )
                    if await self.fact_service.revision() != fact_revision:
                        if attempt == 0:
                            continue
                        raise RevisionConflictError("fact ledger changed during both wiki health projection attempts")

                    observed = self._semantic_wiki_revision(report)
                    maintenance_revision, maintenance_revision_known = self._semantic_wiki_revision_at(
                        report,
                        None if maintenance is None else maintenance.revision,
                    )
                    index_state = self.wiki_page_projection.last_state
                    index_revision, index_revision_known = self._semantic_wiki_revision_at(
                        report,
                        index_state.wiki_head,
                    )
                    index_status = index_state.status
                    index_detail = index_state.detail
                    if not index_revision_known:
                        index_status = "error"
                        index_detail = "indexed wiki revision is not reachable from the current history"
                    if not maintenance_revision_known:
                        maintenance_revision = None

                    workers_by_id = {
                        BUILTIN_MEMORY_CONSOLIDATE_ID: WikiHealthWorker(
                            "Memory Maintenance",
                            completed_runs.get(BUILTIN_MEMORY_CONSOLIDATE_ID),
                            None if fact_maintenance is None else fact_maintenance.revision,
                            fact_revision,
                        ),
                        BUILTIN_MEMORY_RETENTION_ID: WikiHealthWorker(
                            "Memory Retention",
                            completed_runs.get(BUILTIN_MEMORY_RETENTION_ID),
                            None if retention is None else retention.revision,
                            fact_revision,
                            None if retention is None else retention.revision == fact_revision and not due_reviews,
                        ),
                        BUILTIN_MEMORY_SYNTHESIZE_ID: WikiHealthWorker(
                            "Memory Synthesis",
                            completed_runs.get(BUILTIN_MEMORY_SYNTHESIZE_ID),
                            None if synthesis is None else synthesis.revision,
                            fact_revision,
                        ),
                        BUILTIN_WIKI_MAINTENANCE_ID: WikiHealthWorker(
                            WIKI_MAINTENANCE_ACTOR,
                            completed_runs.get(BUILTIN_WIKI_MAINTENANCE_ID),
                            maintenance_revision,
                            observed,
                        ),
                    }

                    value = WikiHealthInput(
                        fact_ledger_revision=fact_revision,
                        wiki=report,
                        workers=tuple(workers_by_id[spec.task_id] for spec in _HEALTH_PHASES),
                        index=WikiHealthIndex(index_revision, index_status, index_detail),
                        issues=(
                            *(
                                WikiHealthIssue(
                                    WikiHealthIssueCode.FACT_REVIEW_DUE,
                                    item.fact.fact_id,
                                    f"review due at {item.due_at.isoformat()}",
                                    WikiHealthIssueOwner.RETENTION,
                                )
                                for item in due_reviews
                            ),
                            *citation_issues,
                        ),
                    )
                    result = await asyncio.to_thread(WikiHealthProjector(self.wiki_service).project, value)
                    if await self.fact_service.revision() != fact_revision:
                        if attempt == 0:
                            continue
                        raise RevisionConflictError("fact ledger changed during both wiki health projection attempts")
                    return report.through_revision if result.commit is None else result.commit.commit_id
                except RevisionConflictError:
                    if attempt == 0:
                        continue
                    raise

    async def project_wiki_state(self) -> None:
        await self.project_wiki_change()

    async def project_wiki_change_after_commit(self) -> bool:
        """Durably queue derived consumers after a committed wiki write."""

        if self.stores is None or self.wiki_service is None:
            raise RuntimeError("managed wiki projection retry is unavailable")
        revision = self.wiki_service.repository.head
        if revision is None:
            raise RuntimeError("committed wiki projection has no revision")
        try:
            await self.stores.outbox.enqueue_wiki_projection(revision)
        except Exception:
            _logger.exception(
                "Wiki projection enqueue failed after committed write; projecting inline",
                revision=revision,
            )
            await self.project_wiki_change()
            return False
        return True

    async def project_wiki_change(self) -> None:
        """Publish one canonical wiki-head transition to every derived consumer."""

        if self.wiki_service is None or self.wiki_page_projection is None or self._wiki_maintenance_store is None:
            raise RuntimeError("managed wiki projection is unavailable")
        async with self._wiki_projection_lock:
            previous_head = self._wiki_change_head
            if self.wiki_service.repository.head == previous_head:
                return
            final_head = None
            for _attempt in range(3):
                await self.wiki_page_projection.sync()
                await self.project_wiki_health()
                await self.wiki_page_projection.sync()
                final_head = await self.project_wiki_health()
                if self.wiki_service.repository.head == final_head:
                    break
            else:
                raise RevisionConflictError("wiki changed during every projection attempt")

            if final_head == previous_head:
                return
            commits = await asyncio.to_thread(
                self.wiki_service.repository.history,
                start=final_head,
                stop_before=previous_head,
            )
            paths: set[str] = set()
            attributed_paths: set[str] = set()
            source_areas_by_path: dict[str, set[str]] = {}
            common_page_created = False
            for commit in commits:
                if commit.origin == WIKI_HEALTH_ORIGIN:
                    continue
                source_area_id = None
                if commit.origin == "area.page" and commit.actor.startswith(CUSTODIAN_ACTOR_PREFIX):
                    candidate = commit.actor.removeprefix(CUSTODIAN_ACTOR_PREFIX)
                    if candidate and ":" not in candidate:
                        source_area_id = candidate
                for change in commit.changes:
                    if (
                        commit.origin not in {FACT_SYNTHESIS_CONSUMER_ID, WIKI_RENAME_ORIGIN}
                        and change.before is None
                        and change.after is not None
                        and change.after.path.endswith(".md")
                        and not change.after.path.startswith(AUTOMATIONS_PATH_PREFIX)
                        and change.after.path.rsplit("/", 1)[-1] != README_FILENAME
                    ):
                        common_page_created = True
                    for path in (
                        None if change.before is None else change.before.path,
                        None if change.after is None else change.after.path,
                    ):
                        if path is None:
                            continue
                        paths.add(path)
                        if path in attributed_paths:
                            continue
                        attributed_paths.add(path)
                        if source_area_id is not None:
                            source_areas_by_path.setdefault(path, set()).add(source_area_id)
            if paths and self.automation is not None:
                await self.automation.notify_wiki_changed(
                    sorted(paths),
                    final_head,
                    source_areas_by_path=source_areas_by_path,
                )
                if common_page_created:
                    await self.automation.request_fact_synthesis()
            if final_head is not None:
                await self._wiki_maintenance_store.record_projection_revision(
                    expected_revision=previous_head,
                    revision=final_head,
                )
            self._wiki_change_head = final_head

    @staticmethod
    def _semantic_wiki_revision(report: WikiChangesReport) -> str | None:
        return next(
            (commit.commit_id for commit in reversed(report.commits) if commit.origin != WIKI_HEALTH_ORIGIN),
            None,
        )

    @staticmethod
    def _semantic_wiki_revision_at(
        report: WikiChangesReport,
        raw_revision: str | None,
    ) -> tuple[str | None, bool]:
        if raw_revision is None:
            return None, True
        semantic: str | None = None
        for commit in report.commits:
            if commit.origin != WIKI_HEALTH_ORIGIN:
                semantic = commit.commit_id
            if commit.commit_id == raw_revision:
                return semantic, True
        return None, False

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
            get_fact_dream=self._get_fact_dream,
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
        if self._fact_ledger is None or self.wiki_service is None:
            raise RuntimeError("managed-history collection requires canonical facts and wiki")
        reports: list[CollectionReport] = []
        errors: list[Exception] = []
        failed: list[str] = []
        for name, collect in (
            ("facts", self._fact_ledger.collect_history),
            ("wiki", self.wiki_service.repository.collect),
        ):
            try:
                reports.append(collect())
            except Exception as error:
                failed.append(name)
                errors.append(error)
        if errors:
            raise ExceptionGroup(f"managed-history collection failed: {', '.join(failed)}", errors)
        facts, wiki = reports
        return facts, wiki

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
        await self._cancel_indexing()
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
        if self._index_error:
            errors["index"] = self._index_error
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
            create_session=self.stores.sessions.create,
            notifiers=self.notifier_service.list_summary() if self.notifier_service else [],
            enqueue_run_completed=self.stores.outbox.enqueue_run_completed,
            skill_registry=self.skill_registry,
        )

    async def start_scheduler(self) -> None:
        if not self.automation:
            raise RuntimeError("Automation runtime is not initialized")
        await self.automation.start_scheduler()
        if self.wiki_service is not None:
            await self.project_wiki_state()

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
