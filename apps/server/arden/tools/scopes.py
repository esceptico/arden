"""Named tool scopes.

Every automation carries a scope, but only as a *key*. The filter itself is
written here in Python and never persisted, so a stored scope contains no tool
names and a renamed tool cannot silently narrow it.

Two of these keys are the lever the UI and the agent may set on an automation —
`read_only` and `all`. The rest are fine-grained contracts the system assigns:
custodians and builtin workers get theirs from code, never from a user or a
model.
"""

from typing import Literal, get_args

from arden.memory.facts.capture import FACT_CAPTURE_REVIEW_TOOL_NAME
from arden.memory.facts.maintenance import FACT_MAINTENANCE_REVIEW_TOOL_NAME
from arden.tools.core.scope import ToolFilter, tools
from arden.wiki.constants import PUBLISH_WIKI_GENERATED_TOOL_NAME, WIKI_MAINTENANCE_REVIEW_TOOL_NAME

#: The only keys an automation's owner — a person or the agent — may choose.
#: The HTTP and tool schemas annotate with this directly, so the set of settable
#: scopes is declared once and cannot drift from what the API accepts.
SettableScopeKey = Literal["read_only", "all"]

ScopeKey = (
    SettableScopeKey
    | Literal[
        "area_observe",
        "area_act",
        "area_reply",
        "area_action",
        "fact_capture",
        "fact_maintenance",
        "fact_retention",
        "daily_notes",
        "wiki_maintenance",
        "wiki_producer",
    ]
)

SETTABLE_SCOPES: tuple[SettableScopeKey, ...] = get_args(SettableScopeKey)

AREA_REPORT_TOOL_NAME = "area_submit_report"

_AREA_OBSERVE = tools.read | tools.named(AREA_REPORT_TOOL_NAME) | tools.prefix("area_page_")

SCOPES: dict[ScopeKey, ToolFilter] = {
    "read_only": tools.read,
    "all": tools.ALL,
    # An area custodian reads anything and tends its own page. Acting adds the
    # child-automation tools; their own policies still gate consequential
    # writes, so acting is never an approval bypass.
    "area_observe": _AREA_OBSERVE,
    "area_act": (
        _AREA_OBSERVE
        | tools.named("automation_create")
        | tools.named("automation_list")
        | tools.named("automation_result")
        | tools.named("area_run_automation")
    ),
    # Answering the user is a conversation, not a custodian run: same reach,
    # minus the terminal report only a scheduled run may submit. The narrowing
    # `~` exists for this.
    "area_reply": _AREA_OBSERVE & ~tools.named(AREA_REPORT_TOOL_NAME),
    # Carrying out a proposal the user approved. This reaches past the
    # custodian's standing scope on purpose — an observing custodian cannot
    # perform what it proposes — but only inside Arden. Reaching an outside
    # service is a separate consent from "yes, do this", so integrations stay
    # out and an action needing one fails here instead of quietly escalating.
    "area_action": _AREA_OBSERVE | tools.system,
    # Builtin workers run one tool to completion and nothing else.
    "fact_capture": tools.named(FACT_CAPTURE_REVIEW_TOOL_NAME),
    "fact_maintenance": tools.named(FACT_MAINTENANCE_REVIEW_TOOL_NAME),
    "wiki_maintenance": tools.named(WIKI_MAINTENANCE_REVIEW_TOOL_NAME),
    "fact_retention": (
        tools.named("fact_search")
        | tools.named("fact_get")
        | tools.named("fact_history")
        | tools.named("fact_due_reviews")
        | tools.named("fact_plan_changes")
        | tools.named("fact_commit_changes")
    ),
    "daily_notes": tools.read | tools.named("wiki_create_page") | tools.named("wiki_edit_page"),
    # Publishes its own generated wiki region; completion is proof-checked in
    # AutomationRuntime._validate_completed_run.
    "wiki_producer": tools.read | tools.named(PUBLISH_WIKI_GENERATED_TOOL_NAME),
}


def resolve(key: str) -> ToolFilter:
    scope = SCOPES.get(key)
    if scope is None:
        raise ValueError(f"unknown tool scope {key!r}; valid: {sorted(SCOPES)}")
    return scope


assert set(SCOPES) == {key for member in get_args(ScopeKey) for key in get_args(member)}, "SCOPES and ScopeKey disagree"
