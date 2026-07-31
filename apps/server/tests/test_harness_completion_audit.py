"""Regression checks for cross-cutting tool-harness audit invariants."""

import ast
from pathlib import Path

import pytest

from arden.tools.automation import CreateAutomationInput, UpdateAutomationInput
from arden.tools.deferred import LoadToolsInput
from arden.tools.notify import NotifyInput
from arden.tools.research import ResearchOutlineInput, ResearchVerifyClaimInput
from arden.tools.workflow import WorkflowInput


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
        (UpdateAutomationInput, "channels", 100),
        (UpdateAutomationInput, "contains", 100),
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
    for source_root in (server_root / "arden" / "tools", server_root / "arden" / "integrations"):
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
