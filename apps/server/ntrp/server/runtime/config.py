import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from ntrp.config import Config, get_config
from ntrp.integrations import IntegrationRegistry
from ntrp.llm.router import init as llm_init
from ntrp.llm.router import reset as llm_reset
from ntrp.server.runtime.knowledge import KnowledgeRuntime
from ntrp.server.stores import Stores
from ntrp.services.config import ConfigService


class RuntimeConfig:
    def __init__(
        self,
        config: Config,
        *,
        get_integrations: Callable[[], IntegrationRegistry],
        get_knowledge: Callable[[], KnowledgeRuntime],
        get_stores: Callable[[], Stores | None],
        sync_mcp: Callable[[Config], Awaitable[None]],
        is_closing: Callable[[], bool],
        refresh_models: Callable[[], Awaitable[bool]] | None = None,
        after_reload: Callable[[], Awaitable[None]] | None = None,
    ):
        self.config = config
        self.service = ConfigService(on_config_change=self.reload)

        self._get_integrations = get_integrations
        self._get_knowledge = get_knowledge
        self._get_stores = get_stores
        self._sync_mcp = sync_mcp
        self._is_closing = is_closing
        self._refresh_models = refresh_models
        self._after_reload = after_reload

        self._lock = asyncio.Lock()
        self._version = 1
        self._loaded_at = datetime.now(UTC)

    def status(self) -> dict[str, int | str]:
        return {
            "config_version": self._version,
            "config_loaded_at": self._loaded_at.isoformat(),
        }

    async def reload(self) -> None:
        if self._is_closing():
            return

        async with self._lock:
            if self._refresh_models:
                await self._refresh_models()
            config = get_config()
            await llm_reset()
            llm_init(config)

            integrations = self._get_integrations()
            integrations.sync(config)
            await self._get_knowledge().reload_config(config, self._get_stores())
            await self._sync_mcp(config)
            if self._after_reload:
                await self._after_reload()

            self.config = config
            self._mark_loaded()

    def _mark_loaded(self) -> None:
        self._version += 1
        self._loaded_at = datetime.now(UTC)
