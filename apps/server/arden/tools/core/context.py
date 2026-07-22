import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, TypedDict
from uuid import uuid4

from coolname import generate_slug

from arden.agent import Role, ToolOutcomeStatus, ToolResult
from arden.agent.agent import RunBudget
from arden.agent.ledger import SharedLedger
from arden.constants import ARDEN_TMP_BASE
from arden.context.models import AreaContext, SessionState
from arden.events.sse import ApprovalNeededEvent, BackgroundTaskEvent, ConnectionNeededEvent, InputNeededEvent
from arden.logging import get_logger
from arden.tools.core.types import ToolOverrideDecision

_logger = get_logger(__name__)

if TYPE_CHECKING:
    from arden.integrations.base import IntegrationConnectionDescriptor
    from arden.server.state import RunRegistry
    from arden.tools.core.registry import ToolRegistry


class ApprovalResponse(TypedDict):
    approved: bool
    result: str


@dataclass
class Rejection:
    feedback: str | None
    code: str = "approval_rejected"
    status: ToolOutcomeStatus = ToolOutcomeStatus.DENIED
    preview: str = "Rejected"
    recovery_action: str | None = "Revise the action or ask the user for different authorization."
    diagnostic_ref: str | None = None

    @classmethod
    def persistence_failure(cls, feedback: str, diagnostic_ref: str) -> "Rejection":
        return cls(
            feedback=feedback,
            code="approval_persistence_failed",
            status=ToolOutcomeStatus.FAILED,
            preview="Approval unavailable",
            recovery_action="Retry after approval storage is available.",
            diagnostic_ref=diagnostic_ref,
        )

    def to_result(self) -> ToolResult:
        if self.code == "approval_rejected":
            content = (
                f"User rejected this action and said: {self.feedback}" if self.feedback else "User rejected this action"
            )
        else:
            content = self.feedback or "Approval storage failed — action cancelled"
        return ToolResult.failure(
            code=self.code,
            message=content,
            preview=self.preview,
            status=self.status,
            recovery_action=self.recovery_action,
            diagnostic_ref=self.diagnostic_ref,
        )


@dataclass
class ApprovalControls:
    """Mutable, run-scoped approval switches controlled by the active client."""

    skip_approvals: bool = False


