from dataclasses import dataclass
from dataclasses import replace as dc_replace
from datetime import UTC, datetime

from arden.automation.models import Automation
from arden.automation.store import AutomationStore
from arden.automation.triggers import TimeTrigger, Trigger
from arden.constants import (
    BUILTIN_MEMORY_CONSOLIDATE_ID,
    BUILTIN_MEMORY_DREAM_ID,
    BUILTIN_MEMORY_RETENTION_ID,
    BUILTIN_MEMORY_SYNTHESIZE_ID,
    BUILTIN_WIKI_MAINTENANCE_ID,
    MEMORY_CONSOLIDATE_AT,
    MEMORY_DREAM_AT,
    MEMORY_RETENTION_AT,
    RETIRED_BUILTIN_AUTOMATION_IDS,
)
from arden.logging import get_logger
from arden.memory.facts.maintenance import FACT_MAINTENANCE_REVIEW_TOOL_NAME
from arden.wiki.constants import WIKI_MAINTENANCE_REVIEW_TOOL_NAME

_logger = get_logger(__name__)


@dataclass
class BuiltinSpec:
    task_id: str
    name: str
    description: str
    prompt: str
    triggers: list[Trigger]
    handler: str | None
    enabled: bool = True
    auto_approve: bool = False
    cooldown_minutes: int | None = None
    tool_scope: list[str] | None = None
    uses_memory_model: bool = False


_FACT_RETENTION_TOOL_SCOPE = [
    "search_facts",
    "get_fact",
    "get_fact_history",
    "get_due_fact_reviews",
    "plan_fact_changes",
    "commit_fact_changes",
]
_FACT_RETENTION_DESCRIPTION = "Review temporary canonical facts when their lifecycle review is due."
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
_FACT_MAINTENANCE_DESCRIPTION = "Reconcile duplicate and misclassified canonical facts."
FACT_MAINTENANCE_TOOL_SCOPE = [FACT_MAINTENANCE_REVIEW_TOOL_NAME]
FACT_MAINTENANCE_PROMPT = (
    "Run Memory Maintenance to completion. Start with fact_maintenance_review action='next'. "
    "For each prepared, version-pinned cluster, merge only genuine duplicates, correct only "
    "kind, labels, subjects, lifecycle, or evidence class, and choose no_change whenever the "
    "evidence is weak or ambiguous. Never create or rewrite fact text, manage age-based expiry, "
    "perform general supersession, edit wiki prose, or let inferred evidence replace direct "
    "evidence. If the tool rejects a decision, correct that same cluster and retry. Continue "
    "until the tool reports completion; never use another tool or stop early."
)
_FACT_SYNTHESIS_DESCRIPTION = "Publish fact-backed wiki pages from canonical facts."
_FACT_SYNTHESIS_PROMPT = (
    "Canonical fact synthesis: publish the managed regions of wiki pages from "
    "active confirmed canonical facts with exact fact citations. Preserve user "
    "content outside managed regions, never invent facts, and leave unresolved "
    "or uncertain facts out of publication."
)
_WIKI_MAINTENANCE_DESCRIPTION = "Reconcile cross-page wiki consistency from managed revision evidence."
WIKI_MAINTENANCE_TOOL_SCOPE = [WIKI_MAINTENANCE_REVIEW_TOOL_NAME]
WIKI_MAINTENANCE_PROMPT = (
    "Run Wiki Maintenance to completion. Start with wiki_maintenance_review action='next'. "
    "For each prepared, version-pinned report, preserve user intent and use only the supplied evidence. "
    "Choose no_change unless a targeted title, alias, or ordinary body edit is necessary for cross-page consistency. "
    "Never do any of the following: invent facts or citations, read raw facts, rename, move, archive, merge, "
    "redirect, or edit generated regions. "
    "Use needs_review when a durable user decision is required. If the tool rejects a decision, correct that same report "
    "and retry. Continue until the tool reports completion; never use another tool or stop early."
)

