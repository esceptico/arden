"""Core integrations — builtin tools that ship with arden.

These are not user-facing integrations (no service_fields, no notifier_class,
no build). They exist to keep the tool registration flow uniform: all tools
belong to an Integration, including the ones arden ships out of the box.
"""

from arden.integrations.base import Integration
from arden.memory.facts.capture import FACT_CAPTURE_REVIEW_TOOL_NAME
from arden.memory.facts.maintenance import FACT_MAINTENANCE_REVIEW_TOOL_NAME
from arden.skills.tool import skill_create_tool, skill_use_tool
from arden.tools.app_control import (
    app_followup_task_tool,
    app_open_tool,
    app_request_attention_tool,
    area_list_tool,
    session_archive_tool,
    session_rename_tool,
    session_send_message_tool,
)
from arden.tools.area import (
    area_page_patch_tool,
    area_page_read_tool,
    area_page_write_tool,
    area_run_automation_tool,
    area_submit_report_tool,
)
from arden.tools.automation import (
    automation_create_tool,
    automation_delete_tool,
    automation_list_runs_tool,
    automation_list_tool,
    automation_result_tool,
    automation_run_tool,
    automation_update_tool,
    loop_create_tool,
    loop_done_tool,
    loop_schedule_wakeup_tool,
)
from arden.tools.background import agent_cancel_tool, agent_result_read_tool
from arden.tools.bash import bash_tool
from arden.tools.connections import connection_request_tool
from arden.tools.deferred import load_tools_tool, tool_search_tool
from arden.tools.directives import directives_get_tool, directives_set_tool
from arden.tools.fact_capture import fact_capture_review_tool
from arden.tools.fact_maintenance import fact_maintenance_review_tool
from arden.tools.facts import (
    fact_commit_changes_tool,
    fact_due_reviews_tool,
    fact_get_tool,
    fact_history_tool,
    fact_plan_changes_tool,
    fact_search_tool,
)
from arden.tools.files import (
    file_edit_tool,
    file_find_tool,
    file_list_tool,
    file_read_tool,
    file_search_text_tool,
    file_write_tool,
)
from arden.tools.goals import goal_block_tool, goal_complete_tool, goal_get_tool
from arden.tools.notify import notify_tool
from arden.tools.render_html import render_html_tool
from arden.tools.research import research_tool
from arden.tools.sessions import (
    session_create_tool,
    session_list_tool,
    session_read_tool,
    session_search_transcripts_tool,
)
from arden.tools.time import current_time_tool
from arden.tools.todos import todo_update_tool
from arden.tools.wiki import (
    wiki_archive_page_tool,
    wiki_create_page_tool,
    wiki_edit_page_tool,
    wiki_links_tool,
    wiki_list_changes_tool,
    wiki_list_pages_tool,
    wiki_move_page_tool,
    wiki_patch_page_tool,
    wiki_publish_generated_tool,
    wiki_read_page_tool,
)
from arden.tools.wiki_maintenance import wiki_maintenance_review_tool
from arden.tools.workflow import workflow_tool
from arden.wiki.constants import WIKI_MAINTENANCE_REVIEW_TOOL_NAME

SYSTEM = Integration(
    id="_system",
    label="System",
    tools={
        "bash": bash_tool,
        "file_read": file_read_tool,
        "file_list": file_list_tool,
        "file_find": file_find_tool,
        "file_search_text": file_search_text_tool,
        "file_write": file_write_tool,
        "file_edit": file_edit_tool,
        "current_time": current_time_tool,
        "render_html": render_html_tool,
        "research": research_tool,
        "workflow": workflow_tool,
        "connection_request": connection_request_tool,
        "load_tools": load_tools_tool,
        "tool_search": tool_search_tool,
    },
)

GOALS = Integration(
    id="_goals",
    label="Goals",
    tools={
        "goal_get": goal_get_tool,
        "goal_complete": goal_complete_tool,
        "goal_block": goal_block_tool,
    },
)

AUTOMATION = Integration(
    id="_automation",
    label="Automation",
    tools={
        "automation_create": automation_create_tool,
        "automation_list": automation_list_tool,
        "automation_list_runs": automation_list_runs_tool,
        "automation_update": automation_update_tool,
        "automation_delete": automation_delete_tool,
        "automation_result": automation_result_tool,
        "automation_run": automation_run_tool,
        "loop_create": loop_create_tool,
        "loop_schedule_wakeup": loop_schedule_wakeup_tool,
        "loop_done": loop_done_tool,
    },
)

