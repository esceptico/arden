import asyncio
import json
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, NotRequired, TypedDict
from uuid import uuid4

import arden.tools.core.background_tasks as _background_tasks
from arden.agent import ToolOutcomeStatus, ToolResult
from arden.agent.agent import RunBudget
from arden.agent.ledger import SharedLedger
from arden.context.models import AreaContext, SessionState
from arden.events.sse import ApprovalNeededEvent, ConnectionNeededEvent, InputNeededEvent
from arden.logging import get_logger
from arden.tools.core.contracts import ConnectionDescriptor, RunRegistryContract, ToolRegistryContract
from arden.tools.core.types import ToolOverrideDecision

_logger = get_logger(__name__)


class ApprovalResponse(TypedDict):
    approved: bool
    result: str
    # Who decided: "user" (explicit /tools/result), "rule" (skip-approvals /
    # auto-approve resolving pending futures). Absent means "user".
    source: NotRequired[str]


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


@dataclass(frozen=True, slots=True)
class ResourceObservation:
    """A backend-only snapshot a run may safely mutate from."""

    version: str | None
    container_version: str | None
    content_read: bool
    # Tool result that currently carries the model-visible full content. This
    # is separate from the version receipt used for mutation safety.
    content_tool_call_id: str | None = None


_MAX_FILE_PATH_OBSERVATIONS = 10_000


