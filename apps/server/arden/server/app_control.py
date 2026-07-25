from collections.abc import Awaitable, Callable

from arden.areas.asks import AskStore
from arden.automation.scheduler import AUTOMATION_BUS_KEY
from arden.events.sse import SSEEvent
from arden.server.bus import BusRegistry


class AppControlService:
    """Tool-facing handle on the app itself.

    `ctx.io.emit` only reaches the run's own session, and the desktop drops
    chat-stream events for any other session — cross-session UI signals must
    ride the global automations bus, so they go through `emit` here.
    """

    def __init__(
        self,
        bus_registry: BusRegistry,
        dispatch_session_message: Callable[..., Awaitable[str | None]],
        asks: AskStore,
    ) -> None:
        self._bus_registry = bus_registry
        self._dispatch = dispatch_session_message
        self.asks = asks

    async def emit(self, event: SSEEvent) -> None:
        await self._bus_registry.get_or_create(AUTOMATION_BUS_KEY).emit(event)

    async def dispatch(self, session_id: str, message: str, *, client_id: str) -> str | None:
        # skip_approvals=None leaves the target run's approval policy alone.
        # False would forcibly clear an automation run's auto-approval
        # (submit_chat_message calls set_skip_approvals on the live run).
        return await self._dispatch(session_id, message, client_id=client_id, skip_approvals=None)
