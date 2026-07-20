from collections import defaultdict
from dataclasses import dataclass

from jinja2 import Environment
from pydantic import BaseModel, Field, model_validator

from ntrp.tools.core import ToolResult, tool
from ntrp.tools.core.context import ToolExecution
from ntrp.tools.core.registry import ToolRegistry, tool_changes_state
from ntrp.tools.core.types import ToolAction, ToolPolicy, ToolScope

DEFERRED_SOURCES = frozenset(
    {"gmail", "calendar", "google_drive", "slack", "_automation", "_background", "_notifications", "_directives", "mcp"}
)

DEFERRED_TOOL_GROUP_BY_NAME = {
    "write_file": "_file_actions",
    "edit_file": "_file_actions",
}

GROUP_ALIASES: dict[str, str] = {
    "email": "gmail",
    "emails": "gmail",
    "gmail": "gmail",
    "mail": "gmail",
    "calendar": "calendar",
    "cal": "calendar",
    "schedule": "calendar",
    "google_drive": "google_drive",
    "drive": "google_drive",
    "docs": "google_drive",
    "sheets": "google_drive",
    "spreadsheets": "google_drive",
    "slack": "slack",
    "automations": "_automation",
    "automation": "_automation",
    "reminders": "_automation",
    "reminder": "_automation",
    "background": "_background",
    "backgrounds": "_background",
    "background_tasks": "_background",
    "background task": "_background",
    "notifications": "_notifications",
    "notification": "_notifications",
    "notify": "_notifications",
    "directives": "_directives",
    "directive": "_directives",
    "rules": "_directives",
    "behavior": "_directives",
    "file": "_file_actions",
    "files": "_file_actions",
    "file_actions": "_file_actions",
    "filesystem": "_file_actions",
    "mcp": "mcp",
}

GROUP_DESCRIPTIONS: dict[str, str] = {
    "gmail": "Search/list/read/send Gmail messages. Use for inbox, emails, Gmail, sending/replying, or communication history.",
    "calendar": "Search/create/edit/delete calendar events. Use for meetings, schedule, availability, appointments, reminders, or rescheduling.",
    "google_drive": "Search/read/create/edit Google Docs and Sheets. Use for Drive documents, spreadsheets, tables, ranges, and rows.",
    "slack": "Search Slack and read channels, DMs, threads, image files, and user profiles. Use for Slack messages, workspace history, coworkers, channels, DMs, threads, screenshots, images, or file IDs.",
    "_automation": "Create/list/update/delete/run autonomous scheduled or event-triggered tasks. Use for reminders, recurring checks, notifications, scheduled agents, or automation management.",
    "_background": "Inspect, cancel, or read background agents. Use only when the user asks about an existing background task; spawning a background agent uses the always-available background() tool.",
    "_notifications": "Send a user-facing notification. Use when the user explicitly asks to be notified or an automation/background flow needs to alert them.",
    "_directives": "Update persistent behavior directives injected into the system prompt. Use when the user asks to change standing behavior, tone, or operating rules.",
    "_file_actions": "Write or edit local files. Use after inspecting files with read_file/list_files/find_files/search_text and deciding an exact file change is needed.",
    "mcp": "Connected MCP server tools. Use for external apps/servers not covered by core tools. Load by server, e.g. mcp:obsidian.",
}

DEFERRED_GROUP_ORDER = (
    "gmail",
    "calendar",
    "google_drive",
    "slack",
    "_automation",
    "_background",
    "_notifications",
    "_directives",
    "_file_actions",
)

DEFERRED_GROUP_LABELS = {
    "gmail": "email",
    "google_drive": "google_drive",
    "_automation": "automations",
    "_background": "background",
    "_notifications": "notifications",
    "_directives": "directives",
    "_file_actions": "files",
}

_env = Environment(trim_blocks=True, lstrip_blocks=True)

