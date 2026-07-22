from pydantic import BaseModel

from arden.agent import ToolResult
from arden.tools.core.base import Tool
from arden.tools.core.registry import ToolRegistry
from arden.tools.core.types import ToolAction, ToolPolicy, ToolScope


class _EmptyIn(BaseModel):
    pass


def _read_tool() -> Tool:
    class _ReadTool(Tool):
        description = "reads"
        policy = ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL)
        input_model = _EmptyIn

        async def execute(self, execution, **kwargs):  # type: ignore[override]
            return ToolResult(content="ok", preview="ok")

    return _ReadTool()


def _write_tool() -> Tool:
    class _WriteTool(Tool):
        description = "writes"
        policy = ToolPolicy(action=ToolAction.WRITE, scope=ToolScope.INTERNAL)
        input_model = _EmptyIn

        async def execute(self, execution, **kwargs):  # type: ignore[override]
            return ToolResult(content="ok", preview="ok")

    return _WriteTool()


def _execute_tool() -> Tool:
    class _ExecuteTool(Tool):
        description = "executes"
        policy = ToolPolicy(action=ToolAction.EXECUTE, scope=ToolScope.INTERNAL)
        input_model = _EmptyIn

        async def execute(self, execution, **kwargs):  # type: ignore[override]
            return ToolResult(content="ok", preview="ok")

    return _ExecuteTool()


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register("read_thing", _read_tool())
    reg.register("write_memory", _write_tool())
    reg.register("bash", _execute_tool())
    return reg


def test_read_only_excludes_write_and_execute_tools_by_default():
    reg = _registry()
    names = {s["function"]["name"] for s in reg.get_schemas(read_only=True)}
    assert names == {"read_thing"}


def test_extra_names_admits_named_write_tool_without_widening_to_other_write_or_execute_tools():
    reg = _registry()
    names = {s["function"]["name"] for s in reg.get_schemas(read_only=True, extra_names=frozenset({"write_memory"}))}
    assert names == {"read_thing", "write_memory"}
    assert "bash" not in names


def test_extra_names_is_a_no_op_when_no_filter_is_active():
    reg = _registry()
    names = {s["function"]["name"] for s in reg.get_schemas(extra_names=frozenset({"write_memory"}))}
    assert names == {"read_thing", "write_memory", "bash"}
