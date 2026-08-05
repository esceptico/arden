import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Protocol, cast

from arden.context.models import SessionState
from arden.tools.core.context import BackgroundTaskRegistry, ChildIOParams, IOBridge, ToolContext


class _SessionService(Protocol):
    async def provision_state(self, state: SessionState, messages: list[dict]) -> None: ...

    async def save(self, state: SessionState, messages: list[dict]) -> None: ...


@dataclass(frozen=True, slots=True)
class ChildSuspension:
    status: str
    child_run_id: str
    child_session_id: str | None
    respawns: int
    result_status: str | None
    result: str | None

    def terminal_result(self) -> tuple[str, str]:
        if self.result_status is None or self.result is None:
            raise RuntimeError("pending subagent suspension has no result")
        return self.result_status, self.result


def decode_child_suspension(value: object) -> ChildSuspension:
    if not isinstance(value, Mapping) or value.get("kind") != "subagent_result":
        raise RuntimeError("invalid subagent suspension")
    status = value.get("status")
    if status not in {"pending", "completed", "failed", "cancelled", "interrupted"}:
        raise RuntimeError("invalid subagent suspension status")
    payload = value.get("payload")
    if not isinstance(payload, Mapping):
        raise RuntimeError("invalid subagent suspension payload")
    child_run_id = payload.get("child_run_id")
    child_session_id = payload.get("child_session_id")
    respawns = payload.get("respawns")
    if not isinstance(child_run_id, str) or not child_run_id:
        raise RuntimeError("subagent suspension is missing child_run_id")
    if child_session_id is not None and not isinstance(child_session_id, str):
        raise RuntimeError("invalid subagent suspension child_session_id")
    if isinstance(respawns, bool) or not isinstance(respawns, int) or respawns < 0:
        raise RuntimeError("invalid subagent suspension respawns")
    if status == "pending":
        return ChildSuspension(status, child_run_id, child_session_id, respawns, None, None)
    resolution = value.get("resolution")
    if not isinstance(resolution, Mapping):
        raise RuntimeError("terminal subagent suspension is missing its resolution")
    result_status = resolution.get("status")
    result = resolution.get("result")
    if result_status != status or not isinstance(result, str):
        raise RuntimeError("invalid subagent suspension resolution")
    return ChildSuspension(status, child_run_id, child_session_id, respawns, result_status, result)


def append_failure(errors: list[Exception], error: Exception) -> None:
    if isinstance(error, ExceptionGroup):
        errors.extend(error.exceptions)
    else:
        errors.append(error)


def raise_failures(message: str, errors: list[Exception]) -> None:
    if len(errors) == 1:
        raise errors[0]
    if errors:
        raise ExceptionGroup(message, errors)


async def collect_failure(errors: list[Exception], operation: Awaitable[object]) -> None:
    try:
        await operation
    except asyncio.CancelledError:
        raise
    except Exception as error:
        append_failure(errors, error)


async def run_operations(message: str, *operations: Awaitable[object]) -> None:
    errors: list[Exception] = []
    for operation in operations:
        await collect_failure(errors, operation)
    raise_failures(message, errors)


async def cancel_and_join(task: asyncio.Task | None, *, observe_done: bool = False) -> None:
    """Cancel an owned task without swallowing a new caller cancellation."""
    if task is None or (task.done() and not observe_done):
        return
    caller = asyncio.current_task()
    cancellation_count = caller.cancelling() if caller is not None else 0
    if not task.done():
        task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        if caller is not None and caller.cancelling() > cancellation_count:
            raise


async def finish_despite_cancellation[T](operation: Awaitable[T]) -> T:
    """Finish an atomic terminal operation, then restore caller cancellation."""
    task = asyncio.create_task(operation)
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            if task.done():
                break
            cancellation = error
        except Exception:
            break
    try:
        result = task.result()
    except BaseException as error:
        if cancellation is not None:
            raise cancellation from error
        raise
    if cancellation is not None:
        raise cancellation
    return result