_DEFERRED_TOOLS_TEMPLATE = _env.from_string("""Some integration/action tools are deferred to reduce prompt noise. Use `load_tools` before calling tools from these groups. Load tools proactively when the user's request needs the capability; do not ask whether to load them. Do not use filesystem/time/no-op tool calls to discover or unlock deferred tools.

{% for group in groups %}
<deferred_tool_group name="{{ group.label }}" load_group="{{ group.label }}">
{{ group.description }}
Tools: {{ group.tool_names | join(", ") }}.
Load with `load_tools(group="{{ group.label }}")`; the listed tools become callable on the next model step.
{% if group.requires_approval %}
Write/action tools require approval after loading.
{% endif %}
</deferred_tool_group>
{% if not loop.last or mcp_servers %}

{% endif %}
{% endfor %}
{% if mcp_servers %}
<deferred_tool_group name="mcp" load_group="mcp:<server>">
{{ mcp_description }}
Connected MCP servers:
{% for server in mcp_servers %}
- {{ server.name }}: load with `load_tools(group="mcp:{{ server.name }}")`. Tools: {{ server.tools_text }}.
{% endfor %}
</deferred_tool_group>
{% endif %}""")

_NATIVE_DEFERRED_TOOLS_TEMPLATE = _env.from_string("""Some integration/action tools are deferred to reduce prompt noise. Use native tool search by exact tool name before calling these tools. For direct requests about email, calendar, Slack, automations, notifications, directives, file edits, or MCP-backed apps, search the relevant listed names before using memory, local files, or current_time unless the user asked for those sources.
MANDATORY PREREQUISITE: call `tool_search(query="select:<tool_name>")` before calling a listed deferred tool. Loading tools does not execute them; it only makes selected tools callable on the next model step. Do not use filesystem/time/no-op tool calls to discover or unlock deferred tools.

{% for group in groups %}
<native_deferred_tool_group name="{{ group.label }}">
{{ group.description }}
Tool names: {{ group.tool_names | join(", ") }}.
Load exact names with `tool_search(query="select:{{ group.tool_names[0] }}")`.
</native_deferred_tool_group>
{% if not loop.last or mcp_servers %}

{% endif %}
{% endfor %}
{% if mcp_servers %}
<native_deferred_tool_group name="mcp">
{{ mcp_description }}
Connected MCP server tools:
{% for server in mcp_servers %}
- {{ server.name }}: {{ server.tools_text }}.
{% endfor %}
</native_deferred_tool_group>
{% endif %}""")


def is_deferred_tool(name: str, registry: ToolRegistry) -> bool:
    if name in DEFERRED_TOOL_GROUP_BY_NAME:
        return True
    source = registry.get_source(name)
    return source in DEFERRED_SOURCES


def _tool_summary(name: str, registry: ToolRegistry) -> str:
    tool = registry.get(name)
    if tool is None:
        return name
    desc = " ".join((tool.description or "").split())
    if len(desc) > 140:
        desc = desc[:137].rstrip() + "..."
    return f"{name} — {desc}" if desc else name


def _mcp_server_from_name(name: str) -> str | None:
    if not name.startswith("mcp_") or "__" not in name:
        return None
    return name.removeprefix("mcp_").split("__", 1)[0]


def _normalize_group(group: str) -> str:
    group = group.strip().lower()
    if group.startswith("mcp:"):
        return "mcp:" + group.split(":", 1)[1].strip()
    return GROUP_ALIASES.get(group, group)


@dataclass(frozen=True)
class DeferredCatalog:
    by_group: dict[str, list[str]]
    mcp_by_server: dict[str, list[str]]


def tool_schema_names(tools: list[dict]) -> set[str]:
    names: set[str] = set()
    for schema in tools:
        name = schema.get("function", {}).get("name")
        if isinstance(name, str):
            names.add(name)
    return names


def build_deferred_catalog(
    registry: ToolRegistry,
    capabilities: frozenset[str],
    *,
    allowed_names: set[str] | None = None,
) -> DeferredCatalog:
    by_group: dict[str, list[str]] = defaultdict(list)
    mcp_by_server: dict[str, list[str]] = defaultdict(list)
    for name, tool_obj in registry.tools.items():
        if allowed_names is not None and name not in allowed_names:
            continue
        if not tool_obj.policy.permissions.issubset(capabilities):
            continue
        source = registry.get_source(name)
        group = DEFERRED_TOOL_GROUP_BY_NAME.get(name, source)
        if group not in DEFERRED_SOURCES and name not in DEFERRED_TOOL_GROUP_BY_NAME:
            continue
        by_group[group].append(name)
        if source == "mcp":
            server = _mcp_server_from_name(name) or "default"
            mcp_by_server[server].append(name)
    return DeferredCatalog(
        by_group={k: sorted(v) for k, v in by_group.items()},
        mcp_by_server={k: sorted(v) for k, v in mcp_by_server.items()},
    )


