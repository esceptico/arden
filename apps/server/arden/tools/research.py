import json
from typing import Literal

from coolname import generate_slug
from pydantic import BaseModel, Field

from arden.agent.coverage import ResearchOutline
from arden.agent.ledger import (
    CandidateSource,
    ContradictionNote,
    CuratedEvidence,
    DeadEndNote,
    FactNote,
    GapNote,
    SharedLedger,
    VerificationRecord,
    WorkspaceQuestion,
)
from arden.core.agent_types import SPAWN_SURFACE_GUIDANCE, AgentType, apply_profile, register_agent_type
from arden.core.isolation import IsolationLevel
from arden.core.prompts import RESEARCH_PROMPTS, current_date_formatted, env
from arden.logging import get_logger
from arden.tools.core import ToolResult, tool
from arden.tools.core.context import ToolExecution
from arden.tools.core.scope import tools
from arden.tools.core.types import ToolAction, ToolPolicy, ToolScope
from arden.tools.research_artifacts import (
    append_research_artifact_tool,
    artifact_scope_dir,
    list_research_artifacts_tool,
    read_research_artifact_tool,
    write_research_artifact_tool,
    write_scope_artifact,
)

_logger = get_logger(__name__)


LEDGER_TEMPLATE = env.from_string("""\
RESEARCH CONTEXT (shared across all agents in this run):
{% if active %}Active:
{% for item in active %}- "{{ item.label }}" ({{ item.metadata.depth }})
{% endfor %}{% endif %}\
{% if done %}Done:
{% for item in done %}- "{{ item.label }}" ({{ item.metadata.depth }})
{% endfor %}{% endif %}\
{% if accessed %}Documents already read: {{ accessed }}
{% endif %}\
{% if notes %}Research notes:
{% for note in notes %}- {{ note }}
{% endfor %}{% endif %}\
{% if coverage %}Coverage: {{ coverage.coverage }}%; gaps: {{ coverage.gaps | join(", ") if coverage.gaps else "none" }}
{% endif %}\
{% if workspace %}Harness workspace:
{% if workspace.search_history %}- Recent searches: {{ workspace.search_history | join("; ") }}
{% endif %}{% if workspace.sources %}- Sources:
{% for source in workspace.sources %}  - {{ source.id }}: {{ source.title }} — {{ source.locator }} [{{ source.status }}]{% if source.reason %}; {{ source.reason }}{% endif %}
{% endfor %}{% endif %}{% if workspace.evidence %}- Curated evidence:
{% for item in workspace.evidence %}  - {{ item.importance }}/{{ item.confidence }}: {{ item.claim }} ({{ item.source }}){% if item.quote %} quote="{{ item.quote }}"{% endif %}
{% endfor %}{% endif %}{% if workspace.verifications %}- Verification records:
{% for item in workspace.verifications %}  - {{ item.verdict }}: {{ item.claim }} ({{ item.sources | join(", ") }}){% if item.rationale %}; {{ item.rationale }}{% endif %}
{% endfor %}{% endif %}{% if workspace.questions %}- Open/dead-end questions:
{% for item in workspace.questions %}  - {{ item.status }}: {{ item.question }}{% if item.answer_or_reason %}; {{ item.answer_or_reason }}{% endif %}
{% endfor %}{% endif %}{% endif %}\
Do not re-research topics already covered. Focus on your specific scope.""")


def _format_ledger(
    ledger: SharedLedger,
    exclude_id: str | None = None,
    *,
    coverage_scope: str = "default",
) -> str:
    items = ledger.get_items(exclude_id=exclude_id)
    notes = [_format_note(note) for note in ledger.notes[-12:]]
    coverage_report = ledger.coverage_report(scope=coverage_scope)
    coverage = (
        {
            "coverage": round(coverage_report.coverage * 100),
            "gaps": coverage_report.gaps,
        }
        if coverage_report is not None
        else None
    )
    workspace = ledger.workspace_summary(scope=coverage_scope, max_items=8)
    if not items and not ledger.accessed_count and not notes and coverage is None and workspace is None:
        return ""

    active = [item for item in items if not item.done]
    done = [item for item in items if item.done]
    return LEDGER_TEMPLATE.render(
        active=active,
        done=done,
        accessed=ledger.accessed_count,
        notes=notes,
        coverage=coverage,
        workspace=workspace,
    )


