from collections.abc import Mapping
from dataclasses import dataclass, field

from arden.core.prompts import UNTRUSTED_DATA_RULE
from arden.tools.core.base import Tool
from arden.tools.core.scope import ToolFilter, tools

# An agent type is a TOOL PROFILE first — what the agent is allowed to touch —
# with a prompt that rides along. This is the shape the research subagent already
# used by hand (read-only base − spawn tools + its ledger tools); both the
# workflow agent() combinator and research() now resolve from this one registry
# instead of each assembling a toolset inline.
#
#   scope    capability, as a ToolFilter: `tools.read` is a read-only analyst,
#            None is a builder that writes/executes. Same algebra automations
#            use, so "read-only" has one spelling in the codebase.
#   exclude  tool names removed on top of the capability filter.
#   extra_tools  specialized tools injected for this type (research's ledger).
#   prompt   static persona prompt; None means the caller supplies it (research's
#            prompt is rendered per-call from depth + live ledger).
#
# Invocation concerns (isolation, wait, compaction handoff, model) are NOT here —
# they are set at the spawn site, because they are not part of "what tools this
# kind of agent has."

_READ = tools.read

SPAWN_SURFACE_GUIDANCE = (
    "Choose research for a detached investigation (depth='quick' for simple one-off tasks); "
    "workflow for a curated multi-agent pipeline; create_automation for recurring "
    "scheduled/event work; create_loop only for adaptive repeated work that stops on a condition.\n"
    "Delegation rubric:\n"
    "- Plan first: identify the critical-path blocker — the thing your very next step depends on. "
    "NEVER delegate it; do it yourself so the critical path keeps moving.\n"
    "- Delegate sidecar work: concrete, bounded subtasks that advance the goal in parallel without "
    "blocking your next step.\n"
    "- Each subtask must be concrete, self-contained, and materially useful; for edits, give agents "
    "disjoint write sets. Fan out independent subtasks in the same turn.\n"
    "- Everything runs detached: you get a receipt now and the result arrives as a message later. "
    "Receipts are not results — never summarize or answer from them.\n"
    "- After spawning, immediately do meaningful non-overlapping work, or briefly say what you "
    "started and end the turn. Never poll or wait idle.\n"
    "- When a report arrives, integrate it — do not redo the child's work."
)


@dataclass(frozen=True)
class AgentType:
    name: str
    scope: ToolFilter | None = None
    exclude: frozenset[str] = frozenset()
    extra_tools: Mapping[str, Tool] = field(default_factory=dict)
    prompt: str | None = None


_REGISTRY: dict[str, AgentType] = {}


def register_agent_type(spec: AgentType) -> None:
    if spec.name in _REGISTRY:
        raise ValueError(f"duplicate agent_type: {spec.name}")
    _REGISTRY[spec.name] = spec


def resolve_agent_type(name: str) -> AgentType:
    """An explicitly-requested type that doesn't exist fails loudly with the valid
    set, so the authoring model self-corrects instead of reproducing a bad id."""
    spec = _REGISTRY.get(name)
    if spec is None:
        raise ValueError(f"unknown agent_type {name!r}; available: {sorted(_REGISTRY)}")
    return spec


def apply_profile(
    spec: AgentType,
    *,
    system_prompt: str | None = None,
    exclude_tools: frozenset[str] = frozenset(),
    extra_tools: Mapping[str, Tool] | None = None,
) -> dict:
    """Merge a type's profile with call-site overrides into spawn kwargs. A
    caller-supplied system_prompt wins over the persona's; excludes and extra
    tools union. Returns the spawn_fn kwargs the type controls."""
    return {
        "scope": spec.scope,
        "system_prompt": system_prompt or spec.prompt,
        "exclude_tools": frozenset(exclude_tools) | spec.exclude,
        "extra_tools": {**dict(spec.extra_tools), **dict(extra_tools or {})},
    }


# Read-only analyst personas: they read/search/reason and report, never mutate.
_REVIEWER_PROMPT = (
    "You are a meticulous code reviewer. Read the relevant code — and the enclosing "
    "functions, not just the changed lines — and surface real defects: correctness "
    "bugs, security holes, broken edge cases, swallowed errors, off-by-ones. Quote "
    "exact file:line and give a concrete failure scenario (the inputs/state that "
    "trigger it). Report only real bugs — no style nits, no speculation. If the code "
    "is correct, say so plainly."
)
_EXPLORER_PROMPT = (
    "You are a codebase explorer. Find the relevant code by searching broadly — grep "
    "for symbols, read excerpts, follow imports and call sites. Report WHERE things "
    "live (file:line) and how the pieces fit together: a concise map, not full file "
    "dumps. Surface the conclusion, not the raw search output."
)
_PLANNER_PROMPT = (
    "You are a software architect. Produce a concrete implementation plan: the exact "
    "files to create or change, the approach for each, the build order, and the "
    "risks/unknowns. Read enough of the existing code to ground the plan in real "
    "patterns. Design it — do not write the implementation."
)
_VERIFIER_PROMPT = (
    "You are an adversarial verifier. Given a claim or finding, try hard to REFUTE it "
    "against the actual code/evidence. Quote the exact lines that confirm or disprove "
    "it. Default to skeptical: if you cannot substantiate the claim, mark it "
    "unconfirmed. State the inputs/state that trigger the issue, or why it can't."
)
# Write-capable persona: full toolset (writes files, runs tools). The capability
# axis is a ToolFilter, so a builder is just `scope=None` — read-only is one
# value of the axis, not the axis itself.
_BUILDER_PROMPT = (
    "You are an implementer. Make the change end to end: read the surrounding code, "
    "edit the files, and use whatever tools the task needs. Match the existing style "
    "and keep the change tightly scoped. Report what you changed, file:line."
)

for _spec in (
    AgentType("reviewer", scope=_READ, prompt=f"{_REVIEWER_PROMPT} {UNTRUSTED_DATA_RULE}"),
    AgentType("explorer", scope=_READ, prompt=f"{_EXPLORER_PROMPT} {UNTRUSTED_DATA_RULE}"),
    AgentType("planner", scope=_READ, prompt=f"{_PLANNER_PROMPT} {UNTRUSTED_DATA_RULE}"),
    AgentType("verifier", scope=_READ, prompt=f"{_VERIFIER_PROMPT} {UNTRUSTED_DATA_RULE}"),
    AgentType("builder", scope=None, prompt=f"{_BUILDER_PROMPT} {UNTRUSTED_DATA_RULE}"),
):
    register_agent_type(_spec)
