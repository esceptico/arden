from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Literal, Protocol

from coolname import generate_slug
from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

import arden.tools.research as research_module
from arden.agent.ledger import ContradictionNote, FactNote, GapNote, SharedLedger
from arden.context.models import SessionState
from arden.core.factory import AgentConfig
from arden.core.spawner import create_spawn_fn
from arden.core.usage_tracker import UsageTracker
from arden.server.runtime import Runtime
from arden.settings import verify_api_key
from arden.tools.core.context import BackgroundTaskRegistry, IOBridge, RunContext, ToolContext, ToolExecution

type ResearchDepth = Literal["quick", "normal", "deep"]


class ResearchEvidence(BaseModel):
    claim: str
    source: str
    quote: str | None = None


class ResearchContradiction(BaseModel):
    claim_a: str
    source_a: str
    claim_b: str
    source_b: str


class ArdenResearchOutput(BaseModel):
    answer: str
    evidence: list[ResearchEvidence] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    contradictions: list[ResearchContradiction] = Field(default_factory=list)
    run_id: str


class ResearchRunner(Protocol):
    async def run(self, *, task: str, depth: ResearchDepth) -> ArdenResearchOutput: ...


RunnerFactory = Callable[[], AbstractAsyncContextManager[ResearchRunner]]


class APIKeyTokenVerifier:
    def __init__(self, api_key_hash: str):
        self._api_key_hash = api_key_hash

    async def verify_token(self, token: str) -> AccessToken | None:
        if not verify_api_key(token, self._api_key_hash):
            return None
        return AccessToken(
            token=token,
            client_id="arden-client",
            scopes=["arden:mcp"],
        )


class RuntimeResearchRunner:
    def __init__(self, runtime, *, run_id_factory: Callable[[], str] | None = None):
        self._runtime = runtime
        self._run_id_factory = run_id_factory or _new_run_id

    async def run(self, *, task: str, depth: ResearchDepth) -> ArdenResearchOutput:
        config = self._runtime.config
        model = config.chat_model or config.research_model
        if not model:
            raise RuntimeError("No chat or research model configured.")
        if self._runtime.executor is None:
            raise RuntimeError("Runtime is not connected.")

        run_id = self._run_id_factory()
        agent_config = AgentConfig.from_config(config, model=model)
        ledger = SharedLedger()
        session_state = SessionState(session_id=f"mcp::{run_id}", started_at=datetime.now(UTC))
        background_tasks = BackgroundTaskRegistry(session_id=session_state.session_id)
        run = RunContext(
            run_id=run_id,
            current_depth=0,
            max_depth=agent_config.max_depth,
            max_iterations=agent_config.max_iterations,
            max_tool_calls=agent_config.max_tool_calls,
            max_wall_time_seconds=agent_config.max_wall_time_seconds,
            max_cost=agent_config.max_cost,
            research_model=agent_config.research_model or model,
            deferred_tools_enabled=agent_config.deferred_tools,
        )
        ctx = ToolContext(
            session_state=session_state,
            registry=self._runtime.executor.registry,
            run=run,
            io=IOBridge(),
            services=self._runtime.executor.tool_services,
            ledger=ledger,
            background_tasks=background_tasks,
            parent_tracker=UsageTracker(),
        )
        ctx.spawn_fn = create_spawn_fn(
            executor=self._runtime.executor,
            model=model,
            max_depth=agent_config.max_depth,
            current_depth=0,
            reasoning_effort=agent_config.reasoning_effort,
            model_reasoning_efforts=agent_config.model_reasoning_efforts or {},
            compactor=agent_config.compactor,
            max_iterations=agent_config.max_iterations,
            max_tool_calls=agent_config.max_tool_calls,
            max_wall_time_seconds=agent_config.max_wall_time_seconds,
            max_cost=agent_config.max_cost,
            budget=run.budget,
        )

        result = await research_module.research(
            ToolExecution(tool_id=run_id, tool_name="arden_research", ctx=ctx),
            research_module.ResearchInput(task=task, depth=depth),
        )
        ledger.add_coverage_gap_notes(scope=run_id)
        return _research_output(run_id, result.content, ledger)


class RuntimeResearchRunnerContext:
    def __init__(self, runtime_factory: Callable[[], Runtime] = Runtime):
        self._runtime_factory = runtime_factory
        self._runtime: Runtime | None = None

    async def __aenter__(self) -> RuntimeResearchRunner:
        self._runtime = self._runtime_factory()
        await self._runtime.connect()
        return RuntimeResearchRunner(self._runtime)

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._runtime is not None:
            await self._runtime.close()


def create_mcp_server(
    *,
    runner_factory: RunnerFactory | None = None,
    host: str = "127.0.0.1",
    port: int = 6878,
    api_key_hash: str | None = None,
    public_url: str | None = None,
) -> FastMCP:
    auth = None
    token_verifier = None
    if api_key_hash:
        public_url = public_url or f"http://{host}:{port}"
        auth = AuthSettings(
            issuer_url=public_url,
            resource_server_url=public_url,
            required_scopes=["arden:mcp"],
        )
        token_verifier = APIKeyTokenVerifier(api_key_hash)

    server = FastMCP("arden", host=host, port=port, auth=auth, token_verifier=token_verifier)
    factory = runner_factory or _default_runner_factory

    @server.tool(
        name="arden_research",
        title="Research with arden",
        description="Run a blocking arden research task and return answer, evidence, gaps, and contradictions.",
        annotations=ToolAnnotations(readOnlyHint=True),
        structured_output=True,
    )
    async def arden_research(
        task: Annotated[str, Field(description="The research task to run.")],
        depth: Annotated[
            ResearchDepth,
            Field(description="Research depth: quick, normal, or deep."),
        ] = "normal",
    ) -> ArdenResearchOutput:
        async with factory() as runner:
            return await runner.run(task=task, depth=depth)

    return server


@asynccontextmanager
async def _default_runner_factory():
    async with RuntimeResearchRunnerContext() as runner:
        yield runner


def _research_output(run_id: str, answer: str, ledger: SharedLedger) -> ArdenResearchOutput:
    evidence: list[ResearchEvidence] = []
    gaps: list[str] = []
    contradictions: list[ResearchContradiction] = []

    for note in ledger.notes:
        if isinstance(note, FactNote):
            evidence.append(ResearchEvidence(claim=note.claim, source=note.source, quote=note.quote))
        elif isinstance(note, GapNote):
            gaps.append(note.what_missing)
        elif isinstance(note, ContradictionNote):
            contradictions.append(
                ResearchContradiction(
                    claim_a=note.claim_a,
                    source_a=note.source_a,
                    claim_b=note.claim_b,
                    source_b=note.source_b,
                )
            )

    return ArdenResearchOutput(
        answer=answer,
        evidence=evidence,
        gaps=gaps,
        contradictions=contradictions,
        run_id=run_id,
    )


def _new_run_id() -> str:
    return f"research-{generate_slug(2)}"


__all__ = [
    "APIKeyTokenVerifier",
    "ArdenResearchOutput",
    "ResearchContradiction",
    "ResearchDepth",
    "ResearchEvidence",
    "RuntimeResearchRunner",
    "RuntimeResearchRunnerContext",
    "create_mcp_server",
]