def _format_note(note) -> str:
    if isinstance(note, FactNote):
        quote = f' quote="{note.quote}"' if note.quote else ""
        return f"fact: {note.claim} (source: {note.source}{quote})"
    if isinstance(note, DeadEndNote):
        return f"dead_end: tried {note.tried}; failed because {note.why_failed}"
    if isinstance(note, ContradictionNote):
        return f"contradiction: {note.claim_a} ({note.source_a}) vs {note.claim_b} ({note.source_b})"
    if isinstance(note, GapNote):
        return f"gap: {note.what_missing}"
    return str(note)


RESEARCH_SYSTEM_PROMPT = env.from_string("""{{ base_prompt }}

Today is {{ date }}.
{% if remaining_depth > 1 %}

DEPTH BUDGET: You can spawn {{ remaining_depth - 1 }} more levels of sub-agents. Use research() to delegate sub-topics — don't try to cover everything yourself.
{% elif remaining_depth == 1 %}

DEPTH BUDGET: You are at the last level — no more sub-agents. Do all work directly.
{% endif %}
{% if ledger_summary %}

{{ ledger_summary }}
{% endif %}
{% if memory_context %}

USER CONTEXT:
{{ memory_context }}
{% endif %}
{% if activated_skill_context %}

{{ activated_skill_context }}
{% endif %}

RESEARCH HARNESS — record AS YOU GO, never batched at the end:
- The harness is shared LIVE with other research agents in this run. The model decides what to inspect; the harness stores state so you do not carry everything in the transcript.
- Use research_track_search() for meaningful queries/paths tried, research_track_source() for candidate/read/rejected sources, research_curate() for important source-backed evidence, research_verify_claim() for claims you checked or found contradictory, and research_question() for open questions, gaps, and dead ends.
- Use research_cover() when a source supports an outline section, and research_outline() early for broad/deep tasks.
- Record after each source you read, before moving to the next. Batching all notes at the end defeats the harness — parallel agents can't see your progress and re-do the same analysis.
- Do not hide unsupported claims. If a claim is weak, contradictory, or missing evidence, record it and say so in the final answer.

OUTPUT CONTRACT:
- quick depth: return a concise answer directly unless there is genuinely bulky evidence.
- normal/deep depth or long outputs: write detailed markdown artifacts such as report.md, sources.md, evidence.md, or verification.md, then return only a TL;DR plus artifact manifest/path references.
- If artifacts already exist, prefer updating them instead of dumping raw text into final answer. Downstream workflow agents must read relevant artifacts before making detailed claims.

SCRATCHPAD (for long output):
- For bulky intermediates — long source inventories, large tables, draft reports — write them to an artifact with write_research_artifact()/append_research_artifact() instead of carrying everything in context, and read_research_artifact() back specific parts as needed.
- Keep your final answer a concise, distilled summary; the caller automatically sees the artifact manifest.""")

RESEARCH_DESCRIPTION = (
    "Spawn a detached research agent with access to all read-only tools. "
    "It runs independently and its report is delivered to this conversation "
    "automatically when done — do not poll or wait for it; finish your turn. "
    "Can fan out (call multiple in one turn) and nest recursively. "
    "Use depth='deep' for thorough research, 'quick' for fast lookups."
    f" {SPAWN_SURFACE_GUIDANCE}"
)


class ResearchInput(BaseModel):
    task: str = Field(description="What to research.")
    depth: Literal["quick", "normal", "deep"] = Field(
        default="normal",
        description="How thorough: 'quick' (fast scan), 'normal' (balanced), 'deep' (exhaustive).",
    )


