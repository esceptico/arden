import asyncio
from collections.abc import Callable
from datetime import date
from pathlib import Path

from ntrp.config import Config
from ntrp.llm.router import get_completion_client
from ntrp.logging import get_logger
from ntrp.server.indexer import Indexer
from ntrp.server.stores import Stores

_logger = get_logger(__name__)
_EMPTY_CANONICAL_REVISION = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def _link_index_revision(revision: str) -> str:
    return revision or _EMPTY_CANONICAL_REVISION


class VaultIndexProjection:
    """Coalesced, retryable projection refresh outside canonical commits."""

    def __init__(self, root: Path, *, retry_delay: float = 2.0):
        from ntrp.memory.vault_index import VaultIndexer

        self._indexer = VaultIndexer(root)
        self._retry_delay = retry_delay
        self._task: asyncio.Task | None = None
        self._retry_handle: asyncio.TimerHandle | None = None
        self._work_future: asyncio.Future | None = None
        self._dirty = False
        self._closed = False
        self.stale = True

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def retry_scheduled(self) -> bool:
        return not self._closed and self._retry_handle is not None and not self._retry_handle.cancelled()

    def schedule(self) -> None:
        if self._closed:
            return
        self.stale = True
        self._dirty = True
        if self._retry_handle is not None:
            self._retry_handle.cancel()
            self._retry_handle = None
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        try:
            while self._dirty:
                self._dirty = False
                try:
                    loop = asyncio.get_running_loop()
                    work = loop.run_in_executor(None, self._indexer.apply)
                    self._work_future = work
                    try:
                        await asyncio.shield(work)
                    except asyncio.CancelledError:
                        await asyncio.shield(work)
                        raise
                    finally:
                        self._work_future = None
                except Exception:
                    self.stale = True
                    _logger.warning("memory vault index projection failed", exc_info=True)
                    if not self._closed:
                        loop = asyncio.get_running_loop()
                        self._retry_handle = loop.call_later(self._retry_delay, self.schedule)
                    return
                self.stale = False
        finally:
            self._task = None

    async def wait_idle(self) -> None:
        task = self._task
        if task is not None:
            await task

    def retry_now(self) -> None:
        if self._closed:
            return
        if self._retry_handle is not None:
            self._retry_handle.cancel()
            self._retry_handle = None
        self.schedule()

    async def close(self) -> None:
        self._closed = True
        self._dirty = False
        if self._retry_handle is not None:
            self._retry_handle.cancel()
            self._retry_handle = None
        task = self._task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        work = self._work_future
        if work is not None:
            await asyncio.shield(work)


class DailyProjectionCoordinator:
    """Coalesced daily projection with retry outside canonical commits."""

    def __init__(self, projector, *, revision: Callable[[], str], retry_delay: float = 2.0):
        self._projector = projector
        self._revision = revision
        self._retry_delay = retry_delay
        self._pending: set[date] = set()
        self._task: asyncio.Task | None = None
        self._retry_handle: asyncio.TimerHandle | None = None
        self._closed = False
        self.stale = True

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def retry_scheduled(self) -> bool:
        return not self._closed and self._retry_handle is not None and not self._retry_handle.cancelled()

    def schedule(self, local_dates=None) -> None:
        if self._closed:
            return
        dates = self._projector.local_dates() if local_dates is None else local_dates
        if isinstance(dates, date):
            dates = (dates,)
        self._pending.update(dates)
        if not self._pending:
            self.stale = False
            return
        self.stale = True
        if self._retry_handle is not None:
            self._retry_handle.cancel()
            self._retry_handle = None
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        failed: set[date] = set()
        try:
            while self._pending:
                local_date = min(self._pending)
                self._pending.remove(local_date)
                try:
                    self._projector.render(local_date, self._revision())
                except Exception:
                    failed.add(local_date)
                    _logger.warning("memory daily projection failed", exc_info=True)
            if failed:
                self._pending.update(failed)
                self.stale = True
                if not self._closed:
                    self._retry_handle = asyncio.get_running_loop().call_later(
                        self._retry_delay, self.schedule, ()
                    )
                return
            self.stale = False
        finally:
            self._task = None

    async def wait_idle(self) -> None:
        task = self._task
        if task is not None:
            await task

    def retry_now(self) -> None:
        if self._closed:
            return
        if self._retry_handle is not None:
            self._retry_handle.cancel()
            self._retry_handle = None
        self.schedule(())

    async def close(self) -> None:
        self._closed = True
        self._pending.clear()
        if self._retry_handle is not None:
            self._retry_handle.cancel()
            self._retry_handle = None
        task = self._task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