BACKGROUND = Integration(
    id="_background",
    label="Background task controls",
    tools={"agent_cancel": agent_cancel_tool, "agent_result_read": agent_result_read_tool},
)

NOTIFICATIONS = Integration(
    id="_notifications",
    label="Notifications",
    tools={"notify": notify_tool},
)

DIRECTIVES = Integration(
    id="_directives",
    label="Directives",
    tools={"directives_get": directives_get_tool, "directives_set": directives_set_tool},
)

TASK_TRACKING = Integration(
    id="_task_tracking",
    label="Task tracking",
    tools={"todo_update": todo_update_tool},
)

SKILLS = Integration(
    id="_skills",
    label="Skills",
    tools={
        "skill_use": skill_use_tool,
        "skill_create": skill_create_tool,
    },
)

SESSIONS = Integration(
    id="_sessions",
    label="Sessions",
    tools={
        "session_list": session_list_tool,
        "session_read": session_read_tool,
        "session_search_transcripts": session_search_transcripts_tool,
        "session_create": session_create_tool,
    },
)

APP_CONTROL = Integration(
    id="_app_control",
    label="App control",
    tools={
        "session_send_message": session_send_message_tool,
        "app_followup_task": app_followup_task_tool,
        "session_rename": session_rename_tool,
        "session_archive": session_archive_tool,
        "app_request_attention": app_request_attention_tool,
        "app_open": app_open_tool,
    },
)

AREA = Integration(
    id="_area",
    label="Area",
    tools={
        "area_list": area_list_tool,
        "area_submit_report": area_submit_report_tool,
        "area_page_read": area_page_read_tool,
        "area_page_patch": area_page_patch_tool,
        "area_page_write": area_page_write_tool,
        "area_run_automation": area_run_automation_tool,
    },
)

FACT_CAPTURE = Integration(
    id="_fact_capture",
    label="Fact capture",
    tools={FACT_CAPTURE_REVIEW_TOOL_NAME: fact_capture_review_tool},
)

FACT_MAINTENANCE = Integration(
    id="_fact_maintenance",
    label="Fact maintenance",
    tools={FACT_MAINTENANCE_REVIEW_TOOL_NAME: fact_maintenance_review_tool},
)

WIKI_MAINTENANCE = Integration(
    id="_wiki_maintenance",
    label="Wiki maintenance",
    tools={WIKI_MAINTENANCE_REVIEW_TOOL_NAME: wiki_maintenance_review_tool},
)

FACTS = Integration(
    id="_facts",
    label="Facts",
    tools={
        "fact_search": fact_search_tool,
        "fact_get": fact_get_tool,
        "fact_history": fact_history_tool,
        "fact_due_reviews": fact_due_reviews_tool,
        "fact_plan_changes": fact_plan_changes_tool,
        "fact_commit_changes": fact_commit_changes_tool,
    },
)

WIKI = Integration(
    id="_wiki",
    label="Wiki",
    tools={
        "wiki_list_pages": wiki_list_pages_tool,
        "wiki_list_changes": wiki_list_changes_tool,
        "wiki_read_page": wiki_read_page_tool,
        "wiki_links": wiki_links_tool,
        "wiki_create_page": wiki_create_page_tool,
        "wiki_edit_page": wiki_edit_page_tool,
        "wiki_patch_page": wiki_patch_page_tool,
        "wiki_archive_page": wiki_archive_page_tool,
        "wiki_move_page": wiki_move_page_tool,
        "wiki_publish_generated": wiki_publish_generated_tool,
    },
)

CORE_INTEGRATIONS = [
    SYSTEM,
    GOALS,
    AUTOMATION,
    BACKGROUND,
    NOTIFICATIONS,
    DIRECTIVES,
    TASK_TRACKING,
    SKILLS,
    SESSIONS,
    APP_CONTROL,
    AREA,
    FACT_CAPTURE,
    FACT_MAINTENANCE,
    WIKI_MAINTENANCE,
    FACTS,
    WIKI,
]
