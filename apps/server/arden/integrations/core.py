"""Core integrations — builtin tools that ship with arden.

These are not user-facing integrations (no service_fields, no notifier_class,
no build). They exist to keep the tool registration flow uniform: all tools
belong to an Integration, including the ones arden ships out of the box.
"""

from arden.integrations.base import Integration
from arden.memory.facts.maintenance import FACT_MAINTENANCE_REVIEW_TOOL_NAME
from arden.skills.tool import create_skill_tool, use_skill_tool
from arden.tools.app_control import (
    archive_session_tool,
    open_in_app_tool,
    rename_session_tool,
    request_attention_tool,
    send_message_tool,
)
from arden.tools.area import area_page_patch_tool, area_page_read_tool, area_page_write_tool, area_run_automation_tool
from arden.tools.automation import (
    create_automation_tool,
    create_loop_tool,
    delete_automation_tool,
    get_automation_result_tool,
    list_automations_tool,
    loop_done_tool,
    run_automation_tool,
    schedule_wakeup_tool,
    update_automation_tool,
)
from arden.tools.background import background_tool, cancel_agent_tool
from arden.tools.bash import bash_tool
from arden.tools.connections import request_connection_tool
from arden.tools.deferred import load_tools_tool, tool_search_tool
from arden.tools.directives import get_directives_tool, set_directives_tool
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
from arden.tools.wiki import list_wiki_pages_tool, publish_wiki_generated_tool, read_wiki_page_tool, wiki_links_tool
from arden.tools.wiki_maintenance import wiki_maintenance_review_tool
from arden.tools.workflow import workflow_tool
from arden.wiki.constants import WIKI_MAINTENANCE_REVIEW_TOOL_NAME

SYSTEM = Integration(
    id="_system",
    label="System",
    tools={
        "bash": bash_tool,
        "read_file": read_file_tool,
        "list_files": list_files_tool,
        "find_files": find_files_tool,
        "search_text": search_text_tool,
        "write_file": write_file_tool,
        "edit_file": edit_file_tool,
        "current_time": current_time_tool,
        "render_html": render_html_tool,
        "research": research_tool,
        "workflow": workflow_tool,
        "background": background_tool,
        "request_connection": request_connection_tool,
        "load_tools": load_tools_tool,
        "tool_search": tool_search_tool,
    },
)

GOALS = Integration(
    id="_goals",
    label="Goals",
    tools={
        "get_goal": get_goal_tool,
        "complete_goal": complete_goal_tool,
        "block_goal": block_goal_tool,
    },
)

AUTOMATION = Integration(
    id="_automation",
    label="Automation",
    tools={
        "create_automation": create_automation_tool,
        "list_automations": list_automations_tool,
        "update_automation": update_automation_tool,
        "delete_automation": delete_automation_tool,
        "get_automation_result": get_automation_result_tool,
        "run_automation": run_automation_tool,
        "create_loop": create_loop_tool,
        "schedule_wakeup": schedule_wakeup_tool,
        "loop_done": loop_done_tool,
    },
)

BACKGROUND = Integration(
    id="_background",
    label="Background task controls",
    tools={"cancel_agent": cancel_agent_tool},
)

NOTIFICATIONS = Integration(
    id="_notifications",
    label="Notifications",
    tools={"notify": notify_tool},
)

DIRECTIVES = Integration(
    id="_directives",
    label="Directives",
    tools={"get_directives": get_directives_tool, "set_directives": set_directives_tool},
)

TASK_TRACKING = Integration(
    id="_task_tracking",
    label="Task tracking",
    tools={"update_todos": update_todos_tool},
)

SKILLS = Integration(
    id="_skills",
    label="Skills",
    tools={
        "use_skill": use_skill_tool,
        "create_skill": create_skill_tool,
    },
)

SESSIONS = Integration(
    id="_sessions",
    label="Sessions",
    tools={
        "list_recent_sessions": list_recent_sessions_tool,
        "read_session": read_session_tool,
        "search_transcripts": search_transcripts_tool,
        "create_session": create_session_tool,
    },
)

# Separate from SESSIONS so the read tools stay always-loaded: DEFERRED_SOURCES
# keys on the integration id, and `_sessions` must not be in it.
APP_CONTROL = Integration(
    id="_app_control",
    label="App control",
    tools={
        "send_message": send_message_tool,
        "rename_session": rename_session_tool,
        "archive_session": archive_session_tool,
        "request_attention": request_attention_tool,
        "open_in_app": open_in_app_tool,
    },
)

AREA = Integration(
    id="_area",
    label="Area",
    tools={
        "area_page_read": area_page_read_tool,
        "area_page_patch": area_page_patch_tool,
        "area_page_write": area_page_write_tool,
        "area_run_automation": area_run_automation_tool,
    },
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
        "search_facts": search_facts_tool,
        "get_fact": get_fact_tool,
        "get_fact_history": get_fact_history_tool,
        "get_due_fact_reviews": get_due_fact_reviews_tool,
        "plan_fact_changes": plan_fact_changes_tool,
        "commit_fact_changes": commit_fact_changes_tool,
    },
)

WIKI = Integration(
    id="_wiki",
    label="Wiki",
    tools={
        "list_wiki_pages": list_wiki_pages_tool,
        "read_wiki_page": read_wiki_page_tool,
        "wiki_links": wiki_links_tool,
        "publish_wiki_generated": publish_wiki_generated_tool,
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
    FACT_MAINTENANCE,
    WIKI_MAINTENANCE,
    FACTS,
    WIKI,
]
