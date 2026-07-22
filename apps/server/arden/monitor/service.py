from collections.abc import Awaitable, Callable
from typing import Protocol, runtime_checkable

from arden.events.triggers import TriggerEvent
from arden.logging import get_logger

_logger = get_logger(__name__)

MonitorEventSink = Callable[[TriggerEvent], Awaitable[None]]


@runtime_checkable
class MonitorProvider(Protocol):
    def start(self, emit_event: MonitorEventSink) -> None: ...

    async def stop(self) -> None: ...


class Monitor:
    def __init__(self, emit_event: MonitorEventSink):
        self._emit_event = emit_event
        self._providers: list[MonitorProvider] = []
        # Index of the first not-yet-started provider. `start()` starts only
        # providers appended since the last start, so registering a new one
        # (e.g. the Slack monitor, wired after the calendar monitor) and
        # calling start() again won't re-walk already-running providers.
        self._started_count = 0

    def register(self, provider: MonitorProvider) -> None:
        self._providers.append(provider)

    def start(self) -> None:
        pending = self._providers[self._started_count :]
        if not pending:
            return
        for provider in pending:
            provider.start(self._emit_event)
        self._started_count = len(self._providers)
        _logger.info("Monitor started (%d new, %d total)", len(pending), len(self._providers))

    async def stop(self) -> None:
        for provider in self._providers:
            await provider.stop()
        self._started_count = 0
        _logger.info("Monitor stopped")
