import json
from collections.abc import Callable
from dataclasses import replace

from arden.agent.model_request import ModelRequest, ModelRequestNext
from arden.llm.models import supports_native_deferred_tools
from arden.tools.core.context import RunContext
from arden.tools.core.registry import ToolRegistry
from arden.tools.deferred import is_deferred_tool, visible_tool_names

_DEFERRED_TOOL_DEPENDENCIES: dict[str, frozenset[str]] = {
    "wiki_edit_page": frozenset({"wiki_read_page"}),
    "wiki_archive_page": frozenset({"wiki_read_page"}),
    "wiki_move_page": frozenset({"wiki_read_page"}),
    "wiki_publish_generated": frozenset({"wiki_read_page"}),
}


def _schema_name(schema: dict) -> str | None:
    name = schema.get("function", {}).get("name")
    return name if isinstance(name, str) else None


def _discovered_tool_names(messages: list[dict]) -> set[str]:
    """Recover native tool-search discoveries from structured history/compaction state."""
    names: set[str] = set()
    for message in messages:
        for call in message.get("provider_tool_calls") or []:
            if not isinstance(call, dict) or call.get("name") != "tool_search":
                continue
            arguments = call.get("arguments")
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except (TypeError, ValueError):
                    continue
            if not isinstance(arguments, dict):
                continue
            tools = arguments.get("tools")
            if isinstance(tools, list):
                names.update(name for name in tools if isinstance(name, str))

        compaction = message.get("compaction")
        if not isinstance(compaction, dict):
            continue
        rehydration = compaction.get("rehydration")
        if not isinstance(rehydration, dict):
            continue
        loaded = rehydration.get("loaded_tools")
        if isinstance(loaded, list):
            names.update(name for name in loaded if isinstance(name, str))
    return names


def _with_allowed_dependencies(names: set[str], allowed: set[str]) -> set[str]:
    expanded = set(names)
    for name in names:
        expanded.update(_DEFERRED_TOOL_DEPENDENCIES.get(name, frozenset()) & allowed)
    return expanded


class DeferredToolsModelRequestMiddleware:
    """Replace the request tool list with always-visible + run-loaded tools."""

    def __init__(
        self,
        *,
        registry: ToolRegistry,
        run: RunContext,
        get_services: Callable[[], dict],
    ):
        self._registry = registry
        self._run = run
        self._get_services = get_services

    def _area_visible_tools(self, request: ModelRequest) -> ModelRequest:
        # Preserve upstream filtering. Operator/read-only paths may pass only non-mutating
        # schemas into Agent.tools; deferred loading must not re-add tools excluded there.
        allowed = {t.get("function", {}).get("name") for t in [*request.tools, *request.deferred_tools]}
        allowed.discard(None)
        capabilities = frozenset(self._get_services())
        native_deferred = supports_native_deferred_tools(request.model)
        self._run.deferred_tool_loader = "tool_search" if native_deferred else "load_tools"
        allowed_deferred = {
            name for name in allowed if isinstance(name, str) and is_deferred_tool(name, self._registry)
        }
        if native_deferred:
            # Native discovery receipts are conversation state. Rehydrate only
            # names still permitted by the current request/registry snapshot.
            discovered = _discovered_tool_names(request.messages) & allowed_deferred
            self._run.loaded_tools.update(_with_allowed_dependencies(discovered, allowed_deferred))
            self._run.loaded_tools.update(
                _with_allowed_dependencies(self._run.loaded_tools & allowed_deferred, allowed_deferred)
            )
        names = visible_tool_names(
            self._registry,
            capabilities,
            self._run.loaded_tools,
            allowed_names=set(allowed),
        )
        if native_deferred:
            names.discard("load_tools")
            # Provider-native search is a reserved special tool, not Arden's
            # function-form loader. Never expose both in one request.
            names.discard("tool_search")
        else:
            names.discard("tool_search")
        deferred_names = {
            name
            for name in allowed
            if name not in names and isinstance(name, str) and is_deferred_tool(name, self._registry)
        }
        return replace(
            request,
            tools=self._registry.get_schemas(capabilities=capabilities, names=names),
            deferred_tools=self._registry.get_schemas(capabilities=capabilities, names=deferred_names),
        )

    async def __call__(self, request: ModelRequest, next_request: ModelRequestNext) -> ModelRequest:
        if not self._run.deferred_tools_enabled:
            return await next_request(
                replace(
                    request,
                    tools=[tool for tool in request.tools if _schema_name(tool) != "tool_search"],
                    deferred_tools=[],
                )
            )

        prepared = self._area_visible_tools(request)
        prepared = await next_request(prepared)
        return self._area_visible_tools(prepared)
