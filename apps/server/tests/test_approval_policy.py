from arden.tools.core.base import Tool
from arden.tools.core.registry import ToolRegistry
from arden.tools.core.types import ApprovalMode, ToolAction, ToolOverrideDecision, ToolPolicy, ToolScope


class DummyTool(Tool):
    description = "Dummy"
    policy = ToolPolicy(action=ToolAction.WRITE, scope=ToolScope.INTERNAL, requires_approval=True)

    async def execute(self, execution, **kwargs):
        raise NotImplementedError


def test_requires_approval_true_maps_to_always():
    policy = ToolPolicy(action=ToolAction.WRITE, scope=ToolScope.INTERNAL, requires_approval=True)

    assert policy.approval_mode == ApprovalMode.ALWAYS


def test_requires_approval_false_maps_to_never():
    policy = ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL, requires_approval=False)

    assert policy.approval_mode == ApprovalMode.NEVER


def test_registry_override_updates_effective_approval_mode():
    registry = ToolRegistry(tool_overrides={"dummy": ToolOverrideDecision.APPROVE})
    registry.register("dummy", DummyTool())

    metadata = registry.get_metadata()[0]

    assert metadata["policy"]["requires_approval"] is False
    assert metadata["policy"]["approval_mode"] == "never"


def test_metadata_reports_approval_mode():
    registry = ToolRegistry()
    registry.register("dummy", DummyTool())

    metadata = registry.get_metadata()[0]

    assert metadata["policy"]["approval_mode"] == "always"


def test_unimplemented_approval_modes_are_rejected():
    import pytest

    with pytest.raises(ValueError):
        ToolPolicy(action=ToolAction.WRITE, scope=ToolScope.INTERNAL, approval_mode="once")