@dataclass
class FileDiscoveryLedger:
    """Run-scoped evidence about filesystem paths and failed discovery roots."""

    observed_paths: OrderedDict[str, Literal["file", "directory", "other"]] = field(default_factory=OrderedDict)
    miss_counts: dict[str, int] = field(default_factory=dict)

    def observe(self, path: str, kind: Literal["file", "directory", "other"]) -> None:
        self.observed_paths[path] = kind
        self.observed_paths.move_to_end(path)
        while len(self.observed_paths) > _MAX_FILE_PATH_OBSERVATIONS:
            self.observed_paths.popitem(last=False)

    def discover(self, root: str) -> None:
        root_path = Path(root)
        for missed_root in tuple(self.miss_counts):
            if Path(missed_root) == root_path or Path(missed_root).is_relative_to(root_path):
                del self.miss_counts[missed_root]

    def miss(self, root: str) -> None:
        self.miss_counts[root] = self.miss_counts.get(root, 0) + 1

    def snapshot(self) -> dict[str, dict[str, str] | dict[str, int]]:
        return {
            "observed_paths": dict(self.observed_paths),
            "miss_counts": dict(self.miss_counts),
        }


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
    # Effort for those roles, when the user set one. Beside the models because
    # they travel together: the role picks both, and a child spawned for the
    # role should not inherit whatever the per-model map says.
    research_reasoning_effort: str | None = None
    workflow_reasoning_effort: str | None = None
    deferred_tools_enabled: bool = False
    deferred_tool_loader: Literal["load_tools", "tool_search"] = "load_tools"
    loaded_tools: set[str] = field(default_factory=set)
    declined_connections: set[str] = field(default_factory=set)
    allowed_tool_names: set[str] | None = None
    loop_task_id: str | None = None
    # Trusted, run-scoped automation identity. Session metadata may describe
    # an automation origin, but must never grant authority to later runs.
    automation_id: str | None = None
    active_plan_ref: str | None = None
    research_scope_id: str | None = None
    _resource_observations: dict[str, ResourceObservation] = field(default_factory=dict, repr=False)
    _file_discovery: FileDiscoveryLedger = field(default_factory=FileDiscoveryLedger, repr=False)
    # Builds an IOBridge bound to a child (subagent) session's own SSE bus, so a
    # spawned FULL subagent streams to its own session exactly like a normal run
    # instead of the parent's bus. Set by the chat service (which owns the
    # BusRegistry); None in non-chat/test paths (then a child reuses the parent io).
    child_io_factory: "ChildIOFactory | None" = None

    def __post_init__(self) -> None:
        if self.budget is None:
            self.budget = RunBudget()

    def observe_resource(
        self,
        resource_id: str,
        *,
        version: str | None,
        container_version: str | None,
        content_read: bool,
        content_tool_call_id: str | None = None,
    ) -> None:
        previous = self._resource_observations.get(resource_id)
        if previous is not None and previous.version == version and previous.container_version == container_version:
            if previous.content_read and not content_read:
                content_read = True
                content_tool_call_id = previous.content_tool_call_id
        self._resource_observations[resource_id] = ResourceObservation(
            version,
            container_version,
            content_read,
            content_tool_call_id if content_read else None,
        )

    def resource_observation(self, resource_id: str) -> ResourceObservation | None:
        return self._resource_observations.get(resource_id)

    def resource_observations(self) -> dict[str, ResourceObservation]:
        return dict(self._resource_observations)

    def downgrade_resource_observations(self) -> None:
        """Keep CAS versions after compaction, but require content reads again."""
        for resource_id, observation in tuple(self._resource_observations.items()):
            if observation.content_read:
                self._resource_observations[resource_id] = ResourceObservation(
                    observation.version,
                    observation.container_version,
                    False,
                )

    def observe_file_path(self, path: str, kind: Literal["file", "directory", "other"]) -> None:
        self._file_discovery.observe(path, kind)

    def reset_file_discovery(self, root: str) -> None:
        self._file_discovery.discover(root)

    def record_file_miss(self, root: str) -> None:
        self._file_discovery.miss(root)

    def file_discovery_state(self) -> dict[str, dict[str, str] | dict[str, int]]:
        return self._file_discovery.snapshot()

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
            "loaded_tools": sorted(self.loaded_tools),
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
        loaded_tools = state.get("loaded_tools")
        if isinstance(loaded_tools, list):
            self.loaded_tools = {value for value in loaded_tools if isinstance(value, str)}


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
    pending_connection_descriptors: dict[str, ConnectionDescriptor] | None = None
    record_approval: Callable[..., Awaitable[None]] | None = None
    resolve_approval: Callable[..., Awaitable[None]] | None = None
    record_connection: Callable[..., Awaitable[None]] | None = None
    resolve_connection: Callable[..., Awaitable[None]] | None = None
    get_suspension: Callable[..., Awaitable[dict | None]] | None = None
    consume_suspension: Callable[..., Awaitable[None]] | None = None
    # Generic run-suspension writes (kind-agnostic, unlike record_approval /
    # resolve_approval which bake in kind='tool_approval'). The spawner uses
    # them to make an awaited child's wait durable (kind='subagent_result').
    record_suspension: Callable[..., Awaitable[None]] | None = None
    resolve_suspension: Callable[..., Awaitable[None]] | None = None
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
    # The spawn's identity in the PARENT session's registry, plus that registry
    # itself — the factory wires the CHILD session's own registry so grandchild
    # results are queued into this child's steering inbox (or bubbled up the
    # parent chain when the child already finished).
    task_id: str | None = None
    parent_background_tasks: "_background_tasks.BackgroundTaskRegistry | None" = None


@dataclass(frozen=True, slots=True)
class ChildSession:
    """A subagent's own-session io, the terminal `finish(status)` that closes its
    run framing (durable status + RunFinished/RunCancelled on its bus), and the
    cleanup that drains + evicts its bus so a never-opened child doesn't leak its
    durable-persist worker."""

    io: IOBridge
    finish: Callable[[str], Awaitable[None]]
    aclose: Callable[[], Awaitable[None]]
    # The child session's OWN registry, wired for durable rows + nested result
    # delivery. The spawner installs it as the child ToolContext's
    # background_tasks so the child's spawns record through its session, not
    # the parent's — the topology cancel_subtree walks.
    background_tasks: "_background_tasks.BackgroundTaskRegistry | None" = None


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


