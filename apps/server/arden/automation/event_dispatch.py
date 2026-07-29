from dataclasses import dataclass
from datetime import datetime, timedelta

from arden.automation.models import Automation
from arden.automation.store import AutomationStore
from arden.automation.triggers import EventTrigger, MessageTrigger
from arden.constants import (
    MESSAGE_RECEIVED,
    SCHEDULER_DEDUP_TTL,
    SCHEDULER_EVENT_MAX_RETRIES,
    SCHEDULER_EVENT_RETRY_BASE_SECONDS,
    SCHEDULER_EVENT_RETRY_MAX_SECONDS,
)
from arden.events.triggers import EVENT_APPROACHING, EventApproaching, MessageReceived, TriggerEvent


@dataclass(frozen=True)
class QueuedEvent:
    """One durably claimed event, ready for a scheduler-owned execution."""

    queue_id: int
    context: str | dict | None
    attempt_count: int


async def enqueue_matching_events(store: AutomationStore, event: TriggerEvent, now: datetime) -> list[str]:
    """Deduplicate, match, and durably enqueue an incoming trigger event."""
    cutoff = now - timedelta(seconds=SCHEDULER_DEDUP_TTL)
    await store.evict_event_claims_older_than(cutoff)
    task_ids: list[str] = []
    for automation in await matching_event_automations(store, event):
        if not event_matches_automation(automation, event):
            continue
        if await store.claim_and_enqueue_event(automation.task_id, event.event_key, event.format_context(), now):
            task_ids.append(automation.task_id)
    return task_ids


async def matching_event_automations(store: AutomationStore, event: TriggerEvent) -> list[Automation]:
    if event.event_type == MESSAGE_RECEIVED and isinstance(event, MessageReceived):
        return await matching_message_automations(store, event)
    return await store.list_event_triggered(event.event_type)


async def matching_message_automations(store: AutomationStore, event: MessageReceived) -> list[Automation]:
    matched: list[Automation] = []
    for automation in await store.list_message_triggered(event.source, event.channel_id):
        triggers = [
            trigger
            for trigger in automation.triggers
            if isinstance(trigger, MessageTrigger)
            and trigger.source == event.source
            and event.channel_id in trigger.channel_ids
        ]
        if any(message_trigger_passes(trigger, event) for trigger in triggers):
            matched.append(automation)
    return matched


def event_matches_automation(automation: Automation, event: TriggerEvent) -> bool:
    if event.event_type != EVENT_APPROACHING or not isinstance(event, EventApproaching):
        return True
    matching_triggers = [
        trigger
        for trigger in automation.triggers
        if isinstance(trigger, EventTrigger) and trigger.event_type == EVENT_APPROACHING
    ]
    if not matching_triggers:
        return True
    lead_minutes = matching_triggers[0].lead_minutes
    return lead_minutes is None or event.minutes_until <= int(lead_minutes)


def message_trigger_passes(trigger: MessageTrigger, event: MessageReceived) -> bool:
    if trigger.from_user_id is not None and trigger.from_user_id != event.user_id:
        return False
    if trigger.contains:
        text = event.text.lower()
        if not any(keyword.lower() in text for keyword in trigger.contains):
            return False
    return True


async def claim_next_queued_event(store: AutomationStore, task_id: str, now: datetime) -> QueuedEvent | None:
    claimed = await store.claim_next_event(task_id, now)
    if claimed is None:
        return None
    queue_id, context, attempt_count = claimed
    return QueuedEvent(queue_id=queue_id, context=context, attempt_count=attempt_count)


async def failure_retry_at(store: AutomationStore, task_id: str, now: datetime) -> datetime:
    """Back off failed task executions without advancing their cadence."""
    failures = 0
    for run in await store.list_runs(task_id, limit=SCHEDULER_EVENT_MAX_RETRIES):
        if run["status"] == "running":
            continue
        if run["status"] != "failed":
            break
        failures += 1
    return now + timedelta(seconds=retry_delay_seconds(failures))


def event_retry_at(attempt_count: int, now: datetime) -> datetime | None:
    if attempt_count + 1 >= SCHEDULER_EVENT_MAX_RETRIES:
        return None
    return now + timedelta(seconds=retry_delay_seconds(attempt_count))


def retry_delay_seconds(attempt_count: int) -> int:
    backoff = SCHEDULER_EVENT_RETRY_BASE_SECONDS * (2 ** max(attempt_count, 0))
    return min(SCHEDULER_EVENT_RETRY_MAX_SECONDS, backoff)
