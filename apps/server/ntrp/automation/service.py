from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import Any

from coolname import generate_slug

from ntrp.automation.models import Automation
from ntrp.automation.output_schemas import resolve_output_schema
from ntrp.automation.scheduler import Scheduler
from ntrp.automation.store import AutomationStore
from ntrp.automation.triggers import MessageTrigger, TimeTrigger, Trigger, build_trigger, parse_one
from ntrp.context.models import SessionState
from ntrp.integrations.slack.client import SlackClient
from ntrp.llm.models import get_models
from ntrp.services.session import SessionService


def _build_trigger_and_next_run(
    *,
    trigger_type: str | None,
    at: str | None,
    days: str | None,
    every: str | None,
    event_type: str | None,
    lead_minutes: int | str | None,
    start: str | None,
    end: str | None,
    idle_minutes: int | None,
    every_n: int | None,
    triggers: list[dict] | None,
) -> tuple[list[Trigger], datetime | None]:
    if triggers:
        parsed_triggers = [trigger for t in triggers if (trigger := parse_one(t)) is not None]
        time_triggers = [t for t in parsed_triggers if isinstance(t, TimeTrigger)]
        next_run = time_triggers[0].next_run(datetime.now(UTC)) if time_triggers else None
        return parsed_triggers, next_run
    if trigger_type:
        trigger, next_run = build_trigger(
            trigger_type,
            at=at,
            days=days,
            every=every,
            event_type=event_type,
            lead_minutes=lead_minutes,
            start=start,
            end=end,
            idle_minutes=idle_minutes,
            every_n=every_n,
        )
        return [trigger], next_run
    raise ValueError("Either 'triggers' list or 'trigger_type' is required")


def _normalize_and_validate_model(model: str | None) -> str | None:
    if model is None:
        return None
    normalized = model.strip()
    if not normalized:
        return None
    available = get_models()
    if normalized not in available:
        raise ValueError(f"Unknown model: {normalized}")
    return normalized


