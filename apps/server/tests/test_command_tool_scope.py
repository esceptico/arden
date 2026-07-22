from ntrp.tools.core.base import Tool
from ntrp.tools.core.registry import ToolRegistry
from ntrp.tools.core.types import ToolAction, ToolPolicy, ToolScope
from ntrp.tools.deferred import tool_schema_names


class _Tool(Tool):
    description = "test tool"

    def __init__(self, action: ToolAction = ToolAction.READ):
        self.policy = ToolPolicy(action=action, scope=ToolScope.INTERNAL)

    async def execute(self, execution, **kwargs):  # pragma: no cover
        raise NotImplementedError

def test_command_filter_is_explicit_and_preserves_outer_scope():
    registry = ToolRegistry()
    registry.register("list_automations", _Tool(), command_eligible=True)
    registry.register("bash", _Tool(ToolAction.EXECUTE))

    schemas = registry.get_schemas(
        command_eligible=True,
        scope=("list_automations", "bash"),
    )

    assert tool_schema_names(schemas) == {"list_automations"}


def test_command_metadata_reports_eligibility():
    registry = ToolRegistry()
    registry.register("list_automations", _Tool(), command_eligible=True)

    assert registry.get_metadata()[0]["command_eligible"] is True


def test_registry_copy_preserves_command_eligibility():
    registry = ToolRegistry()
    registry.register("list_automations", _Tool(), command_eligible=True)

    copied = registry.copy_with({"temporary": _Tool()})

    assert tool_schema_names(copied.get_schemas(command_eligible=True)) == {
        "list_automations"
    }