BUILTINS = [
    BuiltinSpec(
        task_id=BUILTIN_MEMORY_CONSOLIDATE_ID,
        name="Memory Maintenance",
        description=_FACT_MAINTENANCE_DESCRIPTION,
        prompt=FACT_MAINTENANCE_PROMPT,
        triggers=[TimeTrigger(at=MEMORY_CONSOLIDATE_AT, days="daily")],
        handler="memory_maintenance",
        auto_approve=True,
        tool_scope=list(FACT_MAINTENANCE_TOOL_SCOPE),
    ),
    BuiltinSpec(
        task_id=BUILTIN_MEMORY_SYNTHESIZE_ID,
        name="Memory Synthesis",
        description=_FACT_SYNTHESIS_DESCRIPTION,
        prompt=_FACT_SYNTHESIS_PROMPT,
        triggers=[TimeTrigger(every="6h")],
        handler="memory_synthesize",
        auto_approve=True,
    ),
    BuiltinSpec(
        task_id=BUILTIN_MEMORY_RETENTION_ID,
        name="Memory Retention",
        description=_FACT_RETENTION_DESCRIPTION,
        prompt=_FACT_RETENTION_PROMPT,
        triggers=[TimeTrigger(at=MEMORY_RETENTION_AT, days="daily")],
        handler=None,
        auto_approve=True,
        tool_scope=list(_FACT_RETENTION_TOOL_SCOPE),
        uses_memory_model=True,
    ),
    BuiltinSpec(
        task_id=BUILTIN_WIKI_MAINTENANCE_ID,
        name="Wiki Maintenance",
        description=_WIKI_MAINTENANCE_DESCRIPTION,
        prompt=WIKI_MAINTENANCE_PROMPT,
        triggers=[TimeTrigger(every="6h")],
        handler="wiki_maintenance",
        auto_approve=True,
        tool_scope=list(WIKI_MAINTENANCE_TOOL_SCOPE),
    ),
    BuiltinSpec(
        task_id=BUILTIN_MEMORY_DREAM_ID,
        name="Memory Dream",
        description="Publish provisional, cited cross-domain insights from canonical facts.",
        prompt=(
            "Publish a small number of provisional cross-domain insights from direct canonical facts. "
            "Keep each insight grounded in exact cited evidence and never treat it as a canonical fact."
        ),
        triggers=[TimeTrigger(at=MEMORY_DREAM_AT, days="daily")],
        handler="memory_dream",
        enabled=False,
        auto_approve=True,
    ),
]


async def seed_builtins(store: AutomationStore, *, memory_model: str) -> None:
    for task_id in RETIRED_BUILTIN_AUTOMATION_IDS:
        existing = await store.get(task_id)
        if existing is not None and existing.builtin:
            await store.delete(task_id)
            _logger.info("Removed retired builtin automation: %s", existing.name)

    for spec in BUILTINS:
        desired_model = memory_model if spec.uses_memory_model else None
        existing = await store.get(spec.task_id)
        if existing:
            changes: dict = {}
            if existing.name != spec.name:
                changes["name"] = spec.name
            if existing.description != spec.description:
                changes["description"] = spec.description
                changes["description_source"] = "manual"
            elif existing.description_source != "manual":
                changes["description_source"] = "manual"
            if existing.prompt != spec.prompt:
                changes["prompt"] = spec.prompt
            if existing.model != desired_model:
                changes["model"] = desired_model
            if existing.handler != spec.handler:
                changes["handler"] = spec.handler
            if existing.auto_approve != spec.auto_approve:
                changes["auto_approve"] = spec.auto_approve
            if existing.triggers != spec.triggers:
                changes["triggers"] = list(spec.triggers)
            if existing.cooldown_minutes != spec.cooldown_minutes:
                changes["cooldown_minutes"] = spec.cooldown_minutes
            if existing.tool_scope != spec.tool_scope:
                changes["tool_scope"] = None if spec.tool_scope is None else list(spec.tool_scope)
            # Enabled remains the user's pause control. Timing belongs to the
            # system phase contract.
            time_triggers = [trigger for trigger in spec.triggers if isinstance(trigger, TimeTrigger)]
            if existing.enabled and time_triggers:
                canonical_next_run = time_triggers[0].next_run(existing.last_run_at or existing.created_at)
                if existing.next_run_at != canonical_next_run:
                    changes["next_run_at"] = canonical_next_run
            if changes:
                await store.update_metadata(dc_replace(existing, **changes))
                _logger.info("Updated builtin automation defaults: %s", spec.name)
            continue

        now = datetime.now(UTC)
        time_triggers = [trigger for trigger in spec.triggers if isinstance(trigger, TimeTrigger)]
        await store.save(
            Automation(
                task_id=spec.task_id,
                name=spec.name,
                description=spec.description,
                description_source="manual",
                prompt=spec.prompt,
                model=desired_model,
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
        )
        _logger.info("Seeded builtin automation: %s", spec.name)
