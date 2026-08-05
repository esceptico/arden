import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from arden.areas.asks import AskStore
from arden.areas.display_contract import (
    ASK_DETAIL_MAX_CHARS,
    ASK_TEXT_MAX_CHARS,
    DISPLAY_LINE_PATTERN,
    NEXT_CHECK_REASON_MAX_CHARS,
    REPORT_MAX_CHARS,
)
from arden.areas.models import Area, Ask
from arden.areas.work_models import AreaWorkSnapshot, EvidenceDraft, OutcomeChange, WorkChange
from arden.tools.scopes import AREA_REPORT_TOOL_NAME, ScopeKey

# A custodian's toolset is one of the fine-grained scopes in arden.tools.scopes.
# Children mint under area:{key}:{slug} — the boundary area_run_automation
# enforces.
CUSTODIAN_TASK_PREFIX = "area:"
CUSTODIAN_ACTOR_PREFIX = "automation:area:"

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
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    text: str = Field(
        min_length=1,
        max_length=ASK_TEXT_MAX_CHARS,
        pattern=DISPLAY_LINE_PATTERN,
        description="Direct one-line decision or update headline shown to the user.",
    )
    # notify = FYI (no decision, expires quietly); question = blocked on the
    # user's judgment; review = proposed action awaiting approval.
    kind: Literal["notify", "question", "review"]
    # 1 = barely worth the page, 5 = drop everything. Below the threshold the
    # finding stays on the page and never becomes an ask.
    salience: int = Field(ge=1, le=5)
    why_now: str = Field(
        min_length=1,
        max_length=ASK_DETAIL_MAX_CHARS,
        pattern=DISPLAY_LINE_PATTERN,
        description="One concrete sentence explaining why this needs attention now.",
    )
    what_next: str = Field(
        min_length=1,
        max_length=ASK_DETAIL_MAX_CHARS,
        pattern=DISPLAY_LINE_PATTERN,
        description="One concrete sentence describing what happens after the user responds.",
    )
    # The executable half of a proposal. An observe custodian can never widen
    # its own reach — a spawned child is intersected with the parent's allowlist
    # — so the capability is minted by the user's approval instead: on approve,
    # this task runs as a fresh top-level agent carrying the user's own scope.
    action: str | None = Field(default=None, min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def validate_action(self):
        if self.action is not None and self.kind != "review":
            raise ValueError("only a review ask carries an action; notify and question are prose")
        return self


class AreaAskNomination(BaseModel):
    """Findings triaged into asks plus the self-paced next check."""

    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    asks: list[AreaAskDraft] = Field(default_factory=list, max_length=MAX_ASKS_PER_RUN)
    # One line the room shows as "what the agent did this run".
    report: str = Field(
        min_length=1,
        max_length=REPORT_MAX_CHARS,
        pattern=DISPLAY_LINE_PATTERN,
        description="One factual sentence naming the result, wait, or blocker from this check.",
    )
    # Self-paced heartbeat: when to look next (clamped by the area's
    # attention bounds) and the reason the user sees in the room.
    next_check_hours: float = Field(gt=0, le=24 * 14)
    next_check_reason: str = Field(
        min_length=1,
        max_length=NEXT_CHECK_REASON_MAX_CHARS,
        pattern=DISPLAY_LINE_PATTERN,
        description="One line naming the signal or deadline that makes the next check useful.",
    )


# Progress is the work graph moving: something created, finished, or dropped.
# A field edit or a page-prose rewrite is bookkeeping, not motion.
_PROGRESS_OPS = frozenset({"create", "complete", "cancel"})


class AreaCustodianReport(AreaAskNomination):
    outcome_changes: list[OutcomeChange] = Field(default_factory=list, max_length=20)
    work_changes: list[WorkChange] = Field(default_factory=list, max_length=30)
    evidence: list[EvidenceDraft] = Field(default_factory=list, max_length=30)
    work_remaining: bool
    continuation_minutes: int | None = Field(default=None, ge=5, le=240)
    continuation_reason: str | None = Field(
        default=None,
        min_length=1,
        max_length=NEXT_CHECK_REASON_MAX_CHARS,
        pattern=DISPLAY_LINE_PATTERN,
    )

    @property
    def made_progress(self) -> bool:
        """Earned, never claimed. As a self-reported flag this let a run rewrite
        page prose, reset the quiet streak, and dodge attention decay while
        emitting no asks — busy, tidy, and silent."""
        return any(change.op in _PROGRESS_OPS for change in (*self.outcome_changes, *self.work_changes))


def area_agent_instructions(area: Area) -> str:
    """The automation's description = the standing per-turn message. The
    fresh topic page is NOT embedded here — it arrives via the AREA system
    block on the area-tagged channel session, so every turn sees current
    state while these instructions stay static."""
    return (
        f"You are the standing custodian of the '{area.title}' area of the user's life. "
        f"Its topic page is in your AREA context block.\n"
        f"Autonomy contract ({area.autonomy}): {_CONTRACT[area.autonomy]}\n\n"
        "This turn: reconcile new evidence with CURRENT AREA WORK, then move the area "
        "forward. Execute multiple useful steps with tools; do not stop at describing or "
        "tracking work. Stop only after material progress, completion, or a genuine "
        "blocker. Update the topic page when its narrative should change, and return "
        "explicit outcome/work operations plus evidence. Never remove work by omission. "
        "For every non-create operation, copy its expected_updated_at value from CURRENT "
        "AREA WORK exactly.\n"
        "Originate; do not only mirror. A project emits no evidence until someone acts on "
        "it, so an active outcome with nothing incoming is stalled, not finished. When "
        "CONSECUTIVE QUIET RUNS is 2 or more and any outcome is still active, you must "
        "break the stall this turn: propose the next concrete step as a review ask, naming "
        "the exact artifact, command, or decision that would unblock it. Re-confirming that "
        "nothing has moved is not an acceptable result while work is open. Silence is "
        "correct only when nothing is open.\n"
        f"As your final action, call {AREA_REPORT_TOOL_NAME} exactly once with atomic "
        "outcome/work/evidence changes, findings triaged into at most three asks, "
        "whether work remains, and when to continue. Progress is not self-reported: it is "
        "read from the outcome/work operations you actually apply, so a run that only "
        "rewrites prose counts as quiet. "
        "Correct a rejected report and retry before ending. A short continuation is for "
        "executable work, not external waiting.\n"
        "Ask kinds — pick the one matching what you need back from the user:\n"
        "- notify: an FYI worth a glance; no decision needed. Expires on its own.\n"
        "- question: you are blocked on the user's judgment and cannot proceed without it.\n"
        "- review: you propose a concrete action and want approval before it happens. "
        "Set `action` to the task, written for an agent that will execute it with the "
        "user's own tools and cannot ask you anything: state the goal, the exact files or "
        "commands, and what done looks like. This is how work you cannot perform yourself "
        "gets done — approval runs it. Propose the action rather than waiting for the user "
        "to do it by hand.\n"
        "Every ask must name the concrete object, why now, and what happens next. "
        "Give it a short stable key reused across runs until that exact decision is resolved. "
        "Score each 1-5 on salience; low-salience findings belong on the page, not in an ask.\n"
        "Room display copy (schema limits are enforced): report is one factual sentence "
        "under 240 characters naming the concrete result, wait, or blocker — never a diary "
        "of tools or process. Ask text is a direct headline; why_now and what_next are one "
        "concrete sentence each. Work text names one observable action or blocker. Do not "
        "restate the topic or hedge with vague progress language. Next check: pick when you "
        "should genuinely look again and name the exact deadline, reply, or artifact signal "
        "in one line under 180 characters. After the report is accepted, end the run without "
        "another prose response."
    )


def render_work_context(snapshot: AreaWorkSnapshot) -> str:
    outcome_keys = {row.outcome_id: row.stable_key for row in snapshot.outcomes}
    work_keys = {row.item_id: row.stable_key for row in snapshot.work_items}
    recent_evidence = []
    for row in snapshot.events[:10]:
        if row.item_id in work_keys:
            target_type, target_key = "work", work_keys[row.item_id]
        elif row.outcome_id in outcome_keys:
            target_type, target_key = "outcome", outcome_keys[row.outcome_id]
        else:
            target_type, target_key = "area", None
        recent_evidence.append(
            {
                "target_type": target_type,
                "target_key": target_key,
                "event_type": row.event_type,
                "summary": row.summary,
                "source_refs": row.source_refs,
                "created_at": row.created_at,
            }
        )
    payload = {
        "outcomes": [
            {
                "key": row.stable_key,
                "title": row.title,
                "success_criteria": row.success_criteria,
                "status": row.status,
                "priority": row.priority,
                "source": row.source,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
                "completed_at": row.completed_at,
            }
            for row in snapshot.outcomes
        ],
        "work_items": [
            {
                "key": row.stable_key,
                "outcome_key": outcome_keys.get(row.outcome_id),
                "kind": row.kind,
                "text": row.text,
                "status": row.status,
                "owner": row.owner,
                "due_at": row.due_at,
                "next_attempt_at": row.next_attempt_at,
                "created_at": row.created_at,
                "updated_at": row.updated_at,
                "completed_at": row.completed_at,
            }
            for row in snapshot.work_items
        ],
        "recent_evidence": recent_evidence,
    }
    return "CURRENT AREA WORK (canonical structured state):\n" + json.dumps(payload, ensure_ascii=False)


@dataclass(frozen=True)
class CustodianContract:
    description: str
    tool_scope: ScopeKey
    auto_approve: bool = False


def is_custodian_task_id(task_id: str) -> bool:
    """Custodian runs are exactly `area:{id}`. Colon-suffixed children
    (`area:{id}:{slug}`) are ordinary automations — no cap, no intake, and
    the settable read_only/all lever instead of the curated contract."""
    suffix = task_id.removeprefix(CUSTODIAN_TASK_PREFIX)
    return task_id.startswith(CUSTODIAN_TASK_PREFIX) and ":" not in suffix


def custodian_contract(area: Area) -> CustodianContract:
    """Return the complete runtime permission contract for an Area.

    Tool policies remain the approval authority. In particular, acting mode
    expands the allowlist but never disables approvals globally.
    """
    return CustodianContract(
        description=area_agent_instructions(area),
        tool_scope="area_act" if area.autonomy == "act" else "area_observe",
    )


INTAKE_ADDENDUM = (
    "\n\nINTAKE (first run): you have no run history yet. This run, instead of routine "
    "tending: (1) read the topic page and recent area conversations, (2) write a short "
    "WATCHING section onto the topic page — the concrete things you will track and what "
    "you will never touch, (3) if one thing genuinely needs the user's calibration "
    "(a boundary, a priority, a missing fact), raise it as a single question ask, and "
    "(4) name the first concrete step that would move this area forward — raise it as a "
    "review ask if it is actionable now. Set your first next-check."
)


def record_area_run(
    asks: AskStore,
    area_key: str,
    page_path: str,
    report: AreaCustodianReport,
    run_ref: str,
) -> list[Ask]:
    """Sync the accepted report's asks from the outbox completion pipeline."""
    nominated = report.asks
    if not nominated:
        return []
    now = datetime.now(UTC)
    created: list[Ask] = []
    kept = [draft for draft in nominated if draft.salience >= SALIENCE_THRESHOLD]
    for n in kept[:MAX_ASKS_PER_RUN]:
        expires = (now + timedelta(hours=NOTIFY_ASK_TTL_HOURS)).isoformat() if n.kind == "notify" else None
        stable_key = n.key
        ask = Ask(
            id=f"agent:{area_key}:{stable_key}",
            area_key=area_key,
            text=n.text,
            kind=n.kind,
            source="agent",
            actions=[{"verb": "open_page", "ref": page_path}],
            state="active",
            created_at=now.isoformat(),
            provenance=run_ref,
            why_now=n.why_now,
            what_next=n.what_next,
            expires_at=expires,
            stable_key=stable_key,
            action=n.action,
        )
        if asks.upsert_agent_nomination(ask):
            created.append(ask)
    return created