class ResearchOutlineInput(BaseModel):
    sections: list[str] = Field(
        min_length=1,
        max_length=50,
        description="Required coverage sections for this research task (max 50).",
    )


class ResearchCoverInput(BaseModel):
    section: str = Field(description="Outline section title covered by the source.")
    source: str = Field(description="Source path, URL, message id, or tool result reference.")


class ResearchSourceInput(BaseModel):
    id: str = Field(description="Short stable source id, e.g. paper-1, readme, slack-thread-3.")
    title: str = Field(description="Human-readable source title.")
    locator: str = Field(description="URL, path, message id, or tool-result reference.")
    status: Literal["candidate", "read", "rejected"] = Field(default="candidate", description="Current source status.")
    reason: str | None = Field(default=None, description="Why the source matters or why it was rejected.")


class ResearchCurateInput(BaseModel):
    claim: str = Field(description="Atomic claim or finding supported by the source.")
    source: str = Field(description="Source id/path/URL/tool-result reference supporting the claim.")
    quote: str | None = Field(default=None, description="Short direct quote or exact evidence snippet when available.")
    importance: Literal["low", "medium", "high", "critical"] = Field(default="medium")
    confidence: Literal["low", "medium", "high"] = Field(default="medium")
    notes: str | None = Field(default=None, description="Caveats, scope, or why the evidence matters.")


class ResearchVerifyClaimInput(BaseModel):
    claim: str = Field(description="Claim that was checked against sources.")
    verdict: Literal["supported", "contradicted", "uncertain"] = Field(description="Verification verdict.")
    sources: list[str] = Field(default_factory=list, max_length=50, description="Sources consulted for the verdict.")
    rationale: str | None = Field(default=None, description="Brief reason, including what is missing if uncertain.")


class ResearchQuestionInput(BaseModel):
    question: str = Field(description="Open question, answered question, or dead end to track.")
    status: Literal["open", "answered", "dead_end"] = Field(default="open")
    answer_or_reason: str | None = Field(default=None, description="Answer if resolved, or why it dead-ended.")


class ResearchSearchInput(BaseModel):
    query: str = Field(description="Search query or exploration path tried during research.")


def _research_harness_unavailable() -> ToolResult:
    return ToolResult.failure(
        code="not_configured",
        message="Research harness state is unavailable.",
        preview="No research harness",
        recovery_action="Use research first so the child agent receives a scoped research harness.",
    )


def _invalid_research(message: str, preview: str) -> ToolResult:
    return ToolResult.failure(
        code="invalid_arguments",
        message=message,
        preview=preview,
        recovery_action="Correct the cited research field and retry.",
    )


async def _build_research_prompt(ctx, depth: str, remaining_depth: int, tool_id: str) -> str:
    ledger_summary = None
    if ctx.ledger:
        ledger_summary = _format_ledger(
            ctx.ledger,
            exclude_id=tool_id,
            coverage_scope=ctx.run.research_scope_id or "default",
        )

    return RESEARCH_SYSTEM_PROMPT.render(
        base_prompt=RESEARCH_PROMPTS[depth],
        date=current_date_formatted(),
        remaining_depth=remaining_depth,
        ledger_summary=ledger_summary,
        memory_context=None,
        activated_skill_context=None,
    )


