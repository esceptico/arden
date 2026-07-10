import asyncio

from ntrp.config import Config
from ntrp.llm.router import get_completion_client
from ntrp.logging import get_logger
from ntrp.server.indexer import Indexer
from ntrp.server.stores import Stores

_logger = get_logger(__name__)


class KnowledgeRuntime:
    def __init__(self, config: Config):
        self.config = config
        self.embedding = config.embedding
        self.indexer = Indexer(db_path=config.search_db_path, embedding=self.embedding) if self.embedding else None
        self.search_index = None
        self.memory_curator = None
        self.chat_connector = None

        self._record_store = None
        self._consolidate = None
        self._artifact_refresh_task: asyncio.Task | None = None

    @property
    def memory_ready(self) -> bool:
        return self._record_store is not None

    @property
    def record_store(self):
        return self._record_store

    @property
    def consolidate(self):
        return self._consolidate

    def start_memory_watch(self, on_change) -> None:
        """Turn the vault live: absorb external edits (Obsidian, feeds, git) into
        the in-memory store and fan a change event out through `on_change`."""
        store = self._record_store
        if store is not None and hasattr(store, "start_watch"):
            store.start_watch(on_change)

    def _memory_llm(self):
        """(client, model) for memory-page synthesis — the same completion client
        and model the curator/consolidate use. (None, "") when no memory_model is
        configured, which keeps the export mechanical."""
        if not self.config.memory_model:
            return None, ""
        return get_completion_client(self.config.memory_model), self.config.memory_model

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
        if self._consolidate:
            await self._consolidate.close()
        if self._record_store:
            await self._record_store.close()
        if self.indexer:
            await self.indexer.close()

    def tool_services(self) -> dict[str, object]:
        services: dict[str, object] = {}
        if self.search_index:
            services["search_index"] = self.search_index
        if self._record_store is not None:
            from ntrp.tools.memory import MEMORY_RECORDS_SERVICE

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
        # Evict stale old-engine vectors (source="record") from the shared index.
        # Only touches that partition — transcripts + memory_line are untouched.
        if self.search_index is not None:
            try:
                await self.search_index.store.clear_source("record")
            except Exception:
                _logger.warning("clear stale record vectors failed", exc_info=True)
        _logger.info("memory ready (file-canonical)", root=str(self.config.memory_artifacts_dir))

        # Synthesize the prose layer (the wiki view) off the hot path: stale-gated,
        # so a freshly-migrated store gets full prose once and later boots are cheap.
        if memory_llm is not None:
            from ntrp.memory.synthesize import run_synthesis

            self._artifact_refresh_task = asyncio.create_task(
                run_synthesis(self._record_store, memory_llm, self.config.memory_model, reasoning_effort=memory_effort)
            )

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
