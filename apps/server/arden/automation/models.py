from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from arden.automation.triggers import Trigger
from arden.core.public_refs import is_public_ref, public_ref
from arden.tools.scopes import ScopeKey

AutomationKind = Literal["automation", "loop"]
AutomationDescriptionSource = Literal["manual", "generated"]


@dataclass
class Automation:
    task_id: str
    automation_ref: str
    name: str
    prompt: str
    model: str | None
    triggers: list[Trigger]
    enabled: bool
    created_at: datetime
    next_run_at: datetime | None
    last_run_at: datetime | None
    last_result: str | None
    running_since: datetime | None
    auto_approve: bool
    handler: str | None = None
    builtin: bool = False
    cooldown_minutes: int | None = None
    kind: AutomationKind = "automation"
    max_iterations: int | None = None
    iteration_count: int = 0
    stop_when: str | None = None
    max_age_days: int | None = None
    thread_id: str | None = None
    read_history: bool = False
    # Which tools this automation's runs may use — a key into arden.tools.scopes,
    # never a tool list. The UI and the agent may only set "read_only" or "all";
    # custodians and builtin workers get a fine-grained key from code.
    tool_scope: ScopeKey = "read_only"
    parent_automation_id: str | None = None
    idempotency_key: str | None = None
    idempotency_scope: str | None = None
    # Display copy and executable instructions are separate contracts. A
    # migrated row may intentionally have no display description until the
    # user asks the configured auxiliary model to generate one.
    description: str | None = None
    description_source: AutomationDescriptionSource | None = None
    # "manual" marks a user-edited trigger list; builtin seeding then leaves
    # the triggers alone instead of resetting them to the code defaults.
    triggers_source: str | None = None

    def __post_init__(self) -> None:
        if not is_public_ref(self.automation_ref):
            raise ValueError("automation_ref must be an exact public reference")

    def in_cooldown(self, now: datetime) -> bool:
        if not self.cooldown_minutes or not self.last_run_at:
            return False
        return (now - self.last_run_at) < timedelta(minutes=self.cooldown_minutes)

    def aged_out(self, now: datetime) -> bool:
        if not self.max_age_days:
            return False
        return (now - self.created_at) >= timedelta(days=self.max_age_days)


@dataclass(frozen=True)
class IdempotencyClaim:
    claim_id: str
    scope: str
    key: str
    parent_automation_id: str | None
    parent_fire_at: str | None
    attempt_n: int | None
    claimed_at: datetime
    automation_task_id: str | None


def create_automation_ref(name: str, task_id: str) -> str:
    """Create the immutable model-facing ref for a new automation."""

    normalized_name = name.strip()
    if not normalized_name:
        raise ValueError("automation name cannot be blank")
    return public_ref(normalized_name, task_id, empty_slug="automation")
