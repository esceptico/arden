from datetime import UTC, datetime
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from mcp.types import Tool as McpTool

from arden.context.models import SessionState
from arden.mcp.tool import MCPTool
from arden.tools.core.context import IOBridge, RunContext, ToolContext, ToolExecution
from arden.tools.core.registry import ToolRegistry
from arden.tools.core.types import ToolAction, ToolPolicy, ToolScope


class FakeMCPSession:
    def __init__(self, result: CallToolResult):
        self.result = result
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> CallToolResult:
        self.calls.append((tool_name, arguments))
        return self.result


@pytest.mark.asyncio
async def test_mcp_tool_executes_remote_tool_and_adapts_result():
    mcp_tool = McpTool(
        name="search",
        description="Search notes",
        inputSchema={"type": "object", "properties": {"query": {"type": "string"}}},
    )
    session = FakeMCPSession(
        CallToolResult(
            content=[TextContent(type="text", text="Found 1 note")],
            structuredContent={
                "results": [
                    {
                        "id": "Note.md",
                        "title": "Note",
                        "url": "https://notes.example.test/Note.md",
                    }
                ]
            },
        )
    )
    tool = MCPTool("obsidian", mcp_tool, session)
    execution = ToolExecution(
        tool_id="call-1",
        tool_name=tool.name,
        ctx=ToolContext(
            session_state=SessionState(session_id="session-1", started_at=datetime(2026, 4, 30, tzinfo=UTC)),
            registry=ToolRegistry(),
            run=RunContext(run_id="run-1"),
            io=IOBridge(),
        ),
    )

    result = await tool.execute(execution, query="notes")

    assert session.calls == [("search", {"query": "notes"})]
    assert result.content == "Found 1 note"
    assert result.data == {
        "structuredContent": {
            "results": [
                {
                    "id": "Note.md",
                    "title": "Note",
                    "url": "https://notes.example.test/Note.md",
                }
            ]
        }
    }
    assert [ref.to_dict() for ref in result.source_refs] == [
        {
            "provider": "obsidian",
            "kind": "search_result",
            "ref": "Note.md",
            "title": "Note",
            "url": "https://notes.example.test/Note.md",
        }
    ]


def test_mcp_tool_uses_explicit_policy_override():
    mcp_tool = McpTool(name="search", description="Search notes", inputSchema={"type": "object"})
    session = FakeMCPSession(CallToolResult(content=[]))
    policy = ToolPolicy(
        action=ToolAction.READ,
        scope=ToolScope.EXTERNAL,
        requires_approval=False,
        permissions=frozenset({"mcp"}),
    )

    tool = MCPTool("obsidian", mcp_tool, session, policy=policy)

    assert tool.policy is policy


def test_mcp_tool_can_infer_read_policy_from_trusted_annotations():
    mcp_tool = McpTool(
        name="search",
        description="Search notes",
        inputSchema={"type": "object"},
        annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False),
    )
    session = FakeMCPSession(CallToolResult(content=[]))

    tool = MCPTool("obsidian", mcp_tool, session, trust_annotations=True)

    assert tool.policy.action is ToolAction.READ
    assert tool.policy.scope is ToolScope.EXTERNAL
    assert tool.policy.requires_approval is False


def test_mcp_tool_ignores_untrusted_annotations():
    mcp_tool = McpTool(
        name="search",
        description="Search notes",
        inputSchema={"type": "object"},
        annotations=ToolAnnotations(readOnlyHint=True),
    )
    session = FakeMCPSession(CallToolResult(content=[]))

    tool = MCPTool("obsidian", mcp_tool, session)

    assert tool.policy.action is ToolAction.EXECUTE
    assert tool.policy.requires_approval is True


def test_mcp_tool_preserves_complete_nested_schema():
    input_schema = {
        "type": "object",
        "additionalProperties": False,
        "$defs": {
            "Filter": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "score": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "required": ["score"],
            }
        },
        "properties": {
            "filter": {"$ref": "#/$defs/Filter"},
            "mode": {"oneOf": [{"const": "fast"}, {"const": "exact"}]},
        },
        "required": ["filter"],
    }
    tool = MCPTool(
        "notes",
        McpTool(name="search", description="Search", inputSchema=input_schema),
        FakeMCPSession(CallToolResult(content=[])),
    )

    parameters = tool.to_dict(tool.name)["function"]["parameters"]

    assert "$defs" not in parameters
    assert "$ref" not in str(parameters)
    assert parameters["additionalProperties"] is False
    assert parameters["properties"]["filter"]["properties"]["score"]["minimum"] == 1
    assert parameters["properties"]["filter"]["properties"]["score"]["maximum"] == 10
    assert parameters["properties"]["mode"]["oneOf"] == [{"const": "fast"}, {"const": "exact"}]


def test_mcp_tool_rejects_contradictory_trusted_annotations():
    mcp_tool = McpTool(
        name="erase",
        description="Erase",
        inputSchema={"type": "object"},
        annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=True),
    )

    with pytest.raises(ValueError, match="read-only and destructive"):
        MCPTool("notes", mcp_tool, FakeMCPSession(CallToolResult(content=[])), trust_annotations=True)


def test_mcp_tool_exposes_trusted_risk_annotations_in_metadata():
    mcp_tool = McpTool(
        name="publish",
        description="Publish",
        inputSchema={"type": "object"},
        annotations=ToolAnnotations(
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=True,
        ),
    )
    tool = MCPTool("notes", mcp_tool, FakeMCPSession(CallToolResult(content=[])), trust_annotations=True)

    policy = tool.get_metadata(tool.name)["policy"]
    assert policy["destructive"] is False
    assert policy["open_world"] is True
    assert policy["idempotent"] is True


@pytest.mark.asyncio
async def test_mcp_tool_exception_returns_sanitized_typed_failure():
    class FailingSession:
        async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> CallToolResult:
            raise RuntimeError("provider secret")

    tool = MCPTool(
        "notes",
        McpTool(name="search", description="Search", inputSchema={"type": "object"}),
        FailingSession(),
    )
    result = await tool.execute(
        ToolExecution(
            tool_id="call-1",
            tool_name=tool.name,
            ctx=ToolContext(
                session_state=SessionState(session_id="session-1", started_at=datetime(2026, 4, 30, tzinfo=UTC)),
                registry=ToolRegistry(),
                run=RunContext(run_id="run-1"),
                io=IOBridge(),
            ),
        )
    )

    assert result.is_error
    assert result.outcome.error.code == "mcp_provider_error"
    assert "provider secret" not in result.content
