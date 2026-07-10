from datetime import UTC, datetime
from typing import Any

import pytest
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from mcp.types import Tool as McpTool

from ntrp.context.models import SessionState
from ntrp.mcp.tool import MCPTool
from ntrp.tools.core.context import IOBridge, RunContext, ToolContext, ToolExecution
from ntrp.tools.core.registry import ToolRegistry
from ntrp.tools.core.types import ToolAction, ToolPolicy, ToolScope


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
