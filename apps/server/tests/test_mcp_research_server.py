from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from starlette.testclient import TestClient

import arden.mcp.server as mcp_server
import arden.tools.research as research_module
from arden.agent.ledger import ContradictionNote, FactNote, GapNote
from arden.settings import hash_api_key
from arden.tools.core import ToolResult


class FakeRunner:
    def __init__(self, output: mcp_server.ArdenResearchOutput):
        self.output = output
        self.calls = []

    async def run(self, *, task: str, depth: mcp_server.ResearchDepth) -> mcp_server.ArdenResearchOutput:
        self.calls.append({"task": task, "depth": depth})
        return self.output


@pytest.mark.asyncio
async def test_mcp_server_exposes_structured_arden_research_tool():
    output = mcp_server.ArdenResearchOutput(
        answer="Dex can call arden as a research oracle.",
        evidence=[
            mcp_server.ResearchEvidence(
                claim="Arden has a research tool",
                source="apps/server/arden/tools/research.py",
                quote="Spawn a research agent",
            )
        ],
        gaps=["No background polling yet."],
        contradictions=[],
        run_id="research-1",
    )
    runner = FakeRunner(output)

    @asynccontextmanager
    async def runner_factory():
        yield runner

    server = mcp_server.create_mcp_server(runner_factory=runner_factory)

    tools = await server.list_tools()
    result = await server.call_tool("arden_research", {"task": "research Dex integration"})

    content, structured = result
    assert [tool.name for tool in tools] == ["arden_research"]
    assert runner.calls == [{"task": "research Dex integration", "depth": "normal"}]
    assert content[0].text.startswith("{")
    assert structured == output.model_dump(mode="json")


@pytest.mark.asyncio
async def test_runtime_research_runner_areas_internal_ledger_notes(monkeypatch):
    async def fake_research(execution, args):
        execution.ctx.ledger.add_note(
            FactNote(
                claim="Dex should call arden as a research oracle.",
                source="docs/integrations/mcp.mdx",
                quote="consultation oracle",
            )
        )
        execution.ctx.ledger.add_note(GapNote(what_missing="No polling mode in the first area."))
        execution.ctx.ledger.add_note(
            ContradictionNote(
                claim_a="Expose raw memory APIs.",
                source_a="old-plan",
                claim_b="Do not center the MCP surface on memory.",
                source_b="current-plan",
            )
        )
        return ToolResult(content="Research answer.", preview="Researched")

    monkeypatch.setattr(research_module, "research", fake_research)

    runtime = SimpleNamespace(
        config=SimpleNamespace(
            chat_model="model-a",
            research_model="model-b",
            workflow_model="model-c",
            max_depth=3,
            agent_max_iterations=None,
            agent_max_tool_calls=None,
            agent_max_wall_time_seconds=None,
            agent_max_cost=None,
            agent_max_output_tokens=None,
            model_reasoning_efforts={},
            role_setup=lambda _role: SimpleNamespace(model=None, reasoning_effort=None),
            deferred_tools=False,
            compression_threshold=0.8,
            max_messages=120,
            compression_keep_ratio=0.2,
            summary_max_tokens=1500,
            approval_timeout_seconds=300,
            reasoning_effort_for=lambda model: None,
        ),
        executor=SimpleNamespace(registry=SimpleNamespace(), tool_services={}),
    )
    runner = mcp_server.RuntimeResearchRunner(runtime, run_id_factory=lambda: "research-1")

    result = await runner.run(task="research Dex integration", depth="deep")

    assert result.answer == "Research answer."
    assert result.run_id == "research-1"
    assert [e.model_dump() for e in result.evidence] == [
        {
            "claim": "Dex should call arden as a research oracle.",
            "source": "docs/integrations/mcp.mdx",
            "quote": "consultation oracle",
        }
    ]
    assert result.gaps == ["No polling mode in the first area."]
    assert [c.model_dump() for c in result.contradictions] == [
        {
            "claim_a": "Expose raw memory APIs.",
            "source_a": "old-plan",
            "claim_b": "Do not center the MCP surface on memory.",
            "source_b": "current-plan",
        }
    ]


@pytest.mark.asyncio
async def test_api_key_token_verifier_accepts_existing_client_key():
    verifier = mcp_server.APIKeyTokenVerifier(hash_api_key("client-key"))

    accepted = await verifier.verify_token("client-key")
    rejected = await verifier.verify_token("wrong-key")

    assert accepted is not None
    assert accepted.client_id == "arden-client"
    assert accepted.scopes == ["arden:mcp"]
    assert rejected is None


def test_streamable_http_app_requires_api_key_when_configured():
    server = mcp_server.create_mcp_server(
        api_key_hash=hash_api_key("client-key"),
        public_url="http://127.0.0.1:6878",
    )

    with TestClient(server.streamable_http_app(), base_url="http://127.0.0.1:6878") as client:
        response = client.post(
            "/mcp",
            headers={
                "content-type": "application/json",
                "accept": "application/json, text/event-stream",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_token"
