"""Regression checks for cross-cutting tool-harness audit invariants."""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

from ntrp.memory.reconciler import RecordOperation
from ntrp.tools.automation import CreateAutomationInput, UpdateAutomationInput
from ntrp.tools.deferred import LoadToolsInput
from ntrp.tools.memory import MEMORY_RECONCILER_SERVICE, MEMORY_RECORDS_SERVICE, RememberInput, remember
from ntrp.tools.notify import NotifyInput
from ntrp.tools.research import ResearchOutlineInput, ResearchVerifyClaimInput
from ntrp.tools.workflow import WorkflowInput


def _schema_limit(model, field: str, keyword: str) -> int | None:
    def find(value):
        if isinstance(value, dict):
            if keyword in value:
                return value[keyword]
            for child in value.values():
                if (found := find(child)) is not None:
                    return found
        elif isinstance(value, list):
            for child in value:
                if (found := find(child)) is not None:
                    return found
        return None

    return find(model.model_json_schema()["properties"][field])


@pytest.mark.parametrize(
    ("model", "field", "limit"),
    [
        (CreateAutomationInput, "channels", 100),
        (CreateAutomationInput, "contains", 100),
        (CreateAutomationInput, "tool_scope", 200),
        (UpdateAutomationInput, "channels", 100),
        (UpdateAutomationInput, "contains", 100),
        (UpdateAutomationInput, "tool_scope", 200),
        (ResearchOutlineInput, "sections", 50),
        (ResearchVerifyClaimInput, "sources", 50),
        (NotifyInput, "names", 50),
        (WorkflowInput, "phases", 50),
        (WorkflowInput, "args", 100),
        (LoadToolsInput, "names", 100),
    ],
)
def test_agent_controlled_collections_are_bounded(model, field, limit):
    keyword = "maxProperties" if field == "args" else "maxItems"
    assert _schema_limit(model, field, keyword) == limit


def test_production_tools_do_not_construct_string_only_errors():
    server_root = Path(__file__).parents[1]
    violations: list[str] = []
    for source_root in (server_root / "ntrp" / "tools", server_root / "ntrp" / "integrations"):
        for path in source_root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
                if name != "ToolResult":
                    continue
                if any(
                    keyword.arg == "is_error"
                    and isinstance(keyword.value, ast.Constant)
                    and keyword.value.value is True
                    for keyword in node.keywords
                ):
                    violations.append(f"{path.relative_to(server_root)}:{node.lineno}")
    assert violations == []


@pytest.mark.asyncio
async def test_memory_clarification_is_a_non_error_control_flow_result():
    class EmptyStore:
        async def search(self, *args, **kwargs):
            return []

    class AskReconciler:
        async def reconcile_direct_memory(self, **kwargs):
            return [RecordOperation.ask("Which tea should be remembered?")]

    execution = SimpleNamespace(
        tool_id="call-1",
        tool_name="remember",
        ctx=SimpleNamespace(
            services={MEMORY_RECORDS_SERVICE: EmptyStore(), MEMORY_RECONCILER_SERVICE: AskReconciler()},
            session_id="session-1",
            area=None,
        ),
    )

    result = await remember(execution, RememberInput(text="Remember the tea", kind="fact"))

    assert not result.is_error
    assert result.preview == "Clarification required"
    assert result.data == {"clarification_required": True}
