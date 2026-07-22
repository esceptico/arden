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
    description: str
    triggers: list[Trigger]
    handler: str
    enabled: bool = True
    auto_approve: bool = False
    cooldown_minutes: int | None = None


BUILTINS = [
    BuiltinSpec(
        task_id=BUILTIN_AREA_SUGGESTER_ID,
        name="Area Suggester",
        description="Scan memory topic pages for life domains worth a standing area agent",
        triggers=[
            TimeTrigger(at=AREA_SUGGESTER_DAILY_AT, days="daily"),
        ],
        handler="area_suggester_daily",
        auto_approve=True,
    ),
    BuiltinSpec(
        task_id=BUILTIN_AUTOMATION_SUGGESTER_DAILY_ID,
        name="Automation Suggester Daily",
        description="Draft contextual automation suggestions from memory, chats, and actions",
        triggers=[
            TimeTrigger(at=AUTOMATION_SUGGESTER_DAILY_AT, days="daily"),
        ],
        handler="automation_suggester_daily",
        auto_approve=True,
    ),
    BuiltinSpec(
        task_id=BUILTIN_MEMORY_CONSOLIDATE_ID,
        name="Memory Maintenance",
        description="Nightly sleep-time reconcile pass: merge duplicate records, supersede stale or contradicted ones, retype mis-classified records, fold near-duplicate labels, and prune tombstones from the canonical memory pool.",
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
        description="File-native synthesis: tag untagged records with their subject, then rewrite the prose summary of me.md, topic pages (topics/<slug>.md), active-work.md, integration source overviews (observations/<source>.md), and daily logs (daily/<date>.md) from the canonical timeline atoms, with inline (record:id) provenance. Stale-gated so only changed pages re-synthesize. Runs nightly AND after a burst of conversation so topic pages stay current, not 24h stale.",
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
        description="Nightly cross-domain reflection: derive the most salient questions spanning different topics, retrieve cross-topic evidence, and write up to 5 cited cross-domain insights back into memory.",
        triggers=[
            TimeTrigger(at=MEMORY_DREAM_AT, days="daily"),
        ],
        handler="memory_dream",
        auto_approve=True,
    ),
    BuiltinSpec(
        task_id=BUILTIN_MEMORY_RETENTION_ID,
        name="Memory Retention",
        description="Nightly deterministic retention: supersede integration observations older than 90 days, source lines older than 180 days, and fact/changelog lines older than 730 days; dreamer-authored insights age at the 180-day transient TTL. Pinned records, directives, and lessons are exempt.",
        triggers=[
            TimeTrigger(at=MEMORY_RETENTION_AT, days="daily"),
        ],
        handler="memory_retention",
        auto_approve=True,
    ),
]

_CURRENT_BUILTIN_IDS = {spec.task_id for spec in BUILTINS}
# Handlers we seed today, plus retired ones whose registration is gone — both
# must be swept so previously-seeded automations don't dangle on a missing
# handler. (pattern_finder/skill_inducer died with the claims+lens pipeline;
# integration_sync died with the observation ingest — feeds replaced it;
# slice_suggester_daily was renamed to area_suggester_daily in the areas rename.)
_RETIRED_HANDLERS = {
    "pattern_finder_daily",
    "skill_inducer_daily",
    "memory_publish",
    "integration_sync",
    "slice_suggester_daily",
}
_KNOWLEDGE_HANDLERS = {spec.handler for spec in BUILTINS} | _RETIRED_HANDLERS


async def seed_builtins(store: AutomationStore) -> None:
    for automation in await store.list_all():
        if (
            automation.builtin
            and automation.handler in _KNOWLEDGE_HANDLERS
            and automation.task_id not in _CURRENT_BUILTIN_IDS
        ):
            await store.delete(automation.task_id)
            _logger.info("Removed stale builtin automation: %s", automation.task_id)

    for spec in BUILTINS:
        existing = await store.get(spec.task_id)
        if existing:
            changes: dict = {}
            if existing.name != spec.name:
                changes["name"] = spec.name
            if existing.description != spec.description:
                changes["description"] = spec.description
            if existing.handler != spec.handler:
                changes["handler"] = spec.handler
            if existing.auto_approve != spec.auto_approve:
                changes["auto_approve"] = spec.auto_approve
            if existing.cooldown_minutes is None and spec.cooldown_minutes is not None:
                changes["cooldown_minutes"] = spec.cooldown_minutes
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
        )
        await store.save(automation)
        _logger.info("Seeded builtin automation: %s", spec.name)
