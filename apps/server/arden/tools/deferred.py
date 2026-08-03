from collections import defaultdict
from dataclasses import dataclass

from jinja2 import Environment
from pydantic import BaseModel, Field, model_validator

from arden.tools.core import ToolResult, tool
from arden.tools.core.context import ToolExecution
from arden.tools.core.registry import ToolRegistry, tool_changes_state
from arden.tools.core.types import ToolAction, ToolPolicy, ToolScope

# Deferral is declared per tool (ToolPolicy.deferred); MCP tools are always
# deferred. A deferred tool's group is DERIVED from its name's namespace prefix
# (the first `_` segment), so the tool name is the single source of truth for
# grouping — there is no membership map to maintain.

GROUP_DESCRIPTIONS: dict[str, str] = {
    "email": "Search/list/read/send Gmail messages. Use for inbox, emails, Gmail, sending/replying, or communication history.",
    "calendar": "Search/create/edit/delete calendar events. Use for meetings, schedule, availability, appointments, reminders, or rescheduling.",
    "drive": "Search/read/create/edit Google Docs and Sheets. Use for Drive documents, spreadsheets, tables, ranges, and rows.",
    "wiki": "List, read, create, edit, and archive managed wiki pages; inspect links; and publish automation-owned generated regions.",
    "slack": "Search Slack and read channels, DMs, threads, image files, and user profiles. Use for Slack messages, workspace history, coworkers, channels, DMs, threads, screenshots, images, or file IDs.",
    "automation": "Create/list/update/delete/run autonomous scheduled or event-triggered tasks. Use for reminders, recurring checks, scheduled agents, or automation management.",
    "loop": "Repeat work in THIS chat on a cadence: loop_create starts one, loop_schedule_wakeup paces it, loop_done ends it.",
    "session": "Create, rename, archive, list, and read chats; search transcripts. Load only when the user explicitly works with sessions as such — creating a chat is never part of a content task like wiki or file edits.",
    "app": "Drive the Arden app itself: assign work to an agent you spawned, raise a needs-you item on the user's Home, or open a place in the UI.",
    "agent": "Stop a running agent you spawned. Its result arrives automatically, and session_read shows its work.",
    "notify": "Send a user-facing notification. Use when the user explicitly asks to be notified or an automation/background flow needs to alert them.",
    "directives": "Update persistent behavior directives injected into the system prompt. Use when the user asks to change standing behavior, tone, or operating rules.",
    "file": "Write or edit local files. Use after inspecting files with file_read/file_list/file_find/file_search_text and deciding an exact file change is needed.",
    "fact": "Fact history and mutations: provenance lookups plus the prepare-then-commit change pair. fact_search and fact_get are always available without loading.",
    "skill": "Create a new reusable skill from the current conversation. skill_use is always available without loading.",
    "mcp": "Connected MCP server tools. Use for external apps/servers not covered by core tools. Load by server, e.g. mcp:obsidian.",
}

DEFERRED_GROUP_ORDER = tuple(group for group in GROUP_DESCRIPTIONS if group != "mcp")

GROUP_ALIASES: dict[str, str] = {
    "emails": "email",
    "gmail": "email",
    "mail": "email",
    "cal": "calendar",
    "schedule": "calendar",
    "google_drive": "drive",
    "docs": "drive",
    "sheets": "drive",
    "automations": "automation",
    "reminders": "automation",
    "reminder": "automation",
    "loops": "loop",
    "sessions": "session",
    "chats": "session",
    "background": "agent",
    "agents": "agent",
    "notifications": "notify",
    "notification": "notify",
    "directive": "directives",
    "rules": "directives",
    "behavior": "directives",
    "files": "file",
    "filesystem": "file",
    "facts": "fact",
    "memory": "fact",
    "skills": "skill",
    "app_control": "app",
    "navigation": "app",
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

_NATIVE_DEFERRED_TOOLS_TEMPLATE = _env.from_string("""Some integration/action tools are deferred to reduce prompt noise. Use native tool search by exact tool name before calling these tools. For direct requests about email, calendar, Slack, wiki pages, automations, notifications, directives, file edits, sessions, fact history or corrections, skills, or MCP-backed apps, search the relevant listed names before using memory, local files, or current_time unless the user asked for those sources.
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
    tool_obj = registry.get(name)
    if tool_obj is None:
        return False
    return tool_obj.policy.deferred or registry.get_source(name) == "mcp"


def deferred_group(name: str) -> str:
    """A deferred tool's group is its namespace prefix — the first `_` segment."""
    return name.split("_", 1)[0]


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
        if not is_deferred_tool(name, registry):
            continue
        if registry.get_source(name) == "mcp":
            by_group["mcp"].append(name)
            server = _mcp_server_from_name(name) or "default"
            mcp_by_server[server].append(name)
        else:
            by_group[deferred_group(name)].append(name)
    return DeferredCatalog(
        by_group={k: sorted(v) for k, v in by_group.items()},
        mcp_by_server={k: sorted(v) for k, v in mcp_by_server.items()},
    )


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
    known = [group for group in DEFERRED_GROUP_ORDER if group in catalog.by_group]
    unknown = sorted(group for group in catalog.by_group if group not in GROUP_DESCRIPTIONS and group != "mcp")
    groups: list[dict] = []
    for group in (*known, *unknown):
        names = catalog.by_group[group]
        groups.append(
            {
                "label": group,
                "description": GROUP_DESCRIPTIONS.get(group, "Deferred tools."),
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
        description="Deferred group to load, e.g. 'email', 'calendar', 'wiki', 'slack', 'automation', 'loop', 'session', 'app', 'notify', 'directives', 'file', 'fact', 'skill', or 'mcp:obsidian'.",
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
        groups = [g for g in DEFERRED_GROUP_ORDER if g in catalog.by_group]
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
    display_description="Load deferred tools for this chat.",
    description=(
        "Load deferred tool schemas into the current run by exact group or tool name. "
        "Use proactively when the user's request needs a deferred capability listed in the DEFERRED TOOLS prompt section. "
        "Loading tools does not execute them; it only makes them callable on the next model step. "
        "Examples: group='slack', group='email', group='calendar', group='automation', group='session', "
        "group='notify', group='directives', group='file', group='app', group='mcp:obsidian', "
        "or names=['slack_search','slack_thread','slack_file']."
    ),
    input_model=LoadToolsInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL),
    execute=load_tools,
)

tool_search_tool = tool(
    display_name="Search Tools",
    display_description="Find and load deferred tools.",
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
