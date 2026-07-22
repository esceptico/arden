from collections.abc import Callable
from dataclasses import replace

from arden.agent.model_request import ModelRequest, ModelRequestNext
from arden.llm.models import supports_native_deferred_tools
from arden.tools.core.context import RunContext
from arden.tools.core.registry import ToolRegistry
from arden.tools.deferred import is_deferred_tool, visible_tool_names


def _schema_name(schema: dict) -> str | None:
    name = schema.get("function", {}).get("name")
    return name if isinstance(name, str) else None


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
        if native_deferred and allowed_deferred:
            allowed.add("tool_search")
        names = visible_tool_names(
            self._registry,
            capabilities,
            self._run.loaded_tools,
            allowed_names=set(allowed),
        )
        if native_deferred:
            names.discard("load_tools")
            if not allowed_deferred:
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