async def research(execution: ToolExecution, args: ResearchInput) -> ToolResult:
    ctx = execution.ctx
    research_scope_id = f"research-{generate_slug(2)}"

    if not ctx.spawn_fn:
        return ToolResult.failure(
            code="not_configured",
            message="Research spawn capability is unavailable.",
            preview="Research unavailable",
            recovery_action="Run the research from a session with agent spawning enabled.",
        )

    if ctx.ledger:
        await ctx.ledger.register(research_scope_id, args.task, depth=args.depth)

    remaining = ctx.run.max_depth - ctx.run.current_depth - 1
    # Runtime-only: forbid nested research when shallow or nesting depth is exhausted.
    extra_exclude = frozenset({"research"}) if (args.depth == "quick" or remaining <= 1) else frozenset()
    prompt = await _build_research_prompt(ctx, args.depth, remaining, research_scope_id)
    # The read-only capability, the spawn-tool excludes, and the ledger toolset all
    # come from the registered research AgentType; the spawner builds the actual
    # toolset from that profile. Only the dynamic prompt + depth gate are per-call.
    profile = apply_profile(RESEARCH_AGENT_TYPE, system_prompt=prompt, exclude_tools=extra_exclude)
    try:
        spawn = await ctx.spawn_fn(
            ctx,
            task=args.task,
            model_override=ctx.run.research_model,
            reasoning_effort_override=ctx.run.research_reasoning_effort,
            parent_id=execution.tool_id,
            isolation=IsolationLevel.FULL,
            agent_type="research",
            wait=False,
            kind="research",
            compaction_prompt_context="research",
            include_tool_messages_in_compaction=True,
            research_scope_id=research_scope_id,
            **profile,
        )
    finally:
        # The ledger scope is no longer occupied by an awaited call — the spawn
        # is detached, so completing at spawn-receipt is what keeps sibling
        # dedup working: concurrent research calls see the topic claimed, not
        # perpetually "active" for an agent this caller never awaits.
        if ctx.ledger:
            await ctx.ledger.complete(research_scope_id)

    if spawn.status == "failed":
        # The only failure the detached path returns is the per-session concurrency cap.
        return ToolResult.failure(
            code="conflict",
            message=spawn.text,
            preview="At the agent limit",
            recovery_action="Wait for a running agent to finish, then spawn again.",
        )
    if spawn.agent_ref is None or spawn.child_session_ref is None:
        raise RuntimeError("detached research spawn is missing its agent/session ref")

    data: dict = spawn.child_agent_data()
    data["research_scope_id"] = research_scope_id
    data["artifact_dir"] = str(artifact_scope_dir(research_scope_id))
    # Spawn-time provenance only. The agent has not run yet, so there is no
    # usage/cost, no workspace, no artifacts, and no child tool_call_ids — the
    # completed picture lives in the child session and the delivered report.
    provenance = {
        "query": args.task[:4_096],
        "window": {
            "depth": args.depth,
            "current_depth": ctx.run.current_depth,
            "max_depth": ctx.run.max_depth,
        },
        "derivation": {
            "agent_ref": spawn.agent_ref,
        },
    }
    workspace_path = "_provenance.json"
    await write_scope_artifact(
        research_scope_id,
        workspace_path,
        json.dumps(provenance, ensure_ascii=False, sort_keys=True),
    )
    provenance["workspace_ref"] = f"{research_scope_id}:{workspace_path}"
    data["provenance"] = provenance

    content = (
        f"Started a {args.depth} research agent on: {args.task}\n"
        f'Its agent reference is {spawn.agent_ref} — steer it with session_send_message(session_ref="{spawn.child_session_ref}"), '
        "inspect its work with session_read(session_ref=...). "
        "The research report is delivered here automatically when done — do not poll for it; "
        "continue other work or end your turn and wait."
    )
    return ToolResult(
        content=content,
        preview=f"Research started ({args.depth})",
        data=data or None,
    )


async def research_outline(execution: ToolExecution, args: ResearchOutlineInput) -> ToolResult:
    ledger = execution.ctx.ledger
    if not ledger:
        return _research_harness_unavailable()
    sections = [section.strip() for section in args.sections if section.strip()]
    try:
        outline = ResearchOutline.from_titles(sections)
    except ValueError as exc:
        return _invalid_research(str(exc), "Invalid outline")
    ledger.set_outline(outline, scope=execution.ctx.run.research_scope_id or "default")
    return ToolResult(
        content=f"Research outline set: {', '.join(outline.titles)}", preview=f"Outline {len(outline.titles)} sections"
    )


