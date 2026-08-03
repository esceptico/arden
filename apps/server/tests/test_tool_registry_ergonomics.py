from pathlib import Path

import arden.tools.executor as executor_module
from arden.core.agent_types import SPAWN_SURFACE_GUIDANCE
from arden.integrations.core import DIRECTIVES
from arden.integrations.web.tools import WebSearchInput
from arden.tools.app_control import SessionSendMessageInput
from arden.tools.core import ToolResult, tool
from arden.tools.core.scope import tools
from arden.tools.core.types import ToolAction, ToolPolicy, ToolScope
from arden.tools.discover import discover_user_tools
from arden.tools.executor import ToolExecutor
from arden.tools.facts import FactSearchInput


async def _noop(execution, args):
    return ToolResult(content="ok", preview="ok")


def _tool():
    return tool(
        description="Test tool.",
        policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL),
        execute=_noop,
    )


def test_user_discovery_rejects_child_only_reserved_names(tmp_path: Path):
    (tmp_path / "custom.py").write_text(
        "from arden.tools.core import ToolAction, ToolPolicy, ToolResult, ToolScope, tool\n"
        "async def run(execution, args):\n"
        "    return ToolResult(content='ok', preview='ok')\n"
        "candidate = tool(description='custom', policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL), execute=run)\n"
        "tools = {'research_curate': candidate, 'custom_probe': candidate}\n",
        encoding="utf-8",
    )

    discovered = discover_user_tools(tmp_path)

    assert set(discovered) == {"custom_probe"}


def test_user_tools_cannot_shadow_registered_builtins(monkeypatch):
    attempted_shadow = _tool()
    monkeypatch.setattr(executor_module, "discover_user_tools", lambda: {"research": attempted_shadow})

    executor = ToolExecutor()

    assert executor.registry.get("research") is not attempted_shadow
    assert executor.registry.get_source("research") == "_system"


def test_dead_tools_are_not_registered_or_exposed_in_workflow_schema():
    executor = ToolExecutor()
    names = set(executor.registry.tools)
    workflow_schema = executor.registry.get("workflow").to_dict("workflow")

    assert "memory_rebuild" not in names
    assert "save_workflow" not in names
    assert "script" not in workflow_schema["function"]["parameters"]["properties"]


def test_stateful_spawns_do_not_leak_into_read_only_schema():
    read_names = {schema["function"]["name"] for schema in ToolExecutor().get_tools(scope=tools.read)}

    assert "background" not in read_names
    assert "research" not in read_names


def test_all_external_state_changes_require_approval():
    unsafe = [
        name
        for name, registered in ToolExecutor().registry.tools.items()
        if registered.policy.scope is ToolScope.EXTERNAL
        and registered.policy.action is not ToolAction.READ
        and not registered.policy.requires_approval
    ]

    assert unsafe == []


def test_directives_group_exposes_read_before_replace_pair():
    assert set(DIRECTIVES.tools) == {"directives_get", "directives_set"}


def test_shared_spawn_guidance_reaches_each_spawn_surface():
    executor = ToolExecutor()
    for name in ("research", "workflow", "automation_create", "loop_create"):
        assert SPAWN_SURFACE_GUIDANCE in executor.registry.get(name).description


def test_create_automation_description_keeps_scope_and_approval_distinct():
    description = ToolExecutor().registry.get("automation_create").description

    assert "Set all_tools=true when the prompt must act" in description
    assert "auto_approve=true only skips approvals, it never widens" in description


def test_cross_tool_parameter_names_are_canonical():
    assert "session_id" in SessionSendMessageInput.model_fields
    assert "task_id" not in SessionSendMessageInput.model_fields
    assert "limit" in WebSearchInput.model_fields
    assert "num_results" not in WebSearchInput.model_fields
    assert "subject" in FactSearchInput.model_fields
    assert "subjects" not in FactSearchInput.model_fields
