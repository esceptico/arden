import asyncio
from collections.abc import Mapping, Sequence
from typing import Protocol

from arden.tools.core.types import ToolOverrideDecision, ToolPolicy


class ConnectionDescriptor(Protocol):
    integration_id: str
    connection_id: str
    label: str
    capability: str
    action: str
    settings_tab: str
    state: str
    detail: str | None
    required_scopes: tuple[str, ...]


class RegisteredTool(Protocol):
    policy: ToolPolicy


class ToolRegistryContract(Protocol):
    @property
    def tools(self) -> Mapping[str, RegisteredTool]: ...

    def get(self, name: str) -> RegisteredTool | None: ...

    def get_override(self, name: str) -> ToolOverrideDecision | None: ...

    def get_schemas(
        self,
        *,
        read_only: bool | None = None,
        capabilities: frozenset[str] = frozenset(),
    ) -> list[dict]: ...


class SubagentHandleContract(Protocol):
    cancel_requested: bool


class RunStateContract(Protocol):
    run_id: str
    session_id: str
    cancelled: bool
    pending_approvals: Mapping[str, object]

    @property
    def pending_injection_count(self) -> int: ...


class RunRegistryContract(Protocol):
    def mark_session_active(self, session_id: str, run_id: str) -> None: ...

    def clear_session_active(self, session_id: str) -> None: ...

    def list_active_runs(self) -> Sequence[RunStateContract]: ...

    def get_run(self, run_id: str) -> RunStateContract | None: ...

    def get_active_run(self, session_id: str) -> RunStateContract | None: ...

    def sync_session_name(self, session_id: str, name: str) -> None: ...

    def register_subagent(
        self,
        run_id: str,
        tool_call_id: str,
        task: asyncio.Task,
    ) -> SubagentHandleContract | None: ...

    def finish_subagent(self, run_id: str, tool_call_id: str) -> None: ...

    def cancel_subtree(self, child_session_id: str | None) -> list[tuple[str, str]]: ...
