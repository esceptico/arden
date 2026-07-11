import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from ntrp.areas.asks import AskStore
from ntrp.areas.models import Area, Ask
from ntrp.areas.work_models import AreaWorkSnapshot, EvidenceDraft, OutcomeChange, WorkChange

# Observe-mode toolset as DATA: the automation's tool_scope allowlist
# (visible and editable on the automation itself) instead of a code-side
# toolset hack. Read/search surfaces + the memory tools that edit the
# area's own page — nothing that acts on the outside world.
OBSERVE_TOOL_SCOPE = [
    "area_page_read",
    "area_page_patch",
    "area_page_write",
    "recall",
    "list_recent_sessions",
    "read_session",
    "search_transcripts",
    "web_search",
    "web_fetch",
    "read_file",
    "list_files",
    "find_files",
    "search_text",
    "current_time",
    "emails",
    "read_email",
    "calendar",
    "slack_search",
    "slack_channel",
    "slack_thread",
    "slack_channels",
    "slack_dms",
    "slack_dm",
    "slack_users",
    "slack_user",
    "slack_file",
]

# Acting expands only to named integration tools. Their own policies still
# require approval for consequential writes (create_automation is
# approval-gated), so autonomy never becomes a wildcard or an approval
# bypass. Children mint under area:{key}:{slug} — the boundary
# area_run_automation enforces.
ACT_TOOL_SCOPE = [
    *OBSERVE_TOOL_SCOPE,
    "create_automation",
    "list_automations",
    "get_automation_result",
    "area_run_automation",
]

_CONTRACT = {
    "observe": "You may read anything and update this area's topic page, but take no external action.",
    "act": "You may run child automations owned by this area; their consequential actions still require approval.",
}

# Only findings that clear this salience become asks; the rest belong on the
# page. Calibration research says a proactive agent's "worth telling you"
# judgment misfires ~1/3 of the time — marginal findings never reach the user.
SALIENCE_THRESHOLD = 3
MAX_ASKS_PER_RUN = 3
NOTIFY_ASK_TTL_HOURS = 72


class AreaAskDraft(BaseModel):
    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    text: str
    # notify = FYI (no decision, expires quietly); question = blocked on the
    # user's judgment; review = proposed action awaiting approval.
    kind: Literal["notify", "question", "review"]
    # 1 = barely worth the page, 5 = drop everything. Below the threshold the
    # finding stays on the page and never becomes an ask.
    salience: int = Field(ge=1, le=5)
    why_now: str
    what_next: str


class AreaAskNomination(BaseModel):
    """The run's structured output (registered as "area_ask"): the run's
    findings triaged into at most a few asks (empty on a quiet day), plus the
    self-paced next check. Schema-validated by the constrained final step, so
    the transcript stays prose and the nomination arrives as a guaranteed-
    shaped object."""

    asks: list[AreaAskDraft] = Field(default_factory=list, max_length=MAX_ASKS_PER_RUN)
    # One line the room shows as "what the agent did this run".
    report: str
    # Self-paced heartbeat: when to look next (clamped by the area's
    # attention bounds) and the reason the user sees in the room.
    next_check_hours: float = Field(gt=0, le=24 * 14)
    next_check_reason: str


class AreaCustodianReport(AreaAskNomination):
    outcome_changes: list[OutcomeChange] = Field(default_factory=list, max_length=20)
    work_changes: list[WorkChange] = Field(default_factory=list, max_length=30)
    evidence: list[EvidenceDraft] = Field(default_factory=list, max_length=30)
    made_progress: bool
    work_remaining: bool
    continuation_minutes: int | None = Field(default=None, ge=5, le=240)
    continuation_reason: str | None = Field(default=None, min_length=1, max_length=500)