async def activate_child(
    start: Awaitable[None],
    cleanup: Callable[[], Awaitable[None]],
    abort: Callable[[str], Awaitable[None]],
) -> None:
    try:
        await start
    except asyncio.CancelledError as cancellation:
        try:
            await finish_despite_cancellation(
                run_operations("child activation cleanup failed", cleanup(), abort("cancelled"))
            )
        except Exception as error:
            raise cancellation from error
        raise
    except Exception as error:
        errors = [error]
        await collect_failure(errors, cleanup())
        await collect_failure(errors, abort("failed"))
        raise_failures("child activation failed", errors)


class ChildSessionLifecycle:
    """Owns provisioning, IO framing, persistence, and teardown for one child."""

    def __init__(
        self,
        *,
        parent: ToolContext,
        child: ToolContext,
        state: SessionState,
        messages: list[dict],
        task_id: str,
        has_child_session: bool,
    ) -> None:
        self._parent = parent
        self._child = child
        self._state = state
        self._messages = messages
        self._task_id = task_id
        self._has_child_session = has_child_session
        self._session_service = cast("_SessionService | None", parent.services.get("session"))
        self._provisioned = False
        self._io: IOBridge | None = None
        self._close: Callable[[], Awaitable[None]] | None = None
        self._finish: Callable[[str], Awaitable[None]] | None = None
        self._background_tasks = parent.background_tasks

    @property
    def io(self) -> IOBridge | None:
        return self._io

    @property
    def background_tasks(self) -> BackgroundTaskRegistry:
        return self._background_tasks

    @property
    def provisioned(self) -> bool:
        return self._provisioned

    async def provision(self) -> None:
        if self._state.session_id == self._parent.session_id:
            return
        if self._session_service is None:
            raise RuntimeError("separate child agents require the session service")
        await self._session_service.provision_state(self._state, [])
        self._provisioned = True

    async def acquire_io(self) -> None:
        factory = self._parent.run.child_io_factory
        if not self._has_child_session or factory is None:
            return
        session = await factory(
            ChildIOParams(
                session_id=self._state.session_id,
                run_id=self._parent.run.run_id,
                pending_approvals=self._parent.io.pending_approvals or {},
                task_id=self._task_id,
                parent_background_tasks=self._parent.background_tasks,
            )
        )
        self._io = session.io
        self._close = session.aclose
        self._finish = session.finish
        if session.background_tasks is not None:
            self._background_tasks = session.background_tasks
        self._child.io = session.io
        self._child.background_tasks = self._background_tasks
        if self._parent.run_registry is not None:
            self._parent.run_registry.mark_session_active(self._state.session_id, self._parent.run.run_id)

    async def persist(self, status: str) -> None:
        if self._state.session_id == self._parent.session_id:
            return
        if not self._provisioned:
            raise RuntimeError("child agent session was not provisioned")
        self._state.agent_status = status
        await self._session_service.save(self._state, self._messages)

    async def save_step(self, _step: int, _response, messages: list[dict]) -> None:
        if messages is self._messages:
            await self.persist("running")

    async def finish(self, status: str) -> None:
        if self._finish is not None:
            await self._finish(status)

    async def settle(
        self,
        status: str,
        record_terminal: Callable[[str, Exception | None], Awaitable[None]],
        *,
        persist: bool = True,
    ) -> str:
        """Persist and publish one immutable terminal status, then close IO."""
        errors: list[Exception] = []
        persistence_error: Exception | None = None
        if persist:
            try:
                await self.persist(status)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                append_failure(errors, error)
                persistence_error = error
                status = "failed"
                self._state.agent_status = status
        await collect_failure(errors, record_terminal(status, persistence_error))
        await collect_failure(errors, self.finish(status))
        await collect_failure(errors, self.teardown())
        raise_failures("child terminal settlement failed", errors)
        return status

    async def teardown(self) -> None:
        errors: list[Exception] = []
        try:
            if self._parent.run_registry is not None and self._io is not None:
                self._parent.run_registry.clear_session_active(self._state.session_id)
        except Exception as error:
            errors.append(error)
        if self._close is not None:
            await collect_failure(errors, self._close())
        raise_failures("child IO teardown failed", errors)