def initial_loaded_tool_names(
    registry: ToolRegistry, capabilities: frozenset[str], *, read_only: bool | None = None
) -> set[str]:
    names: set[str] = set()
    for name, tool_obj in registry.tools.items():
        if read_only is not None and (tool_obj.policy.action == ToolAction.READ) != read_only:
            continue
        if not tool_obj.policy.permissions.issubset(capabilities):
            continue
        if is_deferred_tool(name, registry):
            continue
        names.add(name)
    return names


def visible_tool_names(
    registry: ToolRegistry,
    capabilities: frozenset[str],
    loaded: set[str],
    *,
    allowed_names: set[str] | None = None,
) -> set[str]:
    names: set[str] = set()
    for name, tool_obj in registry.tools.items():
        if allowed_names is not None and name not in allowed_names:
            continue
        if not tool_obj.policy.permissions.issubset(capabilities):
            continue
        if is_deferred_tool(name, registry) and name not in loaded:
            continue
        names.add(name)
    return names


def _deferred_prompt_groups(catalog: DeferredCatalog, registry: ToolRegistry) -> list[dict]:
    groups: list[dict] = []
    for source in DEFERRED_GROUP_ORDER:
        names = catalog.by_group.get(source)
        if not names:
            continue
        groups.append(
            {
                "label": DEFERRED_GROUP_LABELS.get(source, source),
                "description": GROUP_DESCRIPTIONS[source],
                "tool_names": names,
                "requires_approval": any(
                    (tool_obj := registry.get(name)) is not None and tool_changes_state(tool_obj) for name in names
                ),
            }
        )
    return groups


def _mcp_prompt_servers(catalog: DeferredCatalog) -> list[dict]:
    servers: list[dict] = []
    for server, names in catalog.mcp_by_server.items():
        if len(names) <= 12:
            tools_text = ", ".join(names)
        else:
            tools_text = ", ".join(names[:10]) + f", ... ({len(names)} tools total)"
        servers.append({"name": server, "tools_text": tools_text})
    return servers


def build_deferred_tools_prompt(
    registry: ToolRegistry,
    capabilities: frozenset[str],
    *,
    allowed_names: set[str] | None = None,
) -> str | None:
    catalog = build_deferred_catalog(registry, capabilities, allowed_names=allowed_names)
    if not catalog.by_group:
        return None

    return _DEFERRED_TOOLS_TEMPLATE.render(
        groups=_deferred_prompt_groups(catalog, registry),
        mcp_description=GROUP_DESCRIPTIONS["mcp"],
        mcp_servers=_mcp_prompt_servers(catalog),
    ).strip()


def build_deferred_tools_prompt_for_schemas(
    registry: ToolRegistry,
    capabilities: frozenset[str],
    tools: list[dict],
) -> str | None:
    return build_deferred_tools_prompt(
        registry,
        capabilities,
        allowed_names=tool_schema_names(tools),
    )


def build_native_deferred_tools_prompt(
    registry: ToolRegistry,
    capabilities: frozenset[str],
    *,
    allowed_names: set[str] | None = None,
) -> str | None:
    catalog = build_deferred_catalog(registry, capabilities, allowed_names=allowed_names)
    if not catalog.by_group:
        return None

    return _NATIVE_DEFERRED_TOOLS_TEMPLATE.render(
        groups=_deferred_prompt_groups(catalog, registry),
        mcp_description=GROUP_DESCRIPTIONS["mcp"],
        mcp_servers=_mcp_prompt_servers(catalog),
    ).strip()


def build_native_deferred_tools_prompt_for_schemas(
    registry: ToolRegistry,
    capabilities: frozenset[str],
    tools: list[dict],
) -> str | None:
    return build_native_deferred_tools_prompt(
        registry,
        capabilities,
        allowed_names=tool_schema_names(tools),
    )


