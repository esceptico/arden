"""Namespace invariants: tool names carry their namespace as a prefix, deferred
grouping derives from that prefix, and the always-on surface stays deliberate."""

from arden.tools.deferred import DEFERRED_GROUP_ORDER, GROUP_DESCRIPTIONS, deferred_group, is_deferred_tool
from arden.tools.executor import ToolExecutor

# Namespaced prefixes; single-word tools (bash, notify, research, workflow, ...)
# are their own namespace.
NAMESPACE_PREFIXES = (
    "session_",
    "app_",
    "automation_",
    "loop_",
    "agent_",
    "directives_",
    "wiki_",
    "fact_",
    "skill_",
    "goal_",
    "todo_",
    "file_",
    "email_",
    "calendar_",
    "drive_",
    "slack_",
    "area_",
    "web_",
    "connection_",
    "tool_",
    "load_",
)

# The deliberate always-on surface. Everything else must be deferred (or
# capability-gated to a special run, which these review/area/goal tools are).
ALWAYS_ON = {
    # loop plumbing
    "load_tools",
    "tool_search",
    "todo_update",
    "current_time",
    # local reads
    "bash",
    "file_read",
    "file_list",
    "file_find",
    "file_search_text",
    # web
    "web_search",
    "web_fetch",
    # memory recall
    "fact_search",
    "fact_get",
    # spawn surfaces
    "research",
    "workflow",
    "skill_use",
    # chat output & connections
    "render_html",
    "connection_request",
    # capability-gated special-run tools (invisible in a normal chat)
    "area_page_read",
    "area_page_write",
    "area_page_patch",
    "area_run_automation",
    "area_submit_report",
    "goal_get",
    "goal_complete",
    "goal_block",
    "fact_capture_review",
    "fact_maintenance_review",
    "wiki_maintenance_review",
}


def test_every_tool_name_is_namespaced_or_a_known_single():
    registry = ToolExecutor().registry
    singles = {"bash", "current_time", "render_html", "research", "workflow", "notify"}
    for name in registry.tools:
        assert name.startswith(NAMESPACE_PREFIXES) or name in singles, (
            f"tool {name!r} matches no namespace prefix; extend the namespace table deliberately"
        )


def test_always_on_surface_is_exactly_the_declared_set():
    registry = ToolExecutor().registry
    always = {name for name in registry.tools if not is_deferred_tool(name, registry)}
    assert always == ALWAYS_ON


def test_every_deferred_group_has_a_description():
    registry = ToolExecutor().registry
    groups = {deferred_group(name) for name in registry.tools if is_deferred_tool(name, registry)}
    missing = groups - set(GROUP_DESCRIPTIONS)
    assert not missing, f"deferred groups without a description: {sorted(missing)}"
    assert set(DEFERRED_GROUP_ORDER) == set(GROUP_DESCRIPTIONS) - {"mcp"}