async def research_cover(execution: ToolExecution, args: ResearchCoverInput) -> ToolResult:
    ledger = execution.ctx.ledger
    if not ledger:
        return _research_harness_unavailable()
    try:
        ledger.cover_section(args.section, args.source, scope=execution.ctx.run.research_scope_id or "default")
    except ValueError as exc:
        return _invalid_research(str(exc), "Coverage failed")
    report = ledger.coverage_report(scope=execution.ctx.run.research_scope_id or "default")
    assert report is not None
    percent = round(report.coverage * 100)
    gaps = ", ".join(report.gaps) if report.gaps else "none"
    return ToolResult(
        content=f"Covered {args.section} with {args.source}. Coverage: {percent}%; gaps: {gaps}",
        preview=f"Coverage {percent}%",
    )


def _research_scope(execution: ToolExecution) -> str:
    return execution.ctx.run.research_scope_id or "default"


def _require_ledger(execution: ToolExecution) -> SharedLedger | ToolResult:
    ledger = execution.ctx.ledger
    if not ledger:
        return _research_harness_unavailable()
    return ledger


async def research_track_source(execution: ToolExecution, args: ResearchSourceInput) -> ToolResult:
    ledger = _require_ledger(execution)
    if isinstance(ledger, ToolResult):
        return ledger
    source = CandidateSource(
        id=args.id.strip(),
        title=args.title.strip(),
        locator=args.locator.strip(),
        status=args.status,
        reason=args.reason,
    )
    if not source.id or not source.title or not source.locator:
        return _invalid_research("source id, title, and locator are required", "Invalid source")
    ledger.add_workspace_source(source, scope=_research_scope(execution))
    return ToolResult(
        content=f"Tracked source {source.id}: {source.title} ({source.status})", preview=f"Source {source.status}"
    )


async def research_curate(execution: ToolExecution, args: ResearchCurateInput) -> ToolResult:
    ledger = _require_ledger(execution)
    if isinstance(ledger, ToolResult):
        return ledger
    evidence = CuratedEvidence(
        claim=args.claim.strip(),
        source=args.source.strip(),
        quote=args.quote,
        importance=args.importance,
        confidence=args.confidence,
        notes=args.notes,
    )
    if not evidence.claim or not evidence.source:
        return _invalid_research("claim and source are required", "Invalid evidence")
    ledger.add_workspace_evidence(evidence, scope=_research_scope(execution))
    return ToolResult(
        content=f"Curated {args.importance}/{args.confidence} evidence: {evidence.claim}",
        preview=f"Evidence {args.importance}/{args.confidence}",
    )


async def research_verify_claim(execution: ToolExecution, args: ResearchVerifyClaimInput) -> ToolResult:
    ledger = _require_ledger(execution)
    if isinstance(ledger, ToolResult):
        return ledger
    verification = VerificationRecord(
        claim=args.claim.strip(),
        verdict=args.verdict,
        sources=tuple(s.strip() for s in args.sources if s.strip()),
        rationale=args.rationale,
    )
    if not verification.claim:
        return _invalid_research("claim is required", "Invalid verification")
    ledger.add_workspace_verification(verification, scope=_research_scope(execution))
    return ToolResult(
        content=f"Verified claim as {verification.verdict}: {verification.claim}", preview=f"{verification.verdict}"
    )


async def research_question(execution: ToolExecution, args: ResearchQuestionInput) -> ToolResult:
    ledger = _require_ledger(execution)
    if isinstance(ledger, ToolResult):
        return ledger
    question = WorkspaceQuestion(
        question=args.question.strip(),
        status=args.status,
        answer_or_reason=args.answer_or_reason,
    )
    if not question.question:
        return _invalid_research("question is required", "Invalid question")
    ledger.add_workspace_question(question, scope=_research_scope(execution))
    return ToolResult(
        content=f"Tracked {question.status} question: {question.question}", preview=f"Question {question.status}"
    )