@dataclass(frozen=True)
class TriggerPatch:
    trigger_type: str | None = None
    at: str | None = None
    days: str | None = None
    every: str | None = None
    event_type: str | None = None
    lead_minutes: int | str | None = None
    start: str | None = None
    end: str | None = None
    idle_minutes: int | None = None
    every_n: int | None = None

    @property
    def has_changes(self) -> bool:
        return any(v is not None for v in asdict(self).values())

    @property
    def overrides(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("trigger_type", None)
        return {k: v for k, v in d.items() if v is not None}


class AutomationService:
    def __init__(
        self,
        store: AutomationStore,
        scheduler: Scheduler,
        session_service: SessionService,
        get_slack_client: Callable[[], SlackClient | None] | None = None,
    ):
        self.store = store
        self.scheduler = scheduler
        self.session_service = session_service
        # Resolves the live Slack client from the integration registry
        # (mirrors get_calendar_source wiring). Lets save-time message-trigger
        # resolution reach Slack without a new global; None until Slack connects.
        self._get_slack_client = get_slack_client

    def _slack(self) -> SlackClient:
        client = self._get_slack_client() if self._get_slack_client else None
        if client is None:
            raise ValueError("Slack is not connected — cannot create a Slack message trigger")
        return client

    async def _resolve_message_trigger(self, payload: dict) -> MessageTrigger:
        """Resolve a name-based message-trigger payload to stored IDs.

        Editor sends {channel, from_user?, contains?}; names are stale, so we
        pin to Slack IDs at save time and keep the display names for the UI.
        """
        source = payload.get("source", "slack")
        if source != "slack":
            raise ValueError(f"Unsupported message trigger source: {source!r}")

        slack = self._slack()
        raw_channels = payload.get("channels")
        if isinstance(raw_channels, str):
            raw_channels = [raw_channels]
        channels: list[dict] = []
        for channel_name in raw_channels or []:
            cid, cname = await slack.resolve_channel(channel_name)
            channels.append({"id": cid, "name": cname})
        if not channels:
            raise ValueError("a Slack message trigger needs at least one channel")

        from_user_id: str | None = None
        from_user_name: str | None = None
        if from_user := payload.get("from_user"):
            resolved = await slack.resolve_user(from_user)
            if not isinstance(resolved, dict):
                names = ", ".join(f"{c['name']} (@{c['username']})" for c in resolved) or "none"
                raise ValueError(f"Ambiguous Slack user {from_user!r}; candidates: {names}")
            from_user_id = resolved["id"]
            from_user_name = resolved["name"]

        return MessageTrigger(
            source=source,
            channels=channels,
            from_user_id=from_user_id,
            from_user_name=from_user_name,
            contains=payload.get("contains") or [],
        )

    async def _resolve_message_triggers(self, triggers: list[dict] | None) -> list[dict] | None:
        """Replace name-based message-trigger dicts with resolved, ID-form dicts
        so the shared parse/build path stores Slack IDs."""
        if not triggers:
            return triggers
        resolved: list[dict] = []
        for t in triggers:
            if t.get("type") == "message":
                trigger = await self._resolve_message_trigger(t)
                resolved.append({"type": "message", **trigger.params()})
            else:
                resolved.append(t)
        return resolved

    async def _provision_channel(
        self, name: str, task_id: str, area_id: str | None = None
    ) -> SessionState:
        """Create the durable channel session that owns an automation's
        activity. SessionService.provision announces it (SESSION_CREATED) so
        connected desktops add the sidebar row live instead of after reload."""
        return await self.session_service.provision(
            name=name,
            session_type="channel",
            origin_automation_id=task_id,
            area_id=area_id,
        )

    @property
    def is_running(self) -> bool:
        return self.scheduler.is_running

    async def list_all(self) -> list[Automation]:
        return await self.store.list_all()

    async def get(self, task_id: str) -> Automation:
        if not (task := await self.store.get(task_id)):
            raise KeyError(f"Automation {task_id} not found")
        return task

    async def toggle_enabled(self, task_id: str) -> bool:
        task = await self.get(task_id)
        new_enabled = not task.enabled
        await self.store.set_enabled(task_id, new_enabled)
        return new_enabled

    async def toggle_auto_approve(self, task_id: str) -> bool:
        task = await self.get(task_id)
        new_auto_approve = not task.auto_approve
        await self.store.set_auto_approve(task_id, new_auto_approve)
        return new_auto_approve

    async def run_now(self, task_id: str) -> None:
        if not self.scheduler.is_running:
            raise RuntimeError("Scheduler not running")
        await self.get(task_id)
        self.scheduler.schedule_run(task_id)

    def _build_metadata_changes(
        self,
        *,
        name: str | None,
        description: str | None,
        auto_approve: bool | None,
        enabled: bool | None,
        model: str | None,
        cooldown_minutes: int | None = None,
        max_iterations: int | None = None,
        stop_when: str | None = None,
        max_age_days: int | None = None,
        tool_scope: list[str] | None = None,
        output_schema: str | None = None,
    ) -> dict[str, Any]:
        changes: dict[str, Any] = {}
        if name is not None:
            changes["name"] = name
        if description is not None:
            changes["description"] = description
        if auto_approve is not None:
            changes["auto_approve"] = auto_approve
        if enabled is not None:
            changes["enabled"] = enabled
        if model is not None:
            changes["model"] = _normalize_and_validate_model(model)
        if cooldown_minutes is not None:
            changes["cooldown_minutes"] = cooldown_minutes
        if max_iterations is not None:
            changes["max_iterations"] = max_iterations
        if stop_when is not None:
            changes["stop_when"] = stop_when
        if max_age_days is not None:
            changes["max_age_days"] = max_age_days
        if tool_scope is not None:
            # [] clears the scope back to unrestricted; a non-empty list replaces it.
            changes["tool_scope"] = tool_scope or None
        if output_schema is not None:
            # "" clears it; a name must exist in the registry.
            resolve_output_schema(output_schema or None)
            changes["output_schema"] = output_schema or None
        return changes

    @staticmethod
    def _build_updated_trigger(current: Trigger, patch: TriggerPatch) -> tuple[Trigger, datetime | None] | None:
        if not patch.has_changes:
            return None

        effective_type = patch.trigger_type or current.type

        # Keep current params when staying in same type; blank slate on type switch.
        base = current.params() if effective_type == current.type else {}
        merged = {**base, **patch.overrides}

        # Schedule (at) and interval (every) are mutually exclusive within time triggers.
        if effective_type == "time":
            if patch.every is not None:
                merged.pop("at", None)
            elif patch.at is not None:
                for k in ("every", "start", "end"):
                    merged.pop(k, None)
        elif effective_type == "event":
            time_fields = {"at", "every", "days", "start", "end"} & patch.overrides.keys()
            if time_fields:
                raise ValueError(f"Time fields ({', '.join(sorted(time_fields))}) cannot be set on an event trigger")
        elif effective_type in {"idle", "count"}:
            time_fields = {"at", "every", "days", "start", "end", "event_type", "lead_minutes"} & patch.overrides.keys()
            if time_fields:
                raise ValueError(
                    f"Time/event fields ({', '.join(sorted(time_fields))}) cannot be set on a {effective_type} trigger"
                )

        return build_trigger(effective_type, **{k: v for k, v in merged.items() if v is not None})

    async def update(
        self,
        task_id: str,
        name: str | None = None,
        description: str | None = None,
        trigger_type: str | None = None,
        at: str | None = None,
        days: str | None = None,
        every: str | None = None,
        event_type: str | None = None,
        lead_minutes: int | str | None = None,
        idle_minutes: int | None = None,
        every_n: int | None = None,
        start: str | None = None,
        end: str | None = None,
        auto_approve: bool | None = None,
        enabled: bool | None = None,
        model: str | None = None,
        triggers: list[dict] | None = None,
        cooldown_minutes: int | None = None,
        max_iterations: int | None = None,
        stop_when: str | None = None,
        max_age_days: int | None = None,
        tool_scope: list[str] | None = None,
        output_schema: str | None = None,
    ) -> Automation:
        task = await self.get(task_id)
        changes = self._build_metadata_changes(
            name=name,
            description=description,
            auto_approve=auto_approve,
            enabled=enabled,
            model=model,
            cooldown_minutes=cooldown_minutes,
            max_iterations=max_iterations,
            stop_when=stop_when,
            max_age_days=max_age_days,
            tool_scope=tool_scope,
            output_schema=output_schema,
        )

        trigger_patch = TriggerPatch(
            trigger_type=trigger_type,
            at=at,
            days=days,
            every=every,
            event_type=event_type,
            lead_minutes=lead_minutes,
            start=start,
            end=end,
            idle_minutes=idle_minutes,
            every_n=every_n,
        )

        # Full triggers list replacement takes precedence over field-level patching
        triggers = await self._resolve_message_triggers(triggers)
        if triggers:
            parsed_triggers = [trigger for t in triggers if (trigger := parse_one(t)) is not None]
            time_triggers = [t for t in parsed_triggers if isinstance(t, TimeTrigger)]
            changes["triggers"] = parsed_triggers
            changes["next_run_at"] = time_triggers[0].next_run(datetime.now(UTC)) if time_triggers else None
        else:
            # For single-trigger patching via field params
            if task.triggers:
                current_trigger = task.triggers[0]
            else:
                current_trigger = TimeTrigger(at="00:00")

            trigger_result = self._build_updated_trigger(current_trigger, trigger_patch)
            if trigger_result:
                new_trigger, new_next_run = trigger_result
                changes["triggers"] = [new_trigger]
                changes["next_run_at"] = new_next_run

        updated = replace(task, **changes) if changes else task
        if changes:
            await self.store.update_metadata(updated)
        return updated

    async def create(
        self,
        name: str,
        description: str,
        trigger_type: str | None = None,
        at: str | None = None,
        days: str | None = None,
        every: str | None = None,
        event_type: str | None = None,
        lead_minutes: int | str | None = None,
        idle_minutes: int | None = None,
        every_n: int | None = None,
        auto_approve: bool = False,
        start: str | None = None,
        end: str | None = None,
        model: str | None = None,
        triggers: list[dict] | None = None,
        cooldown_minutes: int | None = None,
        thread_id: str | None = None,
        read_history: bool = False,
        area_id: str | None = None,
        idempotency_key: str | None = None,
        idempotency_scope: str | None = None,
        parent_automation_id: str | None = None,
        parent_fire_at: str | None = None,
        attempt_n: int | None = None,
        tool_scope: list[str] | None = None,
        output_schema: str | None = None,
        task_id: str | None = None,
    ) -> Automation | None:
        triggers = await self._resolve_message_triggers(triggers)
        parsed_triggers, next_run = _build_trigger_and_next_run(
            trigger_type=trigger_type,
            at=at,
            days=days,
            every=every,
            event_type=event_type,
            lead_minutes=lead_minutes,
            start=start,
            end=end,
            idle_minutes=idle_minutes,
            every_n=every_n,
            triggers=triggers,
        )

        now = datetime.now(UTC)
        # Stable ids (e.g. area:{key}) let seeding find its rows across boots.
        # Area-owned children mint under area:{key}:{slug} — that prefix IS
        # the ownership boundary area_run_automation enforces. Everything
        # else gets a random slug.
        if task_id is None:
            slug = generate_slug(2)
            task_id = f"area:{area_id}:{slug}" if area_id else slug

        if idempotency_key is not None and idempotency_scope is None:
            raise ValueError("idempotency_scope required when idempotency_key is set")

        # Auto-provision a durable "channel" session that owns this
        # automation's activity. Binding thread_id routes the automation
        # through the existing session-bound iteration path (no new
        # execution path), which persists the full turn and emits live SSE.
        if thread_id is None:
            channel = await self._provision_channel(name, task_id, area_id=area_id)
            thread_id = channel.session_id
            read_history = True

        resolve_output_schema(output_schema)
        automation = Automation(
            task_id=task_id,
            name=name,
            description=description,
            model=_normalize_and_validate_model(model),
            triggers=parsed_triggers,
            enabled=True,
            created_at=now,
            next_run_at=next_run,
            last_run_at=None,
            last_result=None,
            running_since=None,
            auto_approve=auto_approve,
            cooldown_minutes=cooldown_minutes,
            thread_id=thread_id,
            read_history=read_history,
            parent_automation_id=parent_automation_id,
            idempotency_key=idempotency_key,
            idempotency_scope=idempotency_scope,
            tool_scope=tool_scope,
            output_schema=output_schema,
        )

        if idempotency_key is not None:
            claimed = await self.store.save_with_claim(
                automation,
                scope=idempotency_scope,
                key=idempotency_key,
                parent_automation_id=parent_automation_id,
                parent_fire_at=parent_fire_at,
                attempt_n=attempt_n,
                claimed_at=now,
            )
            if not claimed:
                return None
        else:
            await self.store.save(automation)
        return automation

    async def backfill_channels(self) -> int:
        """One-time upgrade: give pre-existing agent automations a bound
        channel. Skips internal handlers, loops, and already-bound rows.
        Idempotent — only acts on rows with thread_id is None."""
        count = 0
        for task in await self.store.list_all():
            if task.handler is not None or task.kind == "loop" or task.thread_id is not None:
                continue
            channel = await self._provision_channel(task.name, task.task_id)
            updated = replace(task, thread_id=channel.session_id, read_history=True)
            await self.store.update_metadata(updated)
            count += 1
        return count

    async def delete(self, task_id: str) -> int:
        task = await self.get(task_id)
        if task.builtin:
            raise ValueError(f"Cannot delete builtin automation '{task.name}'")
        # Disable (don't delete) children first — preserves forensic data,
        # matches the idempotency-claim "ledger" pattern. Orphans can be
        # cleaned up manually if desired.
        disabled = await self.cancel_children(task_id)
        deleted = await self.store.delete(task_id)
        if not deleted:
            raise KeyError(f"Automation {task_id} not found")
        return disabled

    async def list_children(self, parent_id: str) -> list[Automation]:
        return await self.store.list_by_parent(parent_id)

    async def cancel_children(self, parent_id: str) -> int:
        return await self.store.disable_by_parent(parent_id)

    async def list_loops_by_session(self, session_id: str) -> list[Automation]:
        return await self.store.list_loops_by_session(session_id)

    async def create_loop(
        self,
        *,
        session_id: str,
        prompt: str,
        every: str,
        max_iterations: int | None = None,
        stop_when: str | None = None,
        max_age_days: int | None = None,
        idempotency_key: str | None = None,
        idempotency_scope: str | None = None,
        parent_automation_id: str | None = None,
        parent_fire_at: str | None = None,
        attempt_n: int | None = None,
    ) -> Automation | None:
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("prompt required")
        if not session_id:
            raise ValueError("session_id required")

        triggers, _ = _build_trigger_and_next_run(
            trigger_type="time",
            at=None,
            days=None,
            every=every,
            event_type=None,
            lead_minutes=None,
            start=None,
            end=None,
            idle_minutes=None,
            every_n=None,
            triggers=None,
        )
        now = datetime.now(UTC)
        task_id = f"loop-{generate_slug(2)}"

        if idempotency_key is not None and idempotency_scope is None:
            raise ValueError("idempotency_scope required when idempotency_key is set")

        # First fire is "as soon as the session goes idle" — set next_run_at
        # to now so the scheduler picks it up immediately. The fire gate
        # (apps/server/ntrp/server/app.py) defers the actual fire until the
        # /loop creation turn ends, so the iteration renders as a fresh
        # chat turn instead of getting injected into the creator's turn.
        automation = Automation(
            task_id=task_id,
            name=f"Loop: {prompt[:40]}",
            description=prompt,
            model=None,
            triggers=triggers,
            enabled=True,
            created_at=now,
            next_run_at=now,
            last_run_at=None,
            last_result=None,
            running_since=None,
            auto_approve=True,
            kind="loop",
            thread_id=session_id,
            read_history=True,
            max_iterations=max_iterations,
            iteration_count=0,
            stop_when=stop_when,
            max_age_days=max_age_days,
            parent_automation_id=parent_automation_id,
            idempotency_key=idempotency_key,
            idempotency_scope=idempotency_scope,
        )

        if idempotency_key is not None:
            claimed = await self.store.save_with_claim(
                automation,
                scope=idempotency_scope,
                key=idempotency_key,
                parent_automation_id=parent_automation_id,
                parent_fire_at=parent_fire_at,
                attempt_n=attempt_n,
                claimed_at=now,
            )
            if not claimed:
                return None
        else:
            await self.store.save(automation)
        return automation