class LinkIndexProjection:
    """Coalesced link-index rebuild with durable last-good snapshots."""

    def __init__(self, index, *, artifacts, revision: Callable[[], str], retry_delay: float = 2.0):
        self.index = index
        self._artifacts = artifacts
        self._revision = revision
        self._retry_delay = retry_delay
        self._task: asyncio.Task | None = None
        self._retry_handle: asyncio.TimerHandle | None = None
        self._work_future: asyncio.Future | None = None
        self._dirty = False
        self._closed = False
        self.stale = not index.snapshot.revision or index.snapshot.revision != _link_index_revision(revision())

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def retry_scheduled(self) -> bool:
        return not self._closed and self._retry_handle is not None and not self._retry_handle.cancelled()

    def schedule(self) -> None:
        if self._closed:
            return
        self.stale = True
        self._dirty = True
        if self._retry_handle is not None:
            self._retry_handle.cancel()
            self._retry_handle = None
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        try:
            while self._dirty:
                self._dirty = False
                revision = _link_index_revision(self._revision())
                try:
                    loop = asyncio.get_running_loop()
                    work = loop.run_in_executor(None, self.index.rebuild, self._artifacts, revision)
                    self._work_future = work
                    try:
                        snapshot = await asyncio.shield(work)
                    except asyncio.CancelledError:
                        await asyncio.shield(work)
                        raise
                    finally:
                        self._work_future = None
                    if snapshot.revision != _link_index_revision(self._revision()):
                        self._dirty = True
                        self.stale = True
                except Exception:
                    self.stale = True
                    _logger.warning("memory link index projection failed", exc_info=True)
                    if not self._closed:
                        self._retry_handle = asyncio.get_running_loop().call_later(
                            self._retry_delay, self.schedule
                        )
                    return
            self.stale = False
        finally:
            self._task = None

    async def wait_idle(self) -> None:
        task = self._task
        if task is not None:
            await task

    def retry_now(self) -> None:
        if self._closed:
            return
        if self._retry_handle is not None:
            self._retry_handle.cancel()
            self._retry_handle = None
        self.schedule()

    async def close(self) -> None:
        self._closed = True
        self._dirty = False
        if self._retry_handle is not None:
            self._retry_handle.cancel()
            self._retry_handle = None
        task = self._task
        if task is not None and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        work = self._work_future
        if work is not None:
            await asyncio.shield(work)


