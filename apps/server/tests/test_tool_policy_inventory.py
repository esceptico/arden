from arden.integrations import ALL_INTEGRATIONS
from arden.tools.core.registry import tool_changes_state
from arden.tools.research import RESEARCH_AGENT_TOOLS

type RiskHints = tuple[bool, bool, bool]


_EXPECTED_BY_RISK: dict[RiskHints, set[str]] = {
    (True, True, False): {
        "app_followup_task",
        "area_run_automation",
        "automation_run",
        "bash",
        "session_send_message",
        "workflow",
    },
    (False, True, False): {
        "connection_request",
        "notify",
        "research",
    },
    (True, False, True): {
        "agent_cancel",
        "area_page_patch",
        "area_page_write",
        "area_submit_report",
        "automation_delete",
        "directives_set",
        "fact_commit_changes",
        "file_edit",
        "file_write",
        "goal_complete",
        "loop_done",
        "research_outline",
        "research_track_source",
        "session_archive",
        "session_rename",
        "todo_update",
        "wiki_archive_page",
        "wiki_edit_page",
        "wiki_move_page",
        "wiki_patch_page",
        "wiki_publish_generated",
        "write_research_artifact",
    },
    (True, False, False): {
        "app_request_attention",
        "automation_update",
        "fact_capture_review",
        "fact_maintenance_review",
        "goal_block",
        "loop_schedule_wakeup",
        "wiki_maintenance_review",
    },
    (False, False, True): {
        "automation_create",
        "research_cover",
        "skill_create",
        "wiki_create_page",
    },
    (False, False, False): {
        "append_research_artifact",
        "app_open",
        "fact_plan_changes",
        "loop_create",
        "research_curate",
        "research_question",
        "research_track_search",
        "research_verify_claim",
        "session_create",
    },
    (False, True, True): {
        "calendar_create_event",
        "drive_append_sheet_rows",
        "drive_create_doc",
        "drive_create_sheet",
        "email_reply",
        "email_send",
        "slack_post_blocks",
        "slack_post_message",
    },
    (True, True, True): {
        "calendar_delete_event",
        "calendar_edit_event",
        "drive_edit_doc",
        "drive_update_sheet",
    },
}


def _expected_risks() -> dict[str, RiskHints]:
    return {name: risk for risk, names in _EXPECTED_BY_RISK.items() for name in names}


def _repo_owned_tools():
    tools = {}
    for integration in ALL_INTEGRATIONS:
        overlap = tools.keys() & integration.tools.keys()
        assert not overlap, f"duplicate builtin tools: {sorted(overlap)}"
        tools.update(integration.tools)
    overlap = tools.keys() & RESEARCH_AGENT_TOOLS.keys()
    assert not overlap, f"research tools shadow builtins: {sorted(overlap)}"
    tools.update(RESEARCH_AGENT_TOOLS)
    return tools


def test_builtin_mutating_tool_risk_inventory_is_complete_and_exact():
    actual = {
        name: (tool.policy.destructive, tool.policy.open_world, tool.policy.idempotent)
        for name, tool in _repo_owned_tools().items()
        if tool_changes_state(tool)
    }

    assert actual == _expected_risks()
