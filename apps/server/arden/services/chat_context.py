import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import BaseModel

from arden.context.models import AreaContext, SessionState
from arden.core.factory import AgentConfig
from arden.events.internal import RunCompleted, RunFailed
from arden.server.state import RunRegistry, RunState
from arden.services.session import SessionService
from arden.tools.executor import ToolExecutor


@dataclass
class ChatContext:
    run: RunState
    session_state: SessionState
    is_init: bool
    executor: ToolExecutor
    tools: list[dict]
    config: AgentConfig
    available_integrations: list[str]
    integration_errors: dict[str, str]
    session_service: SessionService
    run_registry: RunRegistry
    connection_catalog: tuple[object, ...] = ()
    initial_input_tokens: int | None = None
    goal_id: str | None = None
    session_name_task: asyncio.Task[str] | None = None
    area_context: AreaContext | None = None
    enqueue_run_completed: Callable[[RunCompleted], Awaitable[bool]] | None = None
    enqueue_run_failed: Callable[[RunFailed], Awaitable[bool]] | None = None
    dispatch_session_message: (
        Callable[
            [str, str, str | None, bool | None, list[dict] | None],
            Awaitable[object],
        ]
        | None
    ) = None
    output_schema: type[BaseModel] | None = None
