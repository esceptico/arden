"""Tool scoping as a filter algebra.

A scope is a predicate over tools, composed with `&`, `|`, `~` — the shape
python-telegram-bot uses for update filters.

    tools.read | tools.named("area_submit_report") | tools.prefix("area_page_")
    tools.system | (tools.integrations & tools.read)

Filters are built in code and never serialized. What persists is a scope *key*
(see `arden.tools.scopes`), so no stored value mentions a tool name and renaming
a tool cannot quietly narrow a saved scope.

A scope is a hard outer gate: it filters the pool AFTER every other selection
(capabilities, action class, extras) so a scoped run can never widen past its
author's declaration.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass

from arden.tools.core.types import ToolAction


@dataclass(frozen=True, slots=True)
class ToolFacts:
    """What a filter may ask about a tool. Built by the registry, which owns the
    system-vs-service classification so no caller encodes the `_` prefix."""

    name: str
    action: ToolAction
    source: str
    external: bool


class ToolFilter(ABC):
    __slots__ = ()

    @abstractmethod
    def matches(self, tool: ToolFacts) -> bool: ...

    @property
    @abstractmethod
    def name(self) -> str: ...

    def __and__(self, other: "ToolFilter") -> "ToolFilter":
        return _All((self, other))

    def __or__(self, other: "ToolFilter") -> "ToolFilter":
        return _Any((self, other))

    def __invert__(self) -> "ToolFilter":
        return _Not(self)

    def __repr__(self) -> str:
        return self.name


class _Any(ToolFilter):
    __slots__ = ("_parts",)

    def __init__(self, parts: Iterable[ToolFilter]) -> None:
        self._parts = tuple(parts)

    def matches(self, tool: ToolFacts) -> bool:
        return any(part.matches(tool) for part in self._parts)

    def __or__(self, other: ToolFilter) -> ToolFilter:
        return _Any((*self._parts, other))

    @property
    def name(self) -> str:
        return " | ".join(part.name for part in self._parts)


class _All(ToolFilter):
    __slots__ = ("_parts",)

    def __init__(self, parts: Iterable[ToolFilter]) -> None:
        self._parts = tuple(parts)

    def matches(self, tool: ToolFacts) -> bool:
        return all(part.matches(tool) for part in self._parts)

    def __and__(self, other: ToolFilter) -> ToolFilter:
        return _All((*self._parts, other))

    @property
    def name(self) -> str:
        # `&` binds tighter than `|`, so a nested union needs its own parens or
        # the rendered expression reads with the wrong precedence.
        return " & ".join(f"({part.name})" if isinstance(part, _Any) else part.name for part in self._parts)


class _Not(ToolFilter):
    __slots__ = ("_inner",)

    def __init__(self, inner: ToolFilter) -> None:
        self._inner = inner

    def matches(self, tool: ToolFacts) -> bool:
        return not self._inner.matches(tool)

    @property
    def name(self) -> str:
        return f"~{self._inner.name}"


class _Everything(ToolFilter):
    __slots__ = ()

    def matches(self, tool: ToolFacts) -> bool:
        return True

    @property
    def name(self) -> str:
        return "*"


class _Action(ToolFilter):
    __slots__ = ("_action",)

    def __init__(self, action: ToolAction) -> None:
        self._action = action

    def matches(self, tool: ToolFacts) -> bool:
        return tool.action == self._action

    @property
    def name(self) -> str:
        return self._action.value


class _External(ToolFilter):
    __slots__ = ("_external",)

    def __init__(self, external: bool) -> None:
        self._external = external

    def matches(self, tool: ToolFacts) -> bool:
        return tool.external is self._external

    @property
    def name(self) -> str:
        return "integrations" if self._external else "system"


class _Source(ToolFilter):
    __slots__ = ("_source",)

    def __init__(self, source: str) -> None:
        self._source = source

    def matches(self, tool: ToolFacts) -> bool:
        return tool.source == self._source

    @property
    def name(self) -> str:
        return f"source({self._source})"


class _Named(ToolFilter):
    __slots__ = ("_tool_name",)

    def __init__(self, tool_name: str) -> None:
        self._tool_name = tool_name

    def matches(self, tool: ToolFacts) -> bool:
        return tool.name == self._tool_name

    @property
    def name(self) -> str:
        return self._tool_name


class _Prefix(ToolFilter):
    __slots__ = ("_prefix",)

    def __init__(self, prefix: str) -> None:
        self._prefix = prefix

    def matches(self, tool: ToolFacts) -> bool:
        return tool.name.startswith(self._prefix)

    @property
    def name(self) -> str:
        return f"{self._prefix}*"


class _ToolFilters:
    """Namespace for the leaves, mirroring `telegram.ext.filters`."""

    ALL = _Everything()

    read = _Action(ToolAction.READ)
    draft = _Action(ToolAction.DRAFT)
    write = _Action(ToolAction.WRITE)
    execute = _Action(ToolAction.EXECUTE)

    system = _External(False)
    integrations = _External(True)

    @staticmethod
    def named(tool_name: str) -> ToolFilter:
        return _Named(tool_name)

    @staticmethod
    def prefix(prefix: str) -> ToolFilter:
        return _Prefix(prefix)

    @staticmethod
    def source(source: str) -> ToolFilter:
        return _Source(source)


tools = _ToolFilters()