def area_agent_instructions(area: Area) -> str:
    """The automation's description = the standing per-turn message. The
    fresh topic page is NOT embedded here — it arrives via the AREA system
    block on the area-tagged channel session, so every turn sees current
    state while these instructions stay static."""
    return (
        f"You are the standing custodian of the '{area.title}' area of the user's life. "
        f"Its topic page is in your AREA context block.\n"
        f"Autonomy contract ({area.autonomy}): {_CONTRACT[area.autonomy]}\n\n"
        "This turn: reconcile new evidence with CURRENT AREA WORK, then choose the "
        "highest-leverage unblocked action. Execute multiple useful steps with tools; "
        "do not stop at describing or tracking work. Stop only after material progress, "
        "completion, or a genuine blocker. Update the topic page when its narrative "
        "should change, and return explicit outcome/work operations plus evidence. "
        "Never remove work by omission. For every non-create operation, copy its "
        "expected_updated_at value from CURRENT AREA WORK exactly.\n"
        "End with a short prose report. Afterwards you will be asked for a structured "
        "report: atomic outcome/work/evidence changes, findings triaged into at most "
        "three asks, whether material progress happened, whether work remains, and when "
        "to continue. A short continuation is for executable work, not external waiting.\n"
        "Ask kinds — pick the dimmest that's true:\n"
        "- notify: an FYI worth a glance; no decision needed. Expires on its own.\n"
        "- question: you are blocked on the user's judgment and cannot proceed without it.\n"
        "- review: you propose a concrete action and want approval before it happens.\n"
        "Every ask must name the concrete object, why now, and what happens next. "
        "Give it a short stable key reused across runs until that exact decision is resolved. "
        "Score each 1-5 on salience; low-salience findings belong on the page, not in an ask.\n"
        "Next check: pick when you should genuinely look again and say why in one line "
        "(a deadline, an expected reply, 'quiet — nothing moves until X'). Routine "
        "tracking does not justify a short interval."
    )


def render_work_context(snapshot: AreaWorkSnapshot) -> str:
    payload = {
        "outcomes": [row.model_dump(mode="json") for row in snapshot.outcomes],
        "work_items": [row.model_dump(mode="json") for row in snapshot.work_items],
        "recent_evidence": [row.model_dump(mode="json") for row in snapshot.events[:10]],
    }
    return "CURRENT AREA WORK (canonical structured state):\n" + json.dumps(payload, ensure_ascii=False)


@dataclass(frozen=True)
class CustodianContract:
    description: str
    tool_scope: list[str]
    auto_approve: bool = False


def custodian_contract(area: Area) -> CustodianContract:
    """Return the complete runtime permission contract for an Area.

    Tool policies remain the approval authority. In particular, acting mode
    expands the allowlist but never disables approvals globally.
    """
    scope = ACT_TOOL_SCOPE if area.autonomy == "act" else OBSERVE_TOOL_SCOPE
    return CustodianContract(
        description=area_agent_instructions(area),
        tool_scope=list(scope),
    )


INTAKE_ADDENDUM = (
    "\n\nINTAKE (first run): you have no run history yet. This run, instead of routine "
    "tending: (1) read the topic page and recent area conversations, (2) write a short "
    "WATCHING section onto the topic page — the concrete things you will track and what "
    "you will never touch, (3) if one thing genuinely needs the user's calibration "
    "(a boundary, a priority, a missing fact), raise it as a single question ask; "
    "otherwise stay silent and set your first next-check."
)


def record_area_run(
    asks: AskStore,
    area_key: str,
    page_path: str,
    structured_output: dict | None,
    run_ref: str,
) -> list[Ask]:
    """Post-run ask sync (called from the outbox run-completed pipeline):
    every run re-decides the area's agent asks — silence retires previous
    nominations just like new ones supersede them. `structured_output` is the
    schema-validated AreaAskNomination dump (or None when the constrained
    step failed — treated as silence)."""
    if structured_output is None:
        return []
    nominated = structured_output.get("asks") or []
    if not nominated:
        return []
    now = datetime.now(UTC)
    created: list[Ask] = []
    kept = [n for n in nominated if n.get("salience", 0) >= SALIENCE_THRESHOLD]
    for n in kept[:MAX_ASKS_PER_RUN]:
        expires = (
            (now + timedelta(hours=NOTIFY_ASK_TTL_HOURS)).isoformat()
            if n["kind"] == "notify"
            else None
        )
        stable_key = n["key"]
        ask = (
            Ask(
                id=f"agent:{area_key}:{stable_key}",
                area_key=area_key,
                text=n["text"],
                kind=n["kind"],
                source="agent",
                actions=[{"verb": "open_page", "ref": page_path}],
                state="active",
                created_at=now.isoformat(),
                provenance=run_ref,
                why_now=n.get("why_now"),
                what_next=n.get("what_next"),
                expires_at=expires,
                stable_key=stable_key,
            )
        )
        if asks.upsert_agent_nomination(ask):
            created.append(ask)
    return created