class KnowledgeRuntime:
    def __init__(self, config: Config):
        self.config = config
        self.embedding = config.embedding
        self.indexer = Indexer(db_path=config.search_db_path, embedding=self.embedding) if self.embedding else None
        self.search_index = None
        self.memory_curator = None
        self.chat_connector = None

        self._record_store = None
        self._page_edit_service = None
        self._consolidate = None
        self._artifact_refresh_task: asyncio.Task | None = None
        self._vault_index = VaultIndexProjection(config.memory_artifacts_dir)
        self._daily_projection: DailyProjectionCoordinator | None = None
        self._link_index: LinkIndexProjection | None = None

    @property
    def memory_ready(self) -> bool:
        return self._record_store is not None

    @property
    def record_store(self):
        return self._record_store

    @property
    def page_edit_service(self):
        return self._page_edit_service

    @property
    def artifact_store(self):
        service = self._page_edit_service
        return service.artifact_store if service is not None else None

    @property
    def consolidate(self):
        return self._consolidate

    def start_memory_watch(self, on_change) -> None:
        """Turn the vault live: absorb external edits (Obsidian, feeds, git) into
        the in-memory store and fan a change event out through `on_change`."""
        store = self._record_store
        if store is not None and hasattr(store, "start_watch"):
            async def _indexed_change(changes):
                from ntrp.memory.file_store import ObservedFileChange

                plain_paths: list[str] = []
                for change in changes:
                    if isinstance(change, ObservedFileChange):
                        revision = change.result_revision
                        review_required = False
                        if change.origin == "external" and self._page_edit_service is not None:
                            event = await self._page_edit_service.ingest_external(change)
                            if event is not None:
                                revision = event.result_revision
                                review_required = event.reconciliation == "needs_review"
                        self._vault_index.schedule()
                        link_index = getattr(self, "_link_index", None)
                        if link_index is not None:
                            link_index.schedule()
                        await on_change(
                            [change.path],
                            revision=revision,
                            review_required=review_required,
                        )
                    else:
                        plain_paths.append(change)
                if plain_paths:
                    self._vault_index.schedule()
                    link_index = getattr(self, "_link_index", None)
                    if link_index is not None:
                        link_index.schedule()
                    await on_change(plain_paths, revision=None, review_required=False)

            store.start_watch(_indexed_change)

    def _memory_llm(self):
        """(client, model) for memory-page synthesis — the same completion client
        and model the curator/consolidate use. (None, "") when no memory_model is
        configured, which keeps the export mechanical."""
        if not self.config.memory_model:
            return None, ""
        return get_completion_client(self.config.memory_model), self.config.memory_model

    async def _reconcile_page_edit(self, analysis):
        if self.memory_curator is None:
            return None
        return await self.memory_curator.reconcile_page_edit(analysis)

    async def connect(self, stores: Stores) -> None:
        await self._init_search()
        await self._init_memory(stores)
        if self.search_index is not None:
            stores.sessions.store.attach_search_index(self.search_index)
        if self.memory_curator is not None:
            self.memory_curator.start_sweep()
        # No boot artifact refresh: files are canonical, there is no projection.

    async def reload_config(self, config: Config, stores: Stores | None) -> None:
        self.config = config
        await self._sync_embedding()
        # Re-wire the live transcript store to the (possibly new/None) index so
        # toggling embedding at runtime enables/disables hybrid search + indexing
        # without a restart. attach_search_index is idempotent and clears on None.
        if stores is not None:
            stores.sessions.store.attach_search_index(self.search_index)
        # Same re-wire for the record store (the curator shares it).
        if self._record_store is not None:
            self._record_store.attach_search_index(self.search_index)

    async def stop(self) -> None:
        link_index = getattr(self, "_link_index", None)
        if link_index is not None:
            await link_index.close()
        daily_projection = getattr(self, "_daily_projection", None)
        if daily_projection is not None:
            await daily_projection.close()
        self._page_edit_service = None
        await self._vault_index.close()
        if self._artifact_refresh_task is not None:
            self._artifact_refresh_task.cancel()
        if self.memory_curator:
            await self.memory_curator.stop()
        if self._consolidate:
            await self._consolidate.close()
        if self._record_store:
            await self._record_store.close()
        if self.indexer:
            await self.indexer.stop()

    async def close(self) -> None:
        link_index = getattr(self, "_link_index", None)
        if link_index is not None:
            await link_index.close()
        daily_projection = getattr(self, "_daily_projection", None)
        if daily_projection is not None:
            await daily_projection.close()
        self._page_edit_service = None
        await self._vault_index.close()
        if self._consolidate:
            await self._consolidate.close()
        if self._record_store:
            await self._record_store.close()
        if self.indexer:
            await self.indexer.close()

    def tool_services(self) -> dict[str, object | None]:
        from ntrp.tools.memory import MEMORY_RECONCILER_SERVICE, MEMORY_RECORDS_SERVICE

        services: dict[str, object | None] = {
            MEMORY_RECONCILER_SERVICE: self.memory_curator,
        }
        if self.search_index:
            services["search_index"] = self.search_index
        if self._record_store is not None:
            services[MEMORY_RECORDS_SERVICE] = self._record_store
        return services

    def start_indexing(self) -> None:
        if self.indexer:
            self.indexer.start(None)

    async def get_index_status(self) -> dict:
        return await self.indexer.get_status() if self.indexer else {"status": "disabled"}

    # --- search index ----------------------------------------------------

    async def _init_search(self) -> None:
        if self.indexer:
            await self.indexer.connect()
            self.search_index = self.indexer.index

    async def _sync_embedding(self) -> None:
        new_embedding = self.config.embedding
        if new_embedding == self.embedding:
            return

        self.embedding = new_embedding

        if new_embedding is None:
            if self.indexer:
                await self.indexer.stop()
                await self.indexer.close()
            self.indexer = None
            self.search_index = None
            return

        if self.indexer:
            await self.indexer.stop()
            await self.indexer.update_embedding(new_embedding)
        else:
            self.indexer = Indexer(db_path=self.config.search_db_path, embedding=new_embedding)
            await self.indexer.connect()
        self.search_index = self.indexer.index

    # --- flat-records memory ---------------------------------------------

    async def _init_memory(self, stores: Stores) -> None:
        if not self.config.memory:
            _logger.info("memory disabled by config")
            return
        if not hasattr(self, "_vault_index"):
            self._vault_index = VaultIndexProjection(self.config.memory_artifacts_dir)

        from ntrp.memory.journal import VaultJournal

        VaultJournal(self.config.memory_artifacts_dir).recover()

        from ntrp.memory.migrate_ledger_v2 import migrate_vault_to_v2, validate_vault

        migrate_vault_to_v2(self.config.memory_artifacts_dir)
        health = validate_vault(self.config.memory_artifacts_dir)
        if not health.healthy:
            raise RuntimeError(f"memory vault validation failed: {health.first_error or 'unknown vault error'}")

        from ntrp.memory.curator import Curator
        from ntrp.memory.file_store import FilePageStore
        from ntrp.memory.project_names import load_project_names

        # Filesystem-canonical memory: two-zone markdown pages are the single
        # source of truth. Mounted under the same surface tools/profile/curator
        # already duck-type, so canonicality flips with one assignment.
        self._record_store = FilePageStore(
            root=self.config.memory_artifacts_dir,
            search_index=self.search_index,
            project_names=load_project_names(self.config.memory_artifacts_dir),
            post_canonical_commit=self._schedule_memory_projections,
        )
        self._consolidate = None  # set below once the memory model is resolved

        memory_llm = get_completion_client(self.config.memory_model) if self.config.memory_model else None
        memory_effort = self._memory_reasoning_effort(self.config.memory_model)

        # File-native record consolidation: the nightly Consolidate engine runs its
        # vector-neighborhood dedup/merge/retype directly on the canonical FilePageStore
        # (it duck-types the store API; db_path is just its own watermark meta table).
        if self.config.memory_model:
            from ntrp.memory.consolidate import Consolidate

            self._consolidate = Consolidate(
                self._record_store,
                memory_llm,
                model=self.config.memory_model,
                db_path=self.config.memory_db_path,
                reasoning_effort=memory_effort,
            )

        # Importance scorer (off hot path: curator sweep + migrate backfill). Falls
        # back to a heuristic when no memory_model, so it's always safe to attach.
        from ntrp.memory.scorer import score_importance

        async def _scorer(text: str, kind: str, pinned: bool) -> int:
            return await score_importance(text, kind, pinned, memory_llm, self.config.memory_model, memory_effort)

        self._record_store.attach_scorer(_scorer)

        if self.config.memory_model:
            self.memory_curator = Curator(
                memory_llm,
                stores.sessions,
                model=self.config.memory_model,
                db_path=self.config.memory_db_path,  # curator owns only its watermark meta here
                record_store=self._record_store,
                consolidate=None,
                reasoning_effort=memory_effort,
            )
        else:
            _logger.warning("memory enabled but no memory_model; curator disabled")

        await self._record_store.open()
        await self._migrate_legacy_if_needed()
        VaultJournal(self.config.memory_artifacts_dir).recover()
        migrate_vault_to_v2(self.config.memory_artifacts_dir)
        health = validate_vault(self.config.memory_artifacts_dir)
        if not health.healthy:
            raise RuntimeError(f"memory vault validation failed: {health.first_error or 'unknown vault error'}")
        await self._record_store.open()
        from ntrp.memory.page_edit_service import PageEditService

        self._page_edit_service = PageEditService(
            self.config.memory_artifacts_dir,
            self._record_store,
            reconciler=self._reconcile_page_edit,
        )
        from ntrp.memory.artifacts import ArtifactMemoryStore
        from ntrp.memory.link_index import LinkIndex

        self._link_index = LinkIndexProjection(
            LinkIndex(self.config.memory_artifacts_dir),
            artifacts=ArtifactMemoryStore(self.config.memory_artifacts_dir),
            revision=lambda: self._record_store.canonical_revision,
        )
        from ntrp.memory.daily import DailyProjector

        daily_projector = DailyProjector(
            self.config.memory_artifacts_dir,
            timezone=self.config.memory_timezone,
            entries=self._record_store._ledger_entries,
            page_events=self._page_edit_service.history,
            projection_writer=self._record_store.commit_generated_projection,
        )
        self._daily_projection = DailyProjectionCoordinator(
            daily_projector,
            revision=lambda: self._record_store.canonical_revision,
        )
        # Evict stale old-engine vectors (source="record") from the shared index.
        # Only touches that partition — transcripts + memory_line are untouched.
        if self.search_index is not None:
            try:
                await self.search_index.store.clear_source("record")
            except Exception:
                _logger.warning("clear stale record vectors failed", exc_info=True)
        _logger.info("memory ready (file-canonical)", root=str(self.config.memory_artifacts_dir))
        self._vault_index.schedule()
        self._link_index.schedule()
        self._daily_projection.schedule()

        # Synthesize the prose layer (the wiki view) off the hot path: stale-gated,
        # so a freshly-migrated store gets full prose once and later boots are cheap.
        if memory_llm is not None:
            from ntrp.memory.synthesize import run_synthesis

            self._artifact_refresh_task = asyncio.create_task(
                run_synthesis(self._record_store, memory_llm, self.config.memory_model, reasoning_effort=memory_effort)
            )

    def _schedule_memory_projections(self) -> None:
        self._vault_index.schedule()
        if self._link_index is not None:
            self._link_index.schedule()
        if self._daily_projection is not None:
            self._daily_projection.schedule()

    async def _migrate_legacy_if_needed(self) -> None:
        """One-time boot migration: if the file store is empty but the legacy
        SQLite pool still has records, convert them to pages. Backs up the db and
        any existing projection dir first; idempotent (skips once pages exist)."""
        if await self._record_store.count_active() > 0:
            return
        db_path = self.config.memory_db_path
        if not db_path.exists():
            return

        from ntrp.memory.records import RecordStore

        legacy = RecordStore(db_path=db_path)
        await legacy.open()
        try:
            if await legacy.count_active() == 0:
                return  # nothing to migrate

            import shutil
            from datetime import UTC, datetime

            from ntrp.memory.migrate_to_files import migrate

            root = self.config.memory_artifacts_dir
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            shutil.copy2(db_path, db_path.parent / f"{db_path.name}.premigrate-{stamp}.bak")
            if root.exists():
                shutil.copytree(root, root.parent / f"{root.name}.bak-{stamp}")
                shutil.rmtree(root)
            result = await migrate(legacy, root)
            _logger.info("auto-migrated legacy memory to files on boot", **result)
        finally:
            await legacy.close()
        await self._record_store.open()  # reload the freshly-written pages

    def _memory_reasoning_effort(self, model_id: str | None) -> str | None:
        """Effort for memory's structured calls: the user's configured effort if set,
        else 'low' (or the model's lowest) so a reasoning model doesn't run at its slow
        API-default and time out. Returns None for non-reasoning models."""
        if not model_id:
            return None
        configured = self.config.reasoning_effort_for(model_id)
        if configured:
            return configured
        from ntrp.llm.models import get_models

        efforts = get_models()[model_id].reasoning_efforts
        if not efforts:
            return None
        return "low" if "low" in efforts else efforts[0]
