"""Core integrations — builtin tools that ship with arden.

These are not user-facing integrations (no service_fields, no notifier_class,
no build). They exist to keep the tool registration flow uniform: all tools
belong to an Integration, including the ones arden ships out of the box.
"""

from arden.integrations.base import Integration
from arden.memory.facts.capture import FACT_CAPTURE_REVIEW_TOOL_NAME
from arden.memory.facts.maintenance import FACT_MAINTENANCE_REVIEW_TOOL_NAME
from arden.skills.tool import create_skill_tool, use_skill_tool
from arden.tools.app_control import (
    archive_session_tool,
    followup_task_tool,
    open_in_app_tool,
    rename_session_tool,
    request_attention_tool,
    send_message_tool,
)
from arden.tools.area import (
    area_page_patch_tool,
    area_page_read_tool,
    area_page_write_tool,
    area_run_automation_tool,
    submit_area_report_tool,
)
from arden.tools.automation import (
    create_automation_tool,
    create_loop_tool,
    delete_automation_tool,
    get_automation_result_tool,
    list_automation_runs_tool,
    list_automations_tool,
    loop_done_tool,
    run_automation_tool,
    schedule_wakeup_tool,
    update_automation_tool,
)
from arden.tools.background import cancel_agent_tool
from arden.tools.bash import bash_tool
from arden.tools.connections import request_connection_tool
from arden.tools.deferred import load_tools_tool, tool_search_tool
from arden.tools.directives import get_directives_tool, set_directives_tool
from arden.tools.fact_capture import fact_capture_review_tool
from arden.tools.fact_maintenance import fact_maintenance_review_tool
from arden.tools.facts import (
    commit_fact_changes_tool,
    get_due_fact_reviews_tool,
    get_fact_history_tool,
    get_fact_tool,
    plan_fact_changes_tool,
    search_facts_tool,
)
from arden.tools.files import (
    edit_file_tool,
    find_files_tool,
    list_files_tool,
    read_file_tool,
    search_text_tool,
    write_file_tool,
)
from arden.tools.goals import block_goal_tool, complete_goal_tool, get_goal_tool
from arden.tools.notify import notify_tool
from arden.tools.render_html import render_html_tool
from arden.tools.research import research_tool
from arden.tools.sessions import (
    create_session_tool,
    list_recent_sessions_tool,
    read_session_tool,
    search_transcripts_tool,
)
from arden.tools.time import current_time_tool
from arden.tools.todos import update_todos_tool
from arden.tools.wiki import (
    archive_wiki_page_tool,
    create_wiki_page_tool,
    edit_wiki_page_tool,
    list_wiki_changes_tool,
    list_wiki_pages_tool,
    move_wiki_page_tool,
    publish_wiki_generated_tool,
    read_wiki_page_tool,
    wiki_links_tool,
)
from arden.tools.wiki_maintenance import wiki_maintenance_review_tool
from arden.tools.workflow import workflow_tool
from arden.wiki.constants import WIKI_MAINTENANCE_REVIEW_TOOL_NAME

SYSTEM = Integration(
    id="_system",
    label="System",
    tools={
        "bash": bash_tool,
        "file_read": read_file_tool,
        "file_list": list_files_tool,
        "file_find": find_files_tool,
        "file_search_text": search_text_tool,
        "file_write": write_file_tool,
        "file_edit": edit_file_tool,
        "current_time": current_time_tool,
        "render_html": render_html_tool,
        "research": research_tool,
        "workflow": workflow_tool,
        "connection_request": request_connection_tool,
        "load_tools": load_tools_tool,
        "tool_search": tool_search_tool,
    },
)

GOALS = Integration(
    id="_goals",
    label="Goals",
    tools={
        "goal_get": get_goal_tool,
        "goal_complete": complete_goal_tool,
        "goal_block": block_goal_tool,
    },
)

AUTOMATION = Integration(
    id="_automation",
    label="Automation",
    tools={
        "automation_create": create_automation_tool,
        "automation_list": list_automations_tool,
        "automation_list_runs": list_automation_runs_tool,
        "automation_update": update_automation_tool,
        "automation_delete": delete_automation_tool,
        "automation_result": get_automation_result_tool,
        "automation_run": run_automation_tool,
        "loop_create": create_loop_tool,
        "loop_schedule_wakeup": schedule_wakeup_tool,
        "loop_done": loop_done_tool,
    },
)

BACKGROUND = Integration(
    id="_background",
    label="Background task controls",
    tools={"agent_cancel": cancel_agent_tool},
)

NOTIFICATIONS = Integration(
    id="_notifications",
    label="Notifications",
    tools={"notify": notify_tool},
)

DIRECTIVES = Integration(
    id="_directives",
    label="Directives",
    tools={"directives_get": get_directives_tool, "directives_set": set_directives_tool},
)

TASK_TRACKING = Integration(
    id="_task_tracking",
    label="Task tracking",
    tools={"todo_update": update_todos_tool},
)

SKILLS = Integration(
    id="_skills",
    label="Skills",
    tools={
        "skill_use": use_skill_tool,
        "skill_create": create_skill_tool,
    },
)

SESSIONS = Integration(
    id="_sessions",
    label="Sessions",
    tools={
        "session_list": list_recent_sessions_tool,
        "session_read": read_session_tool,
        "session_search_transcripts": search_transcripts_tool,
        "session_create": create_session_tool,
    },
)

APP_CONTROL = Integration(
    id="_app_control",
    label="App control",
    tools={
        "session_send_message": send_message_tool,
        "app_followup_task": followup_task_tool,
        "session_rename": rename_session_tool,
        "session_archive": archive_session_tool,
        "app_request_attention": request_attention_tool,
        "app_open": open_in_app_tool,
    },
)

AREA = Integration(
    id="_area",
    label="Area",
    tools={
        "area_submit_report": submit_area_report_tool,
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
        "fact_search": search_facts_tool,
        "fact_get": get_fact_tool,
        "fact_history": get_fact_history_tool,
        "fact_due_reviews": get_due_fact_reviews_tool,
        "fact_plan_changes": plan_fact_changes_tool,
        "fact_commit_changes": commit_fact_changes_tool,
    },
)

WIKI = Integration(
    id="_wiki",
    label="Wiki",
    tools={
        "wiki_list_pages": list_wiki_pages_tool,
        "wiki_list_changes": list_wiki_changes_tool,
        "wiki_read_page": read_wiki_page_tool,
        "wiki_links": wiki_links_tool,
        "wiki_create_page": create_wiki_page_tool,
        "wiki_edit_page": edit_wiki_page_tool,
        "wiki_archive_page": archive_wiki_page_tool,
        "wiki_move_page": move_wiki_page_tool,
        "wiki_publish_generated": publish_wiki_generated_tool,
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