def _require_connection_callback[T](
    callback: Callable[..., Awaitable[T]] | None,
    label: str,
) -> Callable[..., Awaitable[T]]:
    if callback is None:
        raise RuntimeError(f"Connection {label} persistence callback is not configured")
    return callback



@dataclass
class ToolContext:
    """Shared context for tool execution."""

    session_state: SessionState
    registry: ToolRegistryContract
    run: RunContext
    io: IOBridge
    services: dict[str, Any] = field(default_factory=dict)
    area: AreaContext | None = None
    ledger: SharedLedger | None = None
    spawn_fn: Callable[..., Awaitable[Any]] | None = None
    background_tasks: _background_tasks.BackgroundTaskRegistry = field(default_factory=_background_tasks.BackgroundTaskRegistry)
    run_registry: RunRegistryContract | None = None
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
        descriptor: ConnectionDescriptor,
        *,
        source: Literal["recovery", "suggestion"],
        detail: str | None = None,
    ) -> bool:
        if descriptor.integration_id in self.ctx.run.declined_connections:
            return False

        get_suspension = _require_connection_callback(self.ctx.io.get_suspension, "lookup")
        record_connection = _require_connection_callback(self.ctx.io.record_connection, "record")
        resolve_connection = _require_connection_callback(self.ctx.io.resolve_connection, "resolve")
        consume_suspension = _require_connection_callback(self.ctx.io.consume_suspension, "consume")

        suspension = await get_suspension(
            run_id=self.ctx.run.run_id,
            suspension_id=self.tool_id,
        )
        if (
            suspension
            and suspension.get("kind") == "integration_connection"
            and suspension.get("status") != "pending"
        ):
            resolution = suspension.get("resolution") or {}
            accepted = bool(resolution.get("approved"))
            await consume_suspension(
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
        await record_connection(
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
                    account_ref=descriptor.account_ref,
                )
            )
            response = await asyncio.wait_for(future, timeout=self.ctx.io.approval_timeout_seconds)
        except TimeoutError:
            await resolve_connection(
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
        await resolve_connection(
            run_id=self.ctx.run.run_id,
            tool_call_id=self.tool_id,
            status="approved" if accepted else "rejected",
            result_feedback=response.get("result", "").strip() or None,
        )
        if accepted:
            await consume_suspension(
                run_id=self.ctx.run.run_id,
                suspension_id=self.tool_id,
            )
        else:
            self.ctx.run.declined_connections.add(descriptor.integration_id)
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
        # Requester attribution: for a child agent this session IS the child
        # session, so the card can finally say "agent X wants to run Y" and a
        # cross-session index can group by requester.
        agent_type = self.ctx.session_state.agent_type
        agent_name = self.ctx.session_state.name if agent_type else None
        parent_session_id = self.ctx.session_state.parent_session_id

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
            description=description,
            agent_type=agent_type,
            agent_name=agent_name,
            parent_session_id=parent_session_id,
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
                    run_id=self.ctx.run.run_id,
                    session_id=self.ctx.session_id,
                    agent_type=agent_type,
                    agent_name=agent_name,
                    action=action,
                    scope=scope,
                    expires_at=expires_at,
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
                source="cancel",
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
                source="timeout",
            )
            return Rejection(feedback="Approval timed out")
        finally:
            self.ctx.io.pending_approvals.pop(self.tool_id, None)

        decision_source = str(response.get("source") or "user")
        if not response["approved"]:
            feedback = response.get("result", "").strip() or None
            await _approval_callback_best_effort(
                self.ctx.io.resolve_approval,
                "resolve",
                run_id=self.ctx.run.run_id,
                tool_call_id=self.tool_id,
                status="rejected",
                result_feedback=feedback,
                source=decision_source,
            )
            return Rejection(feedback=feedback)

        persistence_error = await _approval_callback_required(
            self.ctx.io.resolve_approval,
            "resolve",
            run_id=self.ctx.run.run_id,
            tool_call_id=self.tool_id,
            status="approved",
            result_feedback=response.get("result", "").strip() or None,
            source=decision_source,
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
