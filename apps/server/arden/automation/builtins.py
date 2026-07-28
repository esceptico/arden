from dataclasses import dataclass
from dataclasses import replace as dc_replace
from datetime import UTC, datetime

from arden.automation.models import Automation
from arden.automation.store import AutomationStore
from arden.automation.triggers import CountTrigger, TimeTrigger, Trigger
from arden.constants import (
    AREA_SUGGESTER_DAILY_AT,
    AUTOMATION_SUGGESTER_DAILY_AT,
    BUILTIN_AREA_SUGGESTER_ID,
    BUILTIN_AUTOMATION_SUGGESTER_DAILY_ID,
    BUILTIN_MEMORY_CONSOLIDATE_ID,
    BUILTIN_MEMORY_DREAM_ID,
    BUILTIN_MEMORY_RETENTION_ID,
    BUILTIN_MEMORY_SYNTHESIZE_ID,
    MEMORY_CONSOLIDATE_AT,
    MEMORY_DREAM_AT,
    MEMORY_RETENTION_AT,
    MEMORY_SYNTHESIZE_AT,
    MEMORY_SYNTHESIZE_COOLDOWN_MINUTES,
    MEMORY_SYNTHESIZE_EVERY_N_RUNS,
)
from arden.logging import get_logger

_logger = get_logger(__name__)


@dataclass
class BuiltinSpec:
    task_id: str
    name: str
    # The mockup renders `summary` separately from `prompt`; keep the same
    # contract for code-owned automation rows.
    description: str
    prompt: str
    triggers: list[Trigger]
    handler: str | None
    enabled: bool = True
    auto_approve: bool = False
    cooldown_minutes: int | None = None
    tool_scope: list[str] | None = None


_FACT_RETENTION_TOOL_SCOPE = [
    "search_facts",
    "get_fact",
    "get_fact_history",
    "get_due_fact_reviews",
    "plan_fact_changes",
    "commit_fact_changes",
]

_FACT_RETENTION_PROMPT = (
    "Review only canonical facts that are due for lifecycle review. Start with "
    "get_due_fact_reviews, then inspect any candidate facts and their saved "
    "provenance before acting. Never treat silence or age alone as evidence. "
    "Only confirm, supersede, or expire when the available evidence "
    "supports that exact decision, and pass its exact fact IDs as "
    "evidence_fact_ids. An expiry may cite the due fact's own explicit expiry. "
    "Otherwise keep the fact active, explicitly mark it uncertain, and "
    "schedule another review. Do not create facts, edit wiki "
    "pages, hard-delete history, or use tools outside this fact workflow. Plan "
    "every change first and commit only the returned plan."
)


BUILTINS = [
    BuiltinSpec(
        task_id=BUILTIN_AREA_SUGGESTER_ID,
        name="Area Suggester",
        description="Find life domains that could benefit from a standing area agent.",
        prompt="Scan memory topic pages for life domains worth a standing area agent.",
        triggers=[
            TimeTrigger(at=AREA_SUGGESTER_DAILY_AT, days="daily"),
        ],
        handler="area_suggester_daily",
        auto_approve=True,
    ),
    BuiltinSpec(
        task_id=BUILTIN_AUTOMATION_SUGGESTER_DAILY_ID,
        name="Automation Suggester Daily",
        description="Draft relevant automation suggestions from current context.",
        prompt="Draft contextual automation suggestions from memory, chats, and actions.",
        triggers=[
            TimeTrigger(at=AUTOMATION_SUGGESTER_DAILY_AT, days="daily"),
        ],
        handler="automation_suggester_daily",
        auto_approve=True,
    ),
    BuiltinSpec(
        task_id=BUILTIN_MEMORY_CONSOLIDATE_ID,
        name="Memory Maintenance",
        description="Consolidate duplicate, stale, and contradicted memory records.",
        prompt="Nightly sleep-time reconcile pass: merge duplicate records, supersede stale or contradicted ones, retype mis-classified records, fold near-duplicate labels, and prune tombstones from the canonical memory pool.",
        triggers=[
            TimeTrigger(at=MEMORY_CONSOLIDATE_AT, days="daily"),
            CountTrigger(every_n=MEMORY_SYNTHESIZE_EVERY_N_RUNS),
        ],
        handler="memory_consolidate",
        auto_approve=True,
        cooldown_minutes=MEMORY_SYNTHESIZE_COOLDOWN_MINUTES,
    ),
    BuiltinSpec(
        task_id=BUILTIN_MEMORY_SYNTHESIZE_ID,
        name="Memory Synthesis",
        description="Refresh memory pages from canonical records with provenance.",
        prompt="File-native synthesis: tag untagged records with their subject, then rewrite the prose summary of me.md, topic pages (topics/<slug>.md), active-work.md, integration source overviews (observations/<source>.md), and daily logs (daily/<date>.md) from the canonical timeline atoms, with inline (record:id) provenance. Stale-gated so only changed pages re-synthesize. Runs nightly AND after a burst of conversation so topic pages stay current, not 24h stale.",
        triggers=[
            TimeTrigger(at=MEMORY_SYNTHESIZE_AT, days="daily"),
            CountTrigger(every_n=MEMORY_SYNTHESIZE_EVERY_N_RUNS),
        ],
        handler="memory_synthesize",
        auto_approve=True,
        cooldown_minutes=MEMORY_SYNTHESIZE_COOLDOWN_MINUTES,
    ),
    BuiltinSpec(
        task_id=BUILTIN_MEMORY_DREAM_ID,
        name="Memory Dream",
        description="Derive cited cross-domain insights from memory.",
        prompt="Nightly cross-domain reflection: derive the most salient questions spanning different topics, retrieve cross-topic evidence, and write up to 5 cited cross-domain insights back into memory.",
        triggers=[
            TimeTrigger(at=MEMORY_DREAM_AT, days="daily"),
        ],
        handler="memory_dream",
        auto_approve=True,
    ),
    BuiltinSpec(
        task_id=BUILTIN_MEMORY_RETENTION_ID,
        name="Memory Retention",
        description="Apply retention rules to stale transient memory records.",
        prompt="Nightly deterministic retention: supersede integration observations older than 90 days, source lines older than 180 days, and fact/changelog lines older than 730 days; dreamer-authored insights age at the 180-day transient TTL. Pinned records, directives, and lessons are exempt.",
        triggers=[
            TimeTrigger(at=MEMORY_RETENTION_AT, days="daily"),
        ],
        handler="memory_retention",
        auto_approve=True,
    ),
]