def append_deferred_tools_prompt(
    system_prompt: str,
    registry: ToolRegistry,
    capabilities: frozenset[str],
    tools: list[dict],
    *,
    enabled: bool,
) -> str:
    if not enabled or "## DEFERRED TOOLS" in system_prompt:
        return system_prompt

    deferred_context = build_deferred_tools_prompt_for_schemas(registry, capabilities, tools)
    if not deferred_context:
        return system_prompt

    return f"{system_prompt.rstrip()}\n\n## DEFERRED TOOLS\n{deferred_context}"


class LoadToolsInput(BaseModel):
    group: str | None = Field(
        default=None,
        description="Deferred group to load, e.g. 'email', 'calendar', 'slack', 'automations', 'background', 'notifications', 'directives', 'files', or 'mcp:obsidian'.",
    )
    names: list[str] | None = Field(
        default=None,
        max_length=100,
        description="Exact deferred tool names to load, e.g. ['slack_search', 'slack_thread', 'slack_file'].",
    )

    @model_validator(mode="after")
    def _require_group_or_names(self):
        if not self.group and not self.names:
            raise ValueError("Provide either group or names")
        return self


class ToolSearchInput(BaseModel):
    query: str = Field(
        description='Query to find deferred tools. Use "select:<tool_name>" for direct selection.',
    )
    max_results: int = Field(default=5, ge=1, le=25, description="Maximum number of matches to load.")


def _names_for_group(
    group: str,
    registry: ToolRegistry,
    capabilities: frozenset[str],
    *,
    allowed_names: set[str] | None = None,
) -> tuple[list[str], str | None]:
    normalized = _normalize_group(group)
    catalog = build_deferred_catalog(registry, capabilities, allowed_names=allowed_names)

    if normalized.startswith("mcp:"):
        server = normalized.split(":", 1)[1]
        names = catalog.mcp_by_server.get(server, [])
        if not names:
            servers = ", ".join(sorted(catalog.mcp_by_server)) or "none"
            return [], f"No MCP server {server!r}. Available MCP servers: {servers}."
        return names, None

    if normalized == "mcp":
        if len(catalog.mcp_by_server) == 1:
            return next(iter(catalog.mcp_by_server.values())), None
        servers = ", ".join(f"mcp:{s}" for s in sorted(catalog.mcp_by_server)) or "none"
        return [], f"Load MCP tools by server, e.g. group='mcp:obsidian'. Available MCP groups: {servers}."

    names = catalog.by_group.get(normalized, [])
    if not names:
        groups = ["email", "calendar", "slack", "automations", "background", "notifications", "directives", "files"]
        groups.extend(f"mcp:{s}" for s in sorted(catalog.mcp_by_server))
        return [], "No deferred group {group!r}. Available groups: {groups}.".format(
            group=group,
            groups=", ".join(groups) or "none",
        )
    return names, None


