import asyncio
from collections.abc import Awaitable, Callable

from fastapi import HTTPException

from arden.config import Config
from arden.llm.models import Provider, get_embedding_model
from arden.logging import get_logger
from arden.memory.facts.index import FACT_SEARCH_SOURCE, FactIndexProjection
from arden.server.indexer import IndexProgress, IndexStatus
from arden.server.runtime.knowledge import KnowledgeRuntime
from arden.services.config import ConfigService
from arden.wiki.context import WIKI_PAGE_SOURCE, WikiPageIndexProjection

_logger = get_logger(__name__)
_VECTOR_UNAVAILABLE = "Vector index is unavailable; full-text search remains available"


class SearchIndexRuntime:
    """Own search projection synchronization and explicit reindexing."""

    def __init__(
        self,
        *,
        get_config: Callable[[], Config],
        config_service: ConfigService,
        knowledge: KnowledgeRuntime,
        reload_config: Callable[[], Awaitable[None]],
        get_fact_projection: Callable[[], FactIndexProjection | None],
        get_wiki_projection: Callable[[], WikiPageIndexProjection | None],
    ) -> None:
        self._get_config = get_config
        self._config_service = config_service
        self._knowledge = knowledge
        self._reload_config = reload_config
        self._get_fact_projection = get_fact_projection
        self._get_wiki_projection = get_wiki_projection
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._progress = IndexProgress(status=IndexStatus.PENDING)
        self._error: str | None = None

    @property
    def task(self) -> asyncio.Task[None] | None:
        return self._task

    @task.setter
    def task(self, task: asyncio.Task[None]) -> None:
        self._task = task

    @property
    def error(self) -> str | None:
        return self._error

    @property
    def rebuild_pending(self) -> bool:
        return (
            self._knowledge.indexer.needs_rebuild
            and self._get_config().embedding is not None
            and self._knowledge.indexer.vector_enabled
        )

    async def reload(self) -> None:
        async with self._lock:
            was_indexing = self.running
            await self.cancel()
            previous_embedding = self._get_config().embedding
            await self._reload_config()
            config = self._get_config()

            if config.embedding is None:
                self._set_state(IndexStatus.DISABLED)
            elif not self._knowledge.indexer.vector_enabled:
                self._set_state(IndexStatus.ERROR, error=_VECTOR_UNAVAILABLE)
            elif (
                was_indexing
                or config.embedding != previous_embedding
                or self._knowledge.indexer.needs_rebuild
                or self._progress.status is IndexStatus.ERROR
            ):
                self._start_locked()

    def model_available(self, model_id: str) -> bool:
        model = get_embedding_model(model_id)
        config = self._get_config()
        if model.provider is Provider.OPENAI:
            return bool(config.openai_api_key)
        if model.provider is Provider.GOOGLE:
            return bool(config.gemini_api_key)
        return model.provider is Provider.CUSTOM

    async def update_model(self, model_id: str | None, confirmed: bool) -> None:
        async with self._lock:
            config = self._get_config()
            if model_id == config.embedding_model:
                return
            if self.running:
                raise HTTPException(status_code=409, detail="Embedding reindex is already running")
            if not confirmed:
                raise HTTPException(
                    status_code=409,
                    detail="Changing the embedding model re-embeds all facts and wiki pages; resend with confirmed=true",
                )
            if model_id is not None:
                try:
                    available = self.model_available(model_id)
                except ValueError as error:
                    raise HTTPException(status_code=400, detail=str(error)) from error
                if not available:
                    raise HTTPException(status_code=400, detail=f"Embedding model {model_id!r} is not available")
            await self._config_service.update(embedding_model=model_id)
            config = self._get_config()
            if config.embedding is None:
                self._set_state(IndexStatus.DISABLED)
            elif not self._knowledge.indexer.vector_enabled:
                self._set_state(IndexStatus.ERROR, error=_VECTOR_UNAVAILABLE)
            else:
                self._start_locked()

    async def start(self) -> bool:
        async with self._lock:
            if self.running:
                raise HTTPException(status_code=409, detail="Embedding reindex is already running")
            config = self._get_config()
            if config.embedding is None:
                self._set_state(IndexStatus.DISABLED)
                return False
            if not self._knowledge.indexer.vector_enabled:
                self._set_state(IndexStatus.ERROR, error=_VECTOR_UNAVAILABLE)
                return False
            self._start_locked()
            return True

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def synchronize(self, *, deferred: bool = False) -> bool:
        """Synchronize current projections once, unless a full rebuild is required."""

        if deferred or self.rebuild_pending:
            self._set_state(IndexStatus.PENDING)
            return self.rebuild_pending

        for projection, label in (
            (self._get_fact_projection(), "fact"),
            (self._get_wiki_projection(), "wiki"),
        ):
            if projection is None:
                continue
            try:
                if label == "wiki":
                    await projection.sync(
                        raise_on_error=self._get_config().embedding is not None,
                        notify_state_change=False,
                    )
                else:
                    await projection.sync(raise_on_error=self._get_config().embedding is not None)
            except Exception:
                _logger.warning("initial %s index sync failed", label, exc_info=True)

        self._set_initial_status()
        return False

    def status(self) -> dict:
        retry_at = self._knowledge.indexer.rate_limit_retry_at
        return {
            "state": self._progress.status.value,
            "model": self._get_config().embedding_model,
            "phase": self._progress.phase,
            "done": self._progress.done,
            "total": self._progress.total,
            "retry_at": retry_at.isoformat() if retry_at is not None else None,
            "error": self._error,
        }

    async def cancel(self) -> None:
        task = self._task
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    def _start_locked(self) -> None:
        self._set_state(IndexStatus.INDEXING, phase="facts")
        self._task = asyncio.create_task(self._rebuild(), name="arden-search-reindex")

    async def _rebuild(self) -> None:
        try:
            rebuilt_sources: set[str] = set()
            fact_projection = self._get_fact_projection()
            if fact_projection is not None:
                result = await fact_projection.sync(
                    progress_callback=self._progress_callback("facts"),
                    raise_on_error=True,
                    force=True,
                )
                if result is None:
                    raise RuntimeError("fact index rebuild did not complete")
                rebuilt_sources.add(FACT_SEARCH_SOURCE)

            wiki_projection = self._get_wiki_projection()
            if wiki_projection is not None:
                result = await wiki_projection.sync(
                    progress_callback=self._progress_callback("wiki"),
                    raise_on_error=True,
                    force=True,
                )
                if result is None:
                    raise RuntimeError("wiki index rebuild did not complete")
                rebuilt_sources.add(WIKI_PAGE_SOURCE)

            search_index = self._knowledge.search_index
            if search_index is not None:
                existing_sources = set(await search_index.store.get_stats())
                unavailable = {FACT_SEARCH_SOURCE, WIKI_PAGE_SOURCE}.intersection(existing_sources).difference(
                    rebuilt_sources
                )
                if unavailable:
                    raise RuntimeError(f"cannot rebuild unavailable sources: {', '.join(sorted(unavailable))}")
                await search_index.store.mark_embedding_ready()
                self._knowledge.indexer.needs_rebuild = False
            self._set_state(IndexStatus.DONE)
        except asyncio.CancelledError:
            self._set_state(IndexStatus.PENDING)
            raise
        except Exception as error:
            self._set_state(IndexStatus.ERROR, error=f"{type(error).__name__}: {error}")

    def _progress_callback(self, phase: str) -> Callable[[int, int], None]:
        def update(done: int, total: int) -> None:
            self._progress.phase = phase
            self._progress.done = done
            self._progress.total = total

        return update

    def _set_initial_status(self) -> None:
        config = self._get_config()
        if config.embedding is None:
            self._set_state(IndexStatus.DISABLED)
            return
        if not self._knowledge.indexer.vector_enabled:
            self._set_state(IndexStatus.ERROR, error=_VECTOR_UNAVAILABLE)
            return
        failed_projection = next(
            (
                projection.last_state
                for projection in (self._get_fact_projection(), self._get_wiki_projection())
                if projection is not None and projection.last_state.status != "ready"
            ),
            None,
        )
        if failed_projection is None:
            self._set_state(IndexStatus.DONE)
        else:
            self._set_state(
                IndexStatus.ERROR,
                error=failed_projection.detail or "Initial index rebuild did not complete",
            )

    def _set_state(
        self,
        status: IndexStatus,
        *,
        phase: str | None = None,
        error: str | None = None,
    ) -> None:
        self._progress.status = status
        self._progress.phase = phase
        self._error = error