def _specs(*, fact_mode: bool) -> list[BuiltinSpec]:
    if not fact_mode:
        return BUILTINS
    return [
        dc_replace(
            spec,
            handler=None,
            prompt=_FACT_RETENTION_PROMPT,
            tool_scope=list(_FACT_RETENTION_TOOL_SCOPE),
        )
        if spec.task_id == BUILTIN_MEMORY_RETENTION_ID
        else spec
        for spec in BUILTINS
    ]


async def seed_builtins(store: AutomationStore, *, fact_mode: bool = False) -> None:
    for spec in _specs(fact_mode=fact_mode):
        existing = await store.get(spec.task_id)
        if existing:
            changes: dict = {}
            if existing.name != spec.name:
                changes["name"] = spec.name
            # Display copy can have been written manually or generated after
            # migration. Keep that concise copy stable across restarts; only
            # seed it when this row has no display description at all.
            if existing.description is None:
                changes["description"] = spec.description
                changes["description_source"] = "manual"
            elif existing.description_source is None:
                changes["description_source"] = "manual"
            if existing.prompt != spec.prompt:
                changes["prompt"] = spec.prompt
            if existing.handler != spec.handler:
                changes["handler"] = spec.handler
            if existing.auto_approve != spec.auto_approve:
                changes["auto_approve"] = spec.auto_approve
            if existing.cooldown_minutes is None and spec.cooldown_minutes is not None:
                changes["cooldown_minutes"] = spec.cooldown_minutes
            if spec.task_id == BUILTIN_MEMORY_RETENTION_ID and existing.tool_scope != spec.tool_scope:
                changes["tool_scope"] = None if spec.tool_scope is None else list(spec.tool_scope)
            # Triggers and enabled are the USER'S dials once the row exists —
            # re-stamping them every boot silently reverted cadence edits and
            # re-enabled paused builtins. Spec values apply on first seed only.
            time_triggers = [t for t in existing.triggers if isinstance(t, TimeTrigger)]
            if existing.enabled and existing.next_run_at is None and time_triggers:
                changes["next_run_at"] = time_triggers[0].next_run(datetime.now(UTC))
            if changes:
                updated = dc_replace(existing, **changes)
                await store.update_metadata(updated)
                _logger.info("Updated builtin automation defaults: %s", spec.name)
            continue

        now = datetime.now(UTC)
        time_triggers = [t for t in spec.triggers if isinstance(t, TimeTrigger)]
        automation = Automation(
            task_id=spec.task_id,
            name=spec.name,
            description=spec.description,
            description_source="manual",
            prompt=spec.prompt,
            model=None,
            triggers=spec.triggers,
            enabled=spec.enabled,
            created_at=now,
            next_run_at=time_triggers[0].next_run(now) if spec.enabled and time_triggers else None,
            last_run_at=None,
            last_result=None,
            running_since=None,
            auto_approve=spec.auto_approve,
            handler=spec.handler,
            builtin=True,
            cooldown_minutes=spec.cooldown_minutes,
            tool_scope=None if spec.tool_scope is None else list(spec.tool_scope),
        )
        await store.save(automation)
        _logger.info("Seeded builtin automation: %s", spec.name)