async def research_track_search(execution: ToolExecution, args: ResearchSearchInput) -> ToolResult:
    ledger = _require_ledger(execution)
    if isinstance(ledger, ToolResult):
        return ledger
    ledger.add_workspace_search(args.query, scope=_research_scope(execution))
    return ToolResult(content=f"Tracked research search: {args.query}", preview="Search tracked")


research_tool = tool(
    display_name="Research",
    display_description="Run a focused research agent.",
    description=RESEARCH_DESCRIPTION,
    input_model=ResearchInput,
    policy=ToolPolicy(
        action=ToolAction.EXECUTE,
        scope=ToolScope.INTERNAL,
        concurrency_group="research_agents",
        destructive=False,
        open_world=True,
        idempotent=False,
    ),
    execute=research,
    kind="agent",
)

research_outline_tool = tool(
    display_name="Research Outline",
    description="Set the required coverage sections for a broad or deep research task.",
    input_model=ResearchOutlineInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        offload=False,
        destructive=True,
        open_world=False,
        idempotent=True,
    ),
    execute=research_outline,
)

research_cover_tool = tool(
    display_name="Research Cover",
    description="Mark an outline section as covered by a specific source.",
    input_model=ResearchCoverInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        offload=False,
        destructive=False,
        open_world=False,
        idempotent=True,
    ),
    execute=research_cover,
)

research_track_source_tool = tool(
    display_name="Research Track Source",
    description="Track a candidate/read/rejected source in the structured research harness workspace.",
    input_model=ResearchSourceInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        offload=False,
        destructive=True,
        open_world=False,
        idempotent=True,
    ),
    execute=research_track_source,
)

research_curate_tool = tool(
    display_name="Research Curate Evidence",
    description="Promote an important source-backed finding into the research harness with importance/confidence metadata.",
    input_model=ResearchCurateInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        offload=False,
        destructive=False,
        open_world=False,
        idempotent=False,
    ),
    execute=research_curate,
)

research_verify_claim_tool = tool(
    display_name="Research Verify Claim",
    description="Record whether a claim is supported, contradicted, or uncertain based on inspected sources.",
    input_model=ResearchVerifyClaimInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        offload=False,
        destructive=False,
        open_world=False,
        idempotent=False,
    ),
    execute=research_verify_claim,
)

research_question_tool = tool(
    display_name="Research Question",
    description="Track an open, answered, or dead-end research question in the structured harness workspace.",
    input_model=ResearchQuestionInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        offload=False,
        destructive=False,
        open_world=False,
        idempotent=False,
    ),
    execute=research_question,
)

research_track_search_tool = tool(
    display_name="Research Track Search",
    description="Record a meaningful query or exploration path tried during research.",
    input_model=ResearchSearchInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        offload=False,
        destructive=False,
        open_world=False,
        idempotent=False,
    ),
    execute=research_track_search,
)

RESEARCH_AGENT_TOOLS = {
    "research_outline": research_outline_tool,
    "research_cover": research_cover_tool,
    "research_track_source": research_track_source_tool,
    "research_curate": research_curate_tool,
    "research_verify_claim": research_verify_claim_tool,
    "research_question": research_question_tool,
    "research_track_search": research_track_search_tool,
    "write_research_artifact": write_research_artifact_tool,
    "append_research_artifact": append_research_artifact_tool,
    "read_research_artifact": read_research_artifact_tool,
    "list_research_artifacts": list_research_artifacts_tool,
}

# research is itself an AgentType in the shared registry: a read-only capability,
# the spawn/background tools excluded, and its ledger tools as extras. The prompt
# is left to the call site (research() renders it per-call from depth + live
# ledger), and the depth-based self-exclusion of `research` is added at call time.
RESEARCH_AGENT_TYPE = AgentType(
    name="research",
    scope=tools.read,
    exclude=frozenset({"agent_cancel", "workflow"}),
    extra_tools=RESEARCH_AGENT_TOOLS,
)
register_agent_type(RESEARCH_AGENT_TYPE)
