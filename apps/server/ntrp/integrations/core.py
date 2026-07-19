"""Core integrations — builtin tools that ship with ntrp.

These are not user-facing integrations (no service_fields, no notifier_class,
no build). They exist to keep the tool registration flow uniform: all tools
belong to an Integration, including the ones ntrp ships out of the box.
"""

from ntrp.integrations.base import Integration
from ntrp.skills.tool import create_skill_tool, use_skill_tool
from ntrp.tools.area import area_page_patch_tool, area_page_read_tool, area_page_write_tool, area_run_automation_tool
from ntrp.tools.automation import (
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
from ntrp.tools.background import (
    background_tool,
    cancel_background_task_tool,
    get_background_result_tool,
    list_background_tasks_tool,
    send_to_agent_tool,
)
from ntrp.tools.bash import bash_tool
from ntrp.tools.connections import request_connection_tool
from ntrp.tools.deferred import load_tools_tool, tool_search_tool
from ntrp.tools.directives import set_directives_tool
from ntrp.tools.files import (
    edit_file_tool,
    find_files_tool,
    list_files_tool,
    read_file_tool,
    search_text_tool,
    write_file_tool,
)
from ntrp.tools.goals import block_goal_tool, complete_goal_tool, get_goal_tool
from ntrp.tools.memory import (
    forget_tool,
    memory_patch_tool,
    memory_read_tool,
    memory_rebuild_tool,
    memory_search_tool,
    memory_tree_tool,
    memory_write_tool,
    recall_tool,
    remember_tool,
)
from ntrp.tools.notify import notify_tool
from ntrp.tools.render_html import render_html_tool
from ntrp.tools.research import research_tool
from ntrp.tools.sessions import (
    create_session_tool,
    list_recent_sessions_tool,
    read_session_tool,
    search_transcripts_tool,
)
from ntrp.tools.time import current_time_tool
from ntrp.tools.todos import update_todos_tool
from ntrp.tools.workflow import save_workflow_tool, workflow_tool

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
        "save_workflow": save_workflow_tool,
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
    tools={
        "cancel_background_task": cancel_background_task_tool,
        "get_background_result": get_background_result_tool,
        "list_background_tasks": list_background_tasks_tool,
        "send_to_agent": send_to_agent_tool,
    },
)

NOTIFICATIONS = Integration(
    id="_notifications",
    label="Notifications",
    tools={"notify": notify_tool},
)

DIRECTIVES = Integration(
    id="_directives",
    label="Directives",
    tools={"set_directives": set_directives_tool},
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

# Memory record and artifact tools stay hidden until the knowledge runtime wires
# the memory_records service — each tool's permission — so they never appear when
# memory is off.
# Transcript recall stays in the hybrid search_transcripts tool; recall here is
# the record-layer lookup; memory_* filesystem tools only inspect/patch the
# artifact projection.
MEMORY = Integration(
    id="_memory",
    label="Memory",
    tools={
        "remember": remember_tool,
        "forget": forget_tool,
        "recall": recall_tool,
        "memory_tree": memory_tree_tool,
        "memory_read": memory_read_tool,
        "memory_search": memory_search_tool,
        "memory_patch": memory_patch_tool,
        "memory_write": memory_write_tool,
        "memory_rebuild": memory_rebuild_tool,
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
    AREA,
    MEMORY,
]