@dataclass
class RunContext:
    """Per-run identity and limits."""

    run_id: str
    current_depth: int = 0
    max_depth: int = 0
    max_iterations: int | None = None
    max_tool_calls: int | None = None
    max_wall_time_seconds: float | None = None
    max_cost: float | None = None
    started_at: float | None = None
    budget: RunBudget | None = None
    extra_auto_approve: set[str] = field(default_factory=set)
    approval_controls: ApprovalControls = field(default_factory=ApprovalControls)
    research_model: str | None = None
    workflow_model: str | None = None
    deferred_tools_enabled: bool = False
    deferred_tool_loader: Literal["load_tools", "tool_search"] = "load_tools"
    loaded_tools: set[str] = field(default_factory=set)
    declined_connections: set[str] = field(default_factory=set)
    allowed_tool_names: set[str] | None = None
    loop_task_id: str | None = None
    active_plan_ref: str | None = None
    research_scope_id: str | None = None
    # Builds an IOBridge bound to a child (subagent) session's own SSE bus, so a
    # spawned FULL subagent streams to its own session exactly like a normal run
    # instead of the parent's bus. Set by the chat service (which owns the
    # BusRegistry); None in non-chat/test paths (then a child reuses the parent io).
    child_io_factory: "ChildIOFactory | None" = None

    def __post_init__(self) -> None:
        if self.budget is None:
            self.budget = RunBudget()

    def to_rehydration_state(
        self,
        *,
        pending_approvals: list[str] | None = None,
        background_tasks: list[dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        return {
            "pending_approval_ids": pending_approvals or [],
            "background_tasks": background_tasks or [],
            "active_plan_ref": self.active_plan_ref,
            "loop_task_id": self.loop_task_id,
            "declined_connections": sorted(self.declined_connections),
        }

    def apply_rehydration_state(self, state: dict[str, Any] | None) -> None:
        if not state:
            return
        active_plan_ref = state.get("active_plan_ref")
        self.active_plan_ref = active_plan_ref if isinstance(active_plan_ref, str) else None
        loop_task_id = state.get("loop_task_id")
        self.loop_task_id = loop_task_id if isinstance(loop_task_id, str) else None
        declined_connections = state.get("declined_connections")
        if isinstance(declined_connections, list):
            self.declined_connections = {value for value in declined_connections if isinstance(value, str)}


@dataclass
class IOBridge:
    """Communication channels to the UI."""

    emit: Callable[[Any], Awaitable[None]] | None = None
    # Per-tool approval response routing. Each tool that needs approval
    # registers `pending_approvals[tool_id] = Future()` and awaits it;
    # the /tools/result endpoint resolves the matching Future.
    pending_approvals: dict[str, "asyncio.Future[ApprovalResponse]"] | None = None
    # Per-tool input routing for render_html mode="input". Same Future
    # mechanics as approvals but a separate dict so set_skip_approvals'
    # blanket-approve never resolves a pending input with an empty string.
    pending_inputs: dict[str, "asyncio.Future[ApprovalResponse]"] | None = None
    # Connection requests are user-interaction suspensions, not approvals.
    # Keep them separate so Auto mode never resolves them.
    pending_connections: dict[str, "asyncio.Future[ApprovalResponse]"] | None = None
    pending_connection_descriptors: dict[str, "IntegrationConnectionDescriptor"] | None = None
    record_approval: Callable[..., Awaitable[None]] | None = None
    resolve_approval: Callable[..., Awaitable[None]] | None = None
    record_connection: Callable[..., Awaitable[None]] | None = None
    resolve_connection: Callable[..., Awaitable[None]] | None = None
    get_suspension: Callable[..., Awaitable[dict | None]] | None = None
    consume_suspension: Callable[..., Awaitable[None]] | None = None
    approval_timeout_seconds: int = 300


@dataclass(frozen=True, slots=True)
class ChildIOParams:
    """What a child_io_factory needs to wire a subagent to its own session bus.
    The child reuses the PARENT run's approval map + run_id: approvals resolve
    through the parent run's /tools/result, and the parent run_id frames the
    child session's bus (RunStarted/RunFinished) consistently with the
    runtime.active_run the parent run surfaces via mark_session_active — so the
    viewed child renders live exactly like a normal run."""

    session_id: str
    run_id: str
    pending_approvals: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ChildSession:
    """A subagent's own-session io, the terminal `finish(status)` that closes its
    run framing (durable status + RunFinished/RunCancelled on its bus), and the
    cleanup that drains + evicts its bus so a never-opened child doesn't leak its
    durable-persist worker."""

    io: IOBridge
    finish: Callable[[str], Awaitable[None]]
    aclose: Callable[[], Awaitable[None]]


ChildIOFactory = Callable[[ChildIOParams], Awaitable[ChildSession]]


async def _approval_callback_best_effort(
    callback: Callable[..., Awaitable[None]] | None,
    label: str,
    **kwargs: Any,
) -> None:
    if not callback:
        return
    try:
        await callback(**kwargs)
    except asyncio.CancelledError:
        raise
    except Exception:
        _logger.exception("Approval %s callback failed", label)


async def _approval_callback_required(
    callback: Callable[..., Awaitable[None]] | None,
    label: str,
    **kwargs: Any,
) -> str | None:
    if not callback:
        return None
    try:
        await callback(**kwargs)
    except asyncio.CancelledError:
        raise
    except Exception:
        diagnostic_ref = f"approval-{uuid4().hex[:12]}"
        _logger.exception(
            "Required approval %s callback failed (diagnostic_ref=%s)",
            label,
            diagnostic_ref,
        )
        return diagnostic_ref
    return None


RESULT_BASE = Path(ARDEN_TMP_BASE)


@dataclass
class BackgroundTaskRegistry:
    """Tracks background tasks and injects results into the agent loop."""

    session_id: str = ""
    on_result: Callable[[list[dict]], Awaitable[None]] | None = None
    record_event: Callable[..., Awaitable[None]] | None = None
    read_result: Callable[[str], Awaitable[str | None]] | None = None
    claim_completion: Callable[..., Awaitable[dict]] | None = None
    mark_completion_delivered: Callable[..., Awaitable[None]] | None = None
    _tasks: dict[str, asyncio.Task] = field(default_factory=dict)
    _commands: dict[str, str] = field(default_factory=dict)
    _reserved: set[str] = field(default_factory=set)
    # Per-agent steering inbox: messages the parent (or user) sends to a
    # running background agent, drained into the child's loop at its next step
    # via the get_pending_messages hook. Mirrors RunState.inject_queue, but
    # keyed per background task instead of per top-level run.
    _inboxes: dict[str, list[dict]] = field(default_factory=dict)
    # task_id -> the agent's own child session id, so a cancel can walk the
    # spawn subtree (descendants run inside this session).
    _child_sessions: dict[str, str] = field(default_factory=dict)
    _delivered_completion_ids: set[str] = field(default_factory=set)

    def generate_id(self) -> str:
        return generate_slug(2)

    def _remove(self, task_id: str) -> None:
        self._tasks.pop(task_id, None)
        self._commands.pop(task_id, None)
        self._reserved.discard(task_id)
        self._inboxes.pop(task_id, None)
        self._child_sessions.pop(task_id, None)

    def reserve(
        self,
        task_id: str,
        *,
        command: str,
        limit: int,
        child_session_id: str | None = None,
    ) -> bool:
        if task_id in self._tasks or task_id in self._reserved:
            return False
        if self.pending_count >= limit:
            return False
        self._reserved.add(task_id)
        self._commands[task_id] = command
        if child_session_id:
            self._child_sessions[task_id] = child_session_id
        return True

    def release(self, task_id: str) -> None:
        if task_id in self._reserved:
            self._remove(task_id)

    def child_session(self, task_id: str) -> str | None:
        return self._child_sessions.get(task_id)

    def queue_injection(self, task_id: str, message: dict) -> bool:
        """Queue a steering message for a running background agent. Returns
        False when no such agent is live (already finished or unknown)."""
        task = self._tasks.get(task_id)
        if task is None or task.done():
            return False
        self._inboxes.setdefault(task_id, []).append(message)
        return True

    def drain_injections(self, task_id: str) -> list[dict]:
        batch = self._inboxes.get(task_id)
        if not batch:
            return []
        self._inboxes[task_id] = []
        return list(batch)

    def queue_steering(self, task_id: str, text: str) -> bool:
        """Queue a steering message (wrapped as a user turn) for a running
        background agent. One front door for the tool + the HTTP route."""
        return self.queue_injection(
            task_id, {"role": "user", "content": f"<steering_message>\n{text}\n</steering_message>"}
        )

    def register(self, task_id: str, task: asyncio.Task, command: str) -> None:
        self._reserved.discard(task_id)
        self._tasks[task_id] = task
        self._commands[task_id] = command
        task.add_done_callback(lambda _: self._remove(task_id))

    async def _record(
        self,
        *,
        task_id: str,
        status: str,
        detail: str | None = None,
        result_ref: str | None = None,
        result_text: str | None = None,
        parent_run_id: str | None = None,
        parent_tool_call_id: str | None = None,
        child_session_id: str | None = None,
        agent_type: str | None = None,
        wait: bool | None = None,
    ) -> None:
        if not self.record_event:
            return
        terminal = status in {"completed", "failed", "cancelled", "interrupted"}
        await self.record_event(
            task_id=task_id,
            session_id=self.session_id,
            parent_run_id=parent_run_id,
            parent_tool_call_id=parent_tool_call_id,
            child_session_id=child_session_id,
            agent_type=agent_type,
            wait=wait,
            command=self._commands.get(task_id, ""),
            status=status,
            detail=detail,
            result_ref=result_ref,
            result_text=result_text,
            terminal=terminal,
        )

    async def record_started(
        self,
        *,
        task_id: str,
        command: str,
        parent_run_id: str | None = None,
        parent_tool_call_id: str | None = None,
        child_session_id: str | None = None,
        agent_type: str | None = None,
        wait: bool | None = None,
    ) -> None:
        self._commands[task_id] = command
        if child_session_id:
            self._child_sessions[task_id] = child_session_id
        await self._record(
            task_id=task_id,
            status="started",
            parent_run_id=parent_run_id,
            parent_tool_call_id=parent_tool_call_id,
            child_session_id=child_session_id,
            agent_type=agent_type,
            wait=wait,
        )

    async def record_activity(self, task_id: str, detail: str) -> None:
        await self._record(task_id=task_id, status="activity", detail=detail)

    def cancel_all(self) -> list[tuple[str, str]]:
        """Cancel all pending tasks. Returns list of (task_id, command) for cancelled tasks."""
        cancelled: list[tuple[str, str]] = []
        for task_id, task in list(self._tasks.items()):
            if not task.done():
                command = self._commands.get(task_id, "")
                cancelled.append((task_id, command))
                task.cancel()
        return cancelled

    def cancel(self, task_id: str) -> str | None:
        """Cancel a single task. Returns the command if cancelled, None if not found or already done."""
        task = self._tasks.get(task_id)
        if task is None or task.done():
            return None
        command = self._commands.get(task_id, "")
        task.cancel()
        return command

    def list_pending(self) -> list[tuple[str, str]]:
        pending = [(tid, self._commands[tid]) for tid, t in self._tasks.items() if not t.done()]
        pending.extend((tid, self._commands.get(tid, "")) for tid in self._reserved)
        return pending

    def to_rehydration_refs(self) -> list[dict[str, str]]:
        return [{"task_id": task_id, "command": command} for task_id, command in sorted(self._commands.items())]

    async def inject(self, messages: list[dict]) -> None:
        if self.on_result:
            await self.on_result(messages)
        else:
            _logger.warning("Background task result dropped — on_result not wired")

    @property
    def pending_count(self) -> int:
        return sum(1 for t in self._tasks.values() if not t.done()) + len(self._reserved)

    def _write_result_file(self, task_id: str, content: str) -> Path:
        path = self._result_path(task_id)
        if path is None:
            raise ValueError("Invalid background task result path")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def _result_path(self, task_id: str) -> Path | None:
        root = RESULT_BASE.resolve()
        result_dir = (root / self.session_id / "bg_results").resolve()
        if not result_dir.is_relative_to(root):
            return None
        path = (result_dir / f"{task_id}.txt").resolve()
        return path if path.is_relative_to(result_dir) else None

    async def read_background_result(self, task_id: str) -> str | None:
        if self.read_result:
            return await self.read_result(task_id)
        path = self._result_path(task_id)
        if path is None:
            return None
        if not path.exists():
            return None
        return await asyncio.to_thread(path.read_text, encoding="utf-8")

    async def deliver_result(
        self,
        task_id: str,
        result: str,
        label: str,
        status: str,
        emit: Callable[[Any], Awaitable[None]] | None,
        child_session_id: str | None = None,
        parent_tool_call_id: str | None = None,
        agent_type: str | None = None,
        wait: bool | None = None,
    ) -> None:
        result_ref = f"background://{task_id}"
        completion_id = f"bg:{task_id}:{status}"
        if self.claim_completion:
            completion = await self.claim_completion(
                task_id=task_id,
                session_id=self.session_id,
                status=status,
                result_ref=result_ref,
                result_text=result,
                completion_id=completion_id,
            )
            if completion.get("delivered"):
                return
            completion_id = str(completion["completion_id"])
            status = str(completion["status"])
            result_ref = str(completion.get("result_ref") or result_ref)
            result = str(completion.get("result_text") or result)

        if completion_id in self._delivered_completion_ids:
            if self.mark_completion_delivered:
                await self.mark_completion_delivered(
                    session_id=self.session_id,
                    task_id=task_id,
                    completion_id=completion_id,
                )
            return

        try:
            await asyncio.to_thread(self._write_result_file, task_id, result)
        except Exception:
            _logger.warning("Failed to write supplementary background result file", exc_info=True)

        notification = (
            f'<background_agent_result task_id="{task_id}" status="{status}">\n'
            "This is a hidden completion event. The user cannot see this message.\n"
            "Write a visible assistant response now. Summarize the result directly for the user.\n"
            "If the result contains sources, IDs, links, or evidence, include the relevant ones inline.\n"
            "Do not say the sources/result are above, hidden, attached, in a file, or in the bg result.\n\n"
            f"<result>\n{result}\n</result>\n"
            "</background_agent_result>"
        )
        messages = [
            {
                "role": Role.USER,
                "content": notification,
                "is_meta": True,
                "client_id": f"bg:{task_id}:{status}",
            }
        ]

        if emit:
            await emit(
                BackgroundTaskEvent(
                    event_id=completion_id,
                    task_id=task_id,
                    session_id=self.session_id,
                    child_run_id=task_id,
                    child_session_id=child_session_id,
                    parent_tool_call_id=parent_tool_call_id,
                    agent_type=agent_type,
                    wait=wait,
                    command=label,
                    status=status,
                    result_ref=result_ref,
                    model_visible=True,
                    ui_visible=False,
                    terminal=True,
                )
            )

        if not self.claim_completion:
            await self._record(
                task_id=task_id,
                status=status,
                result_ref=result_ref,
                result_text=result,
            )

        await self.inject(messages)
        self._delivered_completion_ids.add(completion_id)
        if self.mark_completion_delivered:
            await self.mark_completion_delivered(
                session_id=self.session_id,
                task_id=task_id,
                completion_id=completion_id,
            )


@dataclass
class ToolContext:
    """Shared context for tool execution."""

    session_state: SessionState
    registry: "ToolRegistry"
    run: RunContext
    io: IOBridge
    services: dict[str, Any] = field(default_factory=dict)
    area: AreaContext | None = None
    ledger: SharedLedger | None = None
    spawn_fn: Callable[..., Awaitable[Any]] | None = None
    background_tasks: BackgroundTaskRegistry = field(default_factory=BackgroundTaskRegistry)
    run_registry: "RunRegistry | None" = None
    # UsageTracker of the caller. Spawned subagents create their own tracker
    # for their internal LLM calls and, on completion, roll the resulting
    # `cost` (not the token usage — see SpawnResult docstring) into this
    # one. None at the top-level chat context until chat.py wires it.
    parent_tracker: Any = None

    @property
    def session_id(self) -> str:
        return self.session_state.session_id

    @property
    def skip_approvals(self) -> bool:
        return self.run.approval_controls.skip_approvals

    @property
    def auto_approve(self) -> set[str]:
        return self.session_state.auto_approve | self.run.extra_auto_approve

    @property
    def capabilities(self) -> frozenset[str]:
        return frozenset(self.services)

    def to_rehydration_state(self) -> dict[str, Any]:
        return self.run.to_rehydration_state(
            pending_approvals=sorted((self.io.pending_approvals or {}).keys()),
            background_tasks=self.background_tasks.to_rehydration_refs(),
        )

    def get_client[T](self, id: str, client_type: type[T]) -> T | None:
        s = self.services.get(id)
        return s if isinstance(s, client_type) else None


@dataclass
class ToolExecution:
    """Per-tool execution context. Pairs tool identity with shared context."""

    tool_id: str
    tool_name: str
    ctx: ToolContext

    async def request_connection(
        self,
        descriptor: "IntegrationConnectionDescriptor",
        *,
        source: Literal["recovery", "suggestion"],
        detail: str | None = None,
    ) -> bool:
        if descriptor.integration_id in self.ctx.run.declined_connections:
            return False
        if self.ctx.io.get_suspension:
            try:
                suspension = await self.ctx.io.get_suspension(
                    run_id=self.ctx.run.run_id,
                    suspension_id=self.tool_id,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.exception("Connection suspension lookup failed")
            else:
                if (
                    suspension
                    and suspension.get("kind") == "integration_connection"
                    and suspension.get("status") != "pending"
                ):
                    resolution = suspension.get("resolution") or {}
                    accepted = bool(resolution.get("approved"))
                    await _approval_callback_best_effort(
                        self.ctx.io.consume_suspension,
                        "consume",
                        run_id=self.ctx.run.run_id,
                        suspension_id=self.tool_id,
                    )
                    if not accepted:
                        self.ctx.run.declined_connections.add(descriptor.integration_id)
                    return accepted

        if not self.ctx.io.emit or self.ctx.io.pending_connections is None:
            return False

        request_detail = (detail or descriptor.detail or descriptor.capability).strip()
        expires_at = (datetime.now(UTC) + timedelta(seconds=self.ctx.io.approval_timeout_seconds)).isoformat()
        await _approval_callback_best_effort(
            self.ctx.io.record_connection,
            "record connection",
            run_id=self.ctx.run.run_id,
            session_id=self.ctx.session_id,
            tool_call_id=self.tool_id,
            descriptor=descriptor,
            source=source,
            detail=request_detail,
            expires_at=expires_at,
        )

        loop = asyncio.get_running_loop()
        future: asyncio.Future[ApprovalResponse] = loop.create_future()
        self.ctx.io.pending_connections[self.tool_id] = future
        if self.ctx.io.pending_connection_descriptors is not None:
            self.ctx.io.pending_connection_descriptors[self.tool_id] = descriptor
        try:
            await self.ctx.io.emit(
                ConnectionNeededEvent(
                    run_id=self.ctx.run.run_id,
                    tool_id=self.tool_id,
                    integration_id=descriptor.integration_id,
                    connection_id=descriptor.connection_id,
                    label=descriptor.label,
                    reason=descriptor.state,
                    detail=request_detail,
                    capability=descriptor.capability,
                    action=descriptor.action,
                    settings_tab=descriptor.settings_tab,
                    required_scopes=list(descriptor.required_scopes),
                    source=source,
                )
            )
            response = await asyncio.wait_for(future, timeout=self.ctx.io.approval_timeout_seconds)
        except TimeoutError:
            await _approval_callback_best_effort(
                self.ctx.io.resolve_connection,
                "resolve connection",
                run_id=self.ctx.run.run_id,
                tool_call_id=self.tool_id,
                status="expired",
                result_feedback="Connection request timed out",
            )
            return False
        finally:
            self.ctx.io.pending_connections.pop(self.tool_id, None)
            if self.ctx.io.pending_connection_descriptors is not None:
                self.ctx.io.pending_connection_descriptors.pop(self.tool_id, None)

        accepted = bool(response["approved"])
        if not accepted:
            self.ctx.run.declined_connections.add(descriptor.integration_id)
        await _approval_callback_best_effort(
            self.ctx.io.resolve_connection,
            "resolve connection",
            run_id=self.ctx.run.run_id,
            tool_call_id=self.tool_id,
            status="approved" if accepted else "rejected",
            result_feedback=response.get("result", "").strip() or None,
        )
        if accepted:
            await _approval_callback_best_effort(
                self.ctx.io.consume_suspension,
                "consume",
                run_id=self.ctx.run.run_id,
                suspension_id=self.tool_id,
            )
        return accepted

    async def request_approval(
        self,
        description: str,
        *,
        diff: str | None = None,
        preview: str | None = None,
    ) -> Rejection | None:
        if self.ctx.io.get_suspension:
            try:
                suspension = await self.ctx.io.get_suspension(
                    run_id=self.ctx.run.run_id,
                    suspension_id=self.tool_id,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                _logger.exception("Approval suspension lookup failed")
            else:
                if suspension and suspension.get("status") != "pending":
                    resolution = suspension.get("resolution") or {}
                    if resolution.get("approved"):
                        persistence_error = await _approval_callback_required(
                            self.ctx.io.consume_suspension,
                            "consume",
                            run_id=self.ctx.run.run_id,
                            suspension_id=self.tool_id,
                        )
                        if persistence_error:
                            return Rejection.persistence_failure(
                                "Approval could not be consumed — action cancelled",
                                persistence_error,
                            )
                        return None
                    feedback = str(resolution.get("result") or suspension.get("result_feedback") or "").strip() or None
                    return Rejection(feedback=feedback)

        override = self.ctx.registry.get_override(self.tool_name)
        ui_connected = self.ctx.io.emit is not None and self.ctx.io.pending_approvals is not None
        ask_must_block = override == ToolOverrideDecision.ASK and ui_connected
        tool = self.ctx.registry.get(self.tool_name)
        bypass_allowed = tool is None or tool.policy.allow_approval_bypass
        if (
            bypass_allowed
            and not ask_must_block
            and (self.ctx.skip_approvals or self.tool_name in self.ctx.auto_approve)
        ):
            return None

        action = tool.policy.action.value if tool else "write"
        scope = tool.policy.scope.value if tool else "internal"
        expires_at = (datetime.now(UTC) + timedelta(seconds=self.ctx.io.approval_timeout_seconds)).isoformat()

        persistence_error = await _approval_callback_required(
            self.ctx.io.record_approval,
            "record",
            run_id=self.ctx.run.run_id,
            session_id=self.ctx.session_id,
            tool_call_id=self.tool_id,
            tool_name=self.tool_name,
            action=action,
            scope=scope,
            preview=preview,
            diff=diff,
            expires_at=expires_at,
        )
        if persistence_error:
            return Rejection.persistence_failure(
                "Approval could not be recorded — action cancelled",
                persistence_error,
            )

        if not self.ctx.io.emit or self.ctx.io.pending_approvals is None:
            await _approval_callback_best_effort(
                self.ctx.io.resolve_approval,
                "resolve",
                run_id=self.ctx.run.run_id,
                tool_call_id=self.tool_id,
                status="cancelled",
                result_feedback="No UI connected — cannot approve",
            )
            return Rejection(feedback="No UI connected — cannot approve")

        # Register a Future scoped to THIS tool_id and await it. Multiple
        # tools approving in parallel each wait on their own Future, so
        # responses don't race a shared queue.
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ApprovalResponse] = loop.create_future()
        self.ctx.io.pending_approvals[self.tool_id] = future

        try:
            await self.ctx.io.emit(
                ApprovalNeededEvent(
                    tool_id=self.tool_id,
                    name=self.tool_name,
                    path=description,
                    diff=diff,
                    content_preview=preview if not diff else None,
                )
            )
            response = await asyncio.wait_for(future, timeout=self.ctx.io.approval_timeout_seconds)
        except asyncio.CancelledError:
            await _approval_callback_best_effort(
                self.ctx.io.resolve_approval,
                "resolve",
                run_id=self.ctx.run.run_id,
                tool_call_id=self.tool_id,
                status="cancelled",
                result_feedback="Approval cancelled",
            )
            raise
        except TimeoutError:
            await _approval_callback_best_effort(
                self.ctx.io.resolve_approval,
                "resolve",
                run_id=self.ctx.run.run_id,
                tool_call_id=self.tool_id,
                status="expired",
                result_feedback="Approval timed out",
            )
            return Rejection(feedback="Approval timed out")
        finally:
            self.ctx.io.pending_approvals.pop(self.tool_id, None)

        if not response["approved"]:
            feedback = response.get("result", "").strip() or None
            await _approval_callback_best_effort(
                self.ctx.io.resolve_approval,
                "resolve",
                run_id=self.ctx.run.run_id,
                tool_call_id=self.tool_id,
                status="rejected",
                result_feedback=feedback,
            )
            return Rejection(feedback=feedback)

        persistence_error = await _approval_callback_required(
            self.ctx.io.resolve_approval,
            "resolve",
            run_id=self.ctx.run.run_id,
            tool_call_id=self.tool_id,
            status="approved",
            result_feedback=response.get("result", "").strip() or None,
        )
        if persistence_error:
            return Rejection.persistence_failure(
                "Approval could not be persisted — action cancelled",
                persistence_error,
            )
        persistence_error = await _approval_callback_required(
            self.ctx.io.consume_suspension,
            "consume",
            run_id=self.ctx.run.run_id,
            suspension_id=self.tool_id,
        )
        if persistence_error:
            return Rejection.persistence_failure(
                "Approval could not be consumed — action cancelled",
                persistence_error,
            )

        return None

    async def request_input(self, *, html: str, title: str) -> str | None:
        """Emit input_needed and block until /tools/result resolves it.
        Returns the client's action envelope verbatim ({"action": ..., "values": ...});
        timeout resolves to the cancel envelope. None = no interactive client."""
        if not self.ctx.io.emit or self.ctx.io.pending_inputs is None:
            return None
        loop = asyncio.get_running_loop()
        future: asyncio.Future[ApprovalResponse] = loop.create_future()
        self.ctx.io.pending_inputs[self.tool_id] = future
        try:
            await self.ctx.io.emit(InputNeededEvent(tool_id=self.tool_id, name=self.tool_name, title=title, html=html))
            response = await asyncio.wait_for(future, timeout=self.ctx.io.approval_timeout_seconds)
        except TimeoutError:
            return json.dumps({"action": "cancel", "values": {}})
        finally:
            self.ctx.io.pending_inputs.pop(self.tool_id, None)
        return response["result"]
