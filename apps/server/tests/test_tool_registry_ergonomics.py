from pathlib import Path

import arden.tools.executor as executor_module
from arden.core.agent_types import SPAWN_SURFACE_GUIDANCE
from arden.integrations.core import DIRECTIVES
from arden.integrations.web.tools import WebSearchInput
from arden.tools.background import SendToAgentInput
from arden.tools.core import ToolResult, tool
from arden.tools.core.types import ToolAction, ToolPolicy, ToolScope
from arden.tools.discover import discover_user_tools
from arden.tools.executor import ToolExecutor
from arden.tools.memory import RecallInput


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
    monkeypatch.setattr(executor_module, "discover_user_tools", lambda: {"background": attempted_shadow})

    executor = ToolExecutor()

    assert executor.registry.get("background") is not attempted_shadow
    assert executor.registry.get_source("background") == "_system"


def test_dead_tools_are_not_registered_or_exposed_in_workflow_schema():
    executor = ToolExecutor()
    names = set(executor.registry.tools)
    workflow_schema = executor.registry.get("workflow").to_dict("workflow")

    assert "memory_rebuild" not in names
    assert "save_workflow" not in names
    assert "script" not in workflow_schema["function"]["parameters"]["properties"]


def test_stateful_spawns_do_not_leak_into_read_only_schema():
    read_names = {schema["function"]["name"] for schema in ToolExecutor().get_tools(read_only=True)}

    assert "background" not in read_names
    assert "research" not in read_names


def test_directives_group_exposes_read_before_replace_pair():
    assert set(DIRECTIVES.tools) == {"get_directives", "set_directives"}


def test_shared_spawn_guidance_reaches_each_spawn_surface():
    executor = ToolExecutor()
    for name in ("research", "background", "workflow", "create_automation", "create_loop"):
        assert SPAWN_SURFACE_GUIDANCE in executor.registry.get(name).description


def test_cross_tool_parameter_names_are_canonical():
    assert "task_id" in SendToAgentInput.model_fields
    assert "agent_id" not in SendToAgentInput.model_fields
    assert "limit" in WebSearchInput.model_fields
    assert "num_results" not in WebSearchInput.model_fields
    assert "kind" in RecallInput.model_fields
    assert "kinds" not in RecallInput.model_fields