def _dedupe(names: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for name in names:
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _select_tool_names(query: str, available: list[str]) -> list[str] | None:
    match = query.strip()
    if not match.lower().startswith("select:"):
        return None
    selected = [name.strip() for name in match.split(":", 1)[1].split(",") if name.strip()]
    available_set = set(available)
    return [name for name in _dedupe(selected) if name in available_set]


def _search_tool_names(
    query: str,
    registry: ToolRegistry,
    capabilities: frozenset[str],
    *,
    allowed_names: set[str] | None,
    max_results: int,
) -> list[str]:
    catalog = build_deferred_catalog(registry, capabilities, allowed_names=allowed_names)
    available = sorted({name for names in catalog.by_group.values() for name in names})
    selected = _select_tool_names(query, available)
    if selected is not None:
        return selected[:max_results]

    normalized = query.strip().lower()
    if not normalized:
        return []

    exact = [name for name in available if name.lower() == normalized]
    if exact:
        return exact[:max_results]

    tokens = [token for token in normalized.replace("_", " ").replace("-", " ").split() if token]
    if not tokens:
        return []

    scored: list[tuple[int, str]] = []
    for name in available:
        source = registry.get_source(name) or ""
        tool_obj = registry.get(name)
        description = " ".join((tool_obj.description if tool_obj else "").lower().split())
        name_text = name.lower().replace("_", " ").replace("-", " ")
        score = 0
        for token in tokens:
            if token in name_text.split():
                score += 10
            elif token in name_text:
                score += 5
            if token == source.lower():
                score += 4
            if token in description:
                score += 2
        if score:
            scored.append((score, name))

    scored.sort(key=lambda item: (-item[0], item[1]))
    return [name for _, name in scored[:max_results]]


async def load_tools(execution: ToolExecution, args: LoadToolsInput) -> ToolResult:
    registry = execution.ctx.registry
    capabilities = execution.ctx.capabilities

    requested: list[str] = []
    errors: list[str] = []
    if args.group:
        group_names, err = _names_for_group(
            args.group,
            registry,
            capabilities,
            allowed_names=execution.ctx.run.allowed_tool_names,
        )
        requested.extend(group_names)
        if err:
            errors.append(err)
    if args.names:
        requested.extend(args.names)

    requested = _dedupe(requested)
    loaded_now: list[str] = []
    already_loaded: list[str] = []
    already_available: list[str] = []
    unknown: list[str] = []
    unavailable: list[str] = []
    not_allowed: list[str] = []

    for name in requested:
        tool_obj = registry.get(name)
        if tool_obj is None:
            unknown.append(name)
            continue
        if execution.ctx.run.allowed_tool_names is not None and name not in execution.ctx.run.allowed_tool_names:
            not_allowed.append(name)
            continue
        if not tool_obj.policy.permissions.issubset(capabilities):
            unavailable.append(name)
            continue
        if not is_deferred_tool(name, registry):
            already_available.append(name)
            continue
        if name in execution.ctx.run.loaded_tools:
            already_loaded.append(name)
            continue
        execution.ctx.run.loaded_tools.add(name)
        loaded_now.append(name)

    lines: list[str] = []
    if loaded_now:
        lines.append(f"Loaded {len(loaded_now)} deferred tool(s) for this run:")
        lines.extend(f"- {_tool_summary(name, registry)}" for name in loaded_now)
        lines.append("These tools are available on the next model step. Call them normally when needed.")
    if already_loaded:
        lines.append("Already loaded: " + ", ".join(already_loaded) + ".")
    if already_available:
        lines.append("Already available without loading: " + ", ".join(already_available) + ".")
    if unknown:
        lines.append("Unknown tool(s): " + ", ".join(unknown) + ".")
    if unavailable:
        lines.append("Unavailable due to missing capabilities: " + ", ".join(unavailable) + ".")
    if not_allowed:
        lines.append("Not allowed in this run: " + ", ".join(not_allowed) + ".")
    lines.extend(errors)

    if not lines:
        lines.append("No tools loaded.")

    is_error = (
        bool(errors or unknown or unavailable or not_allowed)
        and not loaded_now
        and not already_loaded
        and not already_available
    )
    preview = f"Loaded {len(loaded_now)}" if loaded_now else "No tools loaded"
    return ToolResult(content="\n".join(lines), preview=preview, is_error=is_error)


async def tool_search(execution: ToolExecution, args: ToolSearchInput) -> ToolResult:
    matches = _search_tool_names(
        args.query,
        execution.ctx.registry,
        execution.ctx.capabilities,
        allowed_names=execution.ctx.run.allowed_tool_names,
        max_results=args.max_results,
    )
    if not matches:
        return ToolResult(content="No matching deferred tools found.", preview="No matches")

    return await load_tools(execution, LoadToolsInput(names=matches))


load_tools_tool = tool(
    display_name="Load Tools",
    description=(
        "Load deferred tool schemas into the current run by exact group or tool name. "
        "Use proactively when the user's request needs a deferred capability listed in the DEFERRED TOOLS prompt section. "
        "Loading tools does not execute them; it only makes them callable on the next model step. "
        "Examples: group='slack', group='email', group='calendar', group='automations', group='background', "
        "group='notifications', group='directives', group='files', group='mcp:obsidian', "
        "or names=['slack_search','slack_thread','slack_file']."
    ),
    input_model=LoadToolsInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL),
    execute=load_tools,
)

tool_search_tool = tool(
    display_name="Search Tools",
    description=(
        "Fetch full schemas for deferred tools so they can be called. "
        "MANDATORY PREREQUISITE: use this before calling any deferred tool listed in the DEFERRED TOOLS prompt. "
        "Use query='select:<tool_name>' for exact names, or keywords to search by name/description. "
        "Loading tools does not execute them; selected tools become callable on the next model step."
    ),
    input_model=ToolSearchInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL),
    execute=tool_search,
)
