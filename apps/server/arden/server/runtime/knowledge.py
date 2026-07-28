from __future__ import annotations

from typing import TYPE_CHECKING

from arden.config import Config
from arden.server.indexer import Indexer

if TYPE_CHECKING:
    from arden.memory.facts import FactService


class KnowledgeRuntime:
    """Lifecycle for rebuildable search plus the canonical fact capability."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.embedding = config.embedding
        self.indexer = Indexer(db_path=config.search_db_path, embedding=self.embedding) if self.embedding else None
        self.search_index = None
        self._fact_service: FactService | None = None

    def set_fact_service(self, service: FactService | None) -> None:
        self._fact_service = service

    async def connect(self, _stores=None) -> None:
        if self.indexer:
            await self.indexer.connect()
            self.search_index = self.indexer.index

    async def reload_config(self, config: Config, stores=None) -> None:
        del stores
        self.config = config
        await self._sync_embedding()

    async def stop(self) -> None:
        if self.indexer:
            await self.indexer.stop()

    async def close(self) -> None:
        if self.indexer:
            await self.indexer.close()

    def tool_services(self) -> dict[str, object]:
        services: dict[str, object] = {}
        if self._fact_service is not None:
            from arden.tools.facts import FACT_SERVICE

            services[FACT_SERVICE] = self._fact_service
        if self.search_index is not None:
            services["search_index"] = self.search_index
        return services

    def start_indexing(self) -> None:
        if self.indexer:
            self.indexer.start(None)

    async def get_index_status(self) -> dict:
        return await self.indexer.get_status() if self.indexer else {"status": "disabled"}

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

    def _memory_reasoning_effort(self, model_id: str | None) -> str | None:
        if not model_id:
            return None
        configured = self.config.reasoning_effort_for_role("memory", model_id)
        if configured:
            return configured
        from arden.llm.models import get_models

        efforts = get_models()[model_id].reasoning_efforts
        if not efforts:
            return None
        return "low" if "low" in efforts else efforts[0]
