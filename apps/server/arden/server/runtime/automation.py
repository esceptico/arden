from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pydantic import ValidationError

from arden.agent_surface.schedules import compile_schedules_to_automations
from arden.areas.agent import AreaCustodianReport, custodian_contract, record_area_run
from arden.areas.asks import AskStore
from arden.areas.custodian import CustodianStore
from arden.areas.models import Area, areas_from_records
from arden.areas.suggester import AreaSuggester, AreaSuggestionStore
from arden.automation.builtins import seed_builtins
from arden.automation.scheduler import Scheduler
from arden.automation.service import AutomationService
from arden.automation.suggestions import AutomationSuggester, AutomationSuggestion
from arden.automation.triggers import TimeTrigger
from arden.config import Config
from arden.constants import (
    AREA_AGENT_DAILY_AT,
    AREA_AGENT_HANDLER,
    AREA_ASK_IGNORED_DAYS,
    AREAS_AGENT_STATE_FILE,
    AREAS_STATE_FILE,
    AREAS_SUGGESTIONS_FILE,
    BUILTIN_AREA_SUGGESTER_ID,
)
from arden.events.sse import AreasChangedEvent, AutomationSuggestionsUpdatedEvent
from arden.integrations.calendar.client import MultiCalendarSource
from arden.logging import get_logger
from arden.monitor.calendar import CalendarMonitor
from arden.monitor.service import Monitor
from arden.operator.runner import OperatorDeps
from arden.server.indexer import Indexer
from arden.server.runtime.outbox import RuntimeOutbox
from arden.server.stores import Stores

_logger = get_logger(__name__)


class SuggesterUnavailableError(Exception):
    """Raised when the automation suggester cannot run (memory or cheap_llm missing)."""


class AutomationRuntime:
    def __init__(
        self,
        *,
        stores: Stores,
        config: Config,
        build_operator_deps: Callable[[], OperatorDeps],
        get_records: Callable[[], object | None],
        get_chat_connector: Callable[[], object | None],
        get_calendar_source: Callable[[], object | None],
        get_slack_client: Callable[[], object | None],
        get_cheap_llm: Callable[[], object | None],
        cheap_model: str | None,
        indexer: Indexer | None,
        get_consolidate: Callable[[], object | None] = lambda: None,
        get_knowledge: Callable[[], object | None] = lambda: None,
        get_integration_clients: Callable[[], dict[str, object]] = dict,
        get_notifiers: Callable[[], object | None] = lambda: None,
    ):
        self.stores = stores
        self.config = config
        self.get_records = get_records
        self.get_calendar_source = get_calendar_source
        self.get_slack_client = get_slack_client
        self.get_cheap_llm = get_cheap_llm
        self.get_consolidate = get_consolidate
        self.get_knowledge = get_knowledge
        self.get_integration_clients = get_integration_clients
        self.cheap_model = cheap_model
        self.build_operator_deps = build_operator_deps
        self.area_asks = AskStore(config.arden_dir / AREAS_STATE_FILE)
        self.custodians = CustodianStore(config.arden_dir / AREAS_AGENT_STATE_FILE)
        self.get_notifiers = get_notifiers
        self.area_suggestions = AreaSuggestionStore(config.arden_dir / AREAS_SUGGESTIONS_FILE)
        self.scheduler = Scheduler(
            store=stores.automations,
            build_deps=build_operator_deps,
        )
        self.automation_service = AutomationService(
            store=stores.automations,
            scheduler=self.scheduler,
            session_service=stores.sessions,
            get_slack_client=self.get_slack_client,
        )
        self.outbox_runtime = RuntimeOutbox(
            outbox_store=stores.outbox,
            automation_store=stores.automations,
            scheduler=self.scheduler,
            indexer=indexer,
            get_chat_connector=get_chat_connector,
            on_area_run=self._on_area_run_completed,
        )
        self.monitor: Monitor | None = None

    async def load_areas(self) -> list[Area]:
        """Areas are the capability-bearing areas (unification: one
        container concept, area_id as identity)."""
        return areas_from_records(await self.stores.sessions.list_areas())

    async def stop(self) -> None:
        if self.monitor:
            await self.monitor.stop()
        await self.outbox_runtime.stop()
        await self.scheduler.stop()

    async def _on_area_run_completed(self, run_completed) -> None:
        """Ask sync for area channel runs: when a completed run's session
        belongs to an area:* automation, every run re-decides the area's one
        ask (record_area_run parses the fenced nomination; silence retires
        the previous one). Rides the outbox — the designed post-run pipeline
        — instead of a scheduler special case."""
        autos = await self.stores.automations.list_session_bound_by_session(run_completed.session_id)
        for auto in autos:
            if not auto.task_id.startswith("area:"):
                continue
            key = auto.task_id.removeprefix("area:")
            area_ = next((s for s in await self.load_areas() if s.key == key), None)
            if area_ is None or area_.page_path is None:
                continue
            # Only asks the user has had a real chance to answer count as
            # ignored (asks are durable now — a fresh question must not start
            # decaying attention on the very next run).
            stale_cutoff = (datetime.now(UTC) - timedelta(days=AREA_ASK_IGNORED_DAYS)).isoformat()
            ignored = any(a.source == "agent" and a.created_at <= stale_cutoff for a in self.area_asks.list(key))
            created = await self._commit_area_report(
                key,
                area_.page_path,
                run_completed.structured_output,
                run_ref=f"run:{run_completed.run_id}",
            )
            record = await self.stores.sessions.get_area(key)
            attention = (record or {}).get("attention") or "ambient"
            # Self-paced heartbeat: the run's own next-check (clamped +
            # quiet-decayed) replaces the pre-run trigger advance.
            next_run = self.custodians.record_run(
                key,
                run_completed.structured_output,
                attention=attention,
                paused=bool((record or {}).get("paused_at")),
            )
            await self.stores.automations.set_next_run(auto.task_id, next_run)
            if self.custodians.note_ignored_asks(key, ignored):
                stepped = {"active": "ambient", "ambient": "dormant"}.get(attention)
                if stepped:
                    await self.stores.sessions.update_area(key, attention=stepped)
                    _logger.info("Area %s attention stepped down to %s (asks unanswered)", key, stepped)
            await self._notify_asks(area_, record, created)
            await self.scheduler.emit_automation_event(AreasChangedEvent(keys=[key]))

    async def _commit_area_report(
        self,
        area_id: str,
        page_path: str,
        structured_output: dict | None,
        run_ref: str,
    ) -> list:
        """Commit canonical work before deriving asks from the same report."""
        if structured_output is None:
            return []
        try:
            report = AreaCustodianReport.model_validate(structured_output)
        except ValidationError:
            _logger.warning("Ignoring malformed Custodian report for %s", area_id, exc_info=True)
            return []
        await self.stores.area_work.apply_report(area_id, run_ref, report)
        return record_area_run(
            self.area_asks,
            area_id,
            page_path,
            report.model_dump(mode="json"),
            run_ref=run_ref,
        )

    async def _notify_asks(self, area_, record: dict | None, created: list) -> None:
        """Push newly nominated asks through the user's notifiers, gated by
        the area's interrupts policy. One ask, one canonical channel: the
        push IS the interrupt; Home holds the queue either way."""
        policy = (record or {}).get("interrupts") or "asks"
        pushable = {"asks": {"question", "review"}, "all": {"notify", "question", "review"}, "none": set()}[policy]
        to_send = [a for a in created if a.kind in pushable]
        if not to_send:
            return
        service = self.get_notifiers()
        if service is None or not getattr(service, "notifiers", None):
            return
        for ask in to_send:
            subject = f"arden · {area_.title}: {ask.kind}"
            lines = [ask.text]
            if ask.why_now:
                lines.append(f"Why now: {ask.why_now}")
            if ask.what_next:
                lines.append(f"Next: {ask.what_next}")
            body = "\n".join(lines)
            for name, notifier in service.notifiers.items():
                try:
                    await notifier.send(subject, body)
                except Exception:
                    _logger.exception("Notifier %s failed for area ask", name)

    async def request_area_wake(self, area_id: str, description: str) -> None:
        """A domain event happened: note it for the custodian and, budget and
        pause permitting, pull the next run earlier (debounced so a burst of
        events coalesces into one run)."""
        record = await self.stores.sessions.get_area(area_id)
        if record is None or record.get("autonomy") is None:
            return  # no standing agent to wake
        deadline = self.custodians.note_event(
            area_id,
            description,
            attention=record.get("attention") or "ambient",
            paused=bool(record.get("paused_at")),
        )
        if deadline is None:
            return
        task_id = f"area:{area_id}"
        auto = await self.stores.automations.get(task_id)
        if auto is None or not auto.enabled:
            return
        if auto.next_run_at is None or auto.next_run_at > deadline:
            await self.stores.automations.set_next_run(task_id, deadline)
            _logger.info("Area %s wake requested (%s), due %s", area_id, description, deadline.isoformat())

    async def sync_area_custodian(self, area: dict) -> None:
        """Synchronously reconcile one live Area into its exact contract."""
        projected = Area(
            key=area["area_id"],
            title=area["name"],
            page_path=area.get("page_path"),
            autonomy=area.get("autonomy"),
        )
        if projected.autonomy is None:
            await self.disable_area_custodian(projected.key)
            return
        records = await self.stores.sessions.list_areas()
        delegated = [record for record in records if record.get("autonomy") is not None]
        index = next((i for i, record in enumerate(delegated) if record["area_id"] == projected.key), 0)
        await self._sync_area_automation(projected, paused=bool(area.get("paused_at")), index=index)

    async def disable_area_custodian(self, area_id: str) -> None:
        task_id = f"area:{area_id}"
        if await self.stores.automations.get(task_id) is not None:
            await self.stores.automations.set_enabled(task_id, False)

    async def start_scheduler(self) -> None:
        self.scheduler.register_handler(
            "automation_suggester_daily",
            self._build_automation_suggester_handler(),
        )
        self.scheduler.register_handler(
            "memory_consolidate",
            self._build_memory_consolidate_handler(),
        )
        self.scheduler.register_handler(
            "memory_dream",
            self._build_memory_dream_handler(),
        )
        self.scheduler.register_handler(
            "memory_synthesize",
            self._build_memory_synthesize_handler(),
        )
        self.scheduler.register_handler(
            "memory_retention",
            self._build_memory_retention_handler(),
        )
        self.scheduler.register_handler(
            "area_suggester_daily",
            self._build_area_suggester_handler(),
        )
        await seed_builtins(self.stores.automations)
        await self._seed_area_automations()
        await self._kick_first_area_suggestion()
        await compile_schedules_to_automations(".", self.stores.automations)
        await self.automation_service.backfill_channels()
        self.scheduler.start()
        self.outbox_runtime.start()

    def _build_automation_suggester_handler(self):
        async def handler(context: dict | None) -> str | None:
            return await self._run_suggester()

        return handler

    def _build_memory_consolidate_handler(self):
        async def handler(context: dict | None) -> str | None:
            consolidate = self.get_consolidate()
            if consolidate is None:
                return "memory consolidation unavailable (no memory model configured)"
            totals: dict[str, int] | None = None
            # run_once is O(delta)-bounded (200/call); loop so one scheduled run
            # drains the day's backlog. Empty pass -> done.
            for _ in range(8):
                rep = await consolidate.run_once()
                if totals is None:
                    totals = dict.fromkeys(rep.summary_counts, 0)
                for key, value in rep.summary_counts.items():
                    totals[key] += value
                if not rep.changed_memory:
                    break
            assert totals is not None
            ordered_keys = (
                "merged",
                "superseded",
                "dropped",
                "retyped",
                "relabeled",
                "reclassified",
                "pruned",
            )
            return ", ".join(f"{key} {totals[key]}" for key in ordered_keys if key in totals)

        return handler

    def _build_memory_dream_handler(self):
        async def handler(context: dict | None) -> str | None:
            knowledge = self.get_knowledge()
            if knowledge is None or not knowledge.memory_ready:
                return "memory dream unavailable (memory not ready)"
            from arden.memory.dreamer import run_dream
            from arden.memory.file_store import load_conventions
            from arden.memory.maintenance import append_learnings, read_learnings
            from arden.memory.models import now_iso

            llm, model = knowledge._memory_llm()
            effort = knowledge._memory_reasoning_effort(knowledge.config.memory_model)
            # B: per-automation continual learning — read prior gotchas, append new ones.
            root = knowledge.record_store._root
            learnings = read_learnings(root, "memory_dream")
            summary, new = await run_dream(
                knowledge.record_store,
                llm,
                model,
                reasoning_effort=effort,
                conventions=load_conventions(),
                learnings=learnings,
            )
            append_learnings(root, "memory_dream", new, date=now_iso())
            return summary

        return handler

    def _build_memory_synthesize_handler(self):
        async def handler(context: dict | None) -> str | None:
            knowledge = self.get_knowledge()
            if knowledge is None or not knowledge.memory_ready:
                return "memory synthesis unavailable (memory not ready)"
            from arden.memory.synthesize import run_synthesis

            llm, model = knowledge._memory_llm()
            effort = knowledge._memory_reasoning_effort(knowledge.config.memory_model)
            # Tag untagged records with their named subject FIRST, so recurring people/
            # orgs/products promote to topic pages that this same pass then synthesizes.
            tagged = 0
            if knowledge.memory_curator is not None:
                tagged = await knowledge.memory_curator.backfill_entity_labels()
            summary = await run_synthesis(knowledge.record_store, llm, model, reasoning_effort=effort)
            return f"{summary} (+{tagged} entity tags)" if tagged else summary

        return handler

    def _build_memory_retention_handler(self):
        async def handler(context: dict | None) -> str | None:
            knowledge = self.get_knowledge()
            if knowledge is None or not knowledge.memory_ready:
                return "memory retention unavailable (memory not ready)"
            from arden.memory.retention import run_retention

            store = knowledge.record_store
            report = await run_retention(store)
            # Retention tombstones atoms; fold any entity page that just dropped
            # below the promotion threshold back into me.md the same night.
            stats = await store.reconcile_entities()
            detail = f"; entities {stats}" if (stats["promoted"] or stats["demoted"]) else ""
            return report.summary() + detail

        return handler

    def _build_area_suggester_handler(self):
        async def handler(context: dict | None) -> str | None:
            cheap_llm = self.get_cheap_llm()
            if cheap_llm is None:
                return "area suggester unavailable (no cheap model configured)"
            attached = {Path(s.page_path).stem for s in await self.load_areas() if s.page_path}
            suggester = AreaSuggester(
                attached_page_slugs=attached,
                vault_dir=self.config.memory_artifacts_dir,
                store=self.area_suggestions,
                cheap_llm=cheap_llm,
                model=self.cheap_model,
            )
            return await suggester.run()

        return handler

    async def _kick_first_area_suggestion(self) -> None:
        """Don't make a fresh install wait a day for its first suggestions:
        pull the builtin's next run to now so the scheduler fires it on this
        tick. Guard on last_run_at, NOT the suggestions file — a run killed
        mid-flight (a quick restart) advances next_run to the far daily slot
        but never writes the file, so keying on 'has it ever completed'
        re-arms it every boot until the first real run lands, instead of
        stranding suggestions for a day."""
        auto = await self.stores.automations.get(BUILTIN_AREA_SUGGESTER_ID)
        if auto and auto.enabled and auto.last_run_at is None:
            await self.stores.automations.set_next_run(BUILTIN_AREA_SUGGESTER_ID, datetime.now(UTC))

    @staticmethod
    def _area_run_at(index: int) -> str:
        """Stagger the daily slots 5 minutes apart. Identical times made all
        agents stampede the LLM/embedding providers at once every morning —
        the observed 503 cascade under parallel load."""
        hour, minute = (int(p) for p in AREA_AGENT_DAILY_AT.split(":"))
        total = hour * 60 + minute + index * 5
        return f"{total // 60 % 24:02d}:{total % 60:02d}"

    async def _seed_area_automations(self) -> None:
        """Area agents are ordinary CHANNEL automations — created through
        AutomationService.create like everything else: an area-tagged channel
        session owns each agent's runs (visible transcript, replyable,
        approvals surface in the session), iteration mode gives run-to-run
        memory, and the observe contract lives in tool_scope as editable
        data. Also migrates rows from the earlier handler-based shape."""
        records = [record for record in await self.stores.sessions.list_areas() if record.get("autonomy") is not None]
        for index, record in enumerate(records):
            area_ = Area(
                key=record["area_id"],
                title=record["name"],
                page_path=record.get("page_path"),
                autonomy=record.get("autonomy"),
            )
            await self._sync_area_automation(area_, paused=bool(record.get("paused_at")), index=index)

    async def _sync_area_automation(self, area_: Area, *, paused: bool, index: int) -> None:
        """Idempotent boot/live construction path for one Custodian."""
        contract = custodian_contract(area_)
        task_id = f"area:{area_.key}"
        run_at = self._area_run_at(index)
        trigger = {"type": "time", "at": run_at, "days": "daily"}
        existing = await self.stores.automations.get(task_id)
        channel_name = f"{area_.title} agent"
        if existing is None:
            channel = await self.automation_service._provision_channel(channel_name, task_id, area_id=area_.key)
            await self.automation_service.create(
                task_id=task_id,
                name=channel_name,
                description=contract.description,
                triggers=[trigger],
                auto_approve=contract.auto_approve,
                tool_scope=contract.tool_scope,
                output_schema="area_custodian",
                thread_id=channel.session_id,
                read_history=True,
            )
            if paused:
                await self.stores.automations.set_enabled(task_id, False)
            _logger.info("Seeded area channel automation: %s (at=%s)", task_id, run_at)
            return

        repaired = False
        if existing.handler == AREA_AGENT_HANDLER or existing.thread_id is None:
            channel = await self.automation_service._provision_channel(channel_name, task_id, area_id=area_.key)
            existing.handler = None
            existing.thread_id = channel.session_id
            existing.read_history = True
            existing.triggers = [TimeTrigger(at=run_at, days="daily")]
            existing.next_run_at = existing.triggers[0].next_run(datetime.now(UTC))
            existing.last_result = None
            repaired = True

        if existing.thread_id:
            if existing.name != channel_name:
                # The area was retitled — retitle the seeder-owned channel.
                await self.stores.sessions.rename(existing.thread_id, channel_name)
            else:
                # Repair empty/slug channel names; a user rename of the
                # channel session is theirs to keep.
                await self.stores.sessions.rename_if_empty(existing.thread_id, channel_name)

        # enabled derives from paused_at — the single pause control. An
        # out-of-band disable of the automation row heals on the next sync.
        desired_enabled = not paused
        stale_result = bool(existing.last_result and "without a report" in existing.last_result)
        changed = (
            repaired
            or stale_result
            or existing.name != channel_name
            or existing.description != contract.description
            or existing.auto_approve != contract.auto_approve
            or existing.tool_scope != contract.tool_scope
            or existing.output_schema != "area_custodian"
            or existing.enabled != desired_enabled
        )
        if not changed:
            return
        if stale_result:
            existing.last_result = None
        existing.name = channel_name
        existing.description = contract.description
        existing.auto_approve = contract.auto_approve
        existing.tool_scope = contract.tool_scope
        existing.output_schema = "area_custodian"
        existing.enabled = desired_enabled
        await self.stores.automations.save(existing)

    def _suggester_available(self) -> bool:
        return self.get_records() is not None and self.get_cheap_llm() is not None

    async def _run_suggester(self) -> str | None:
        if not self._suggester_available():
            return None
        suggester = AutomationSuggester(
            records=self.get_records(),
            sessions=self.stores.sessions,
            automations=self.stores.automations,
            cheap_llm=self.get_cheap_llm(),
            model=self.cheap_model,
        )
        summary = await suggester.run()
        await self.scheduler.emit_automation_event(AutomationSuggestionsUpdatedEvent())
        return summary

    async def refresh_suggestions(self) -> list[AutomationSuggestion]:
        if not self._suggester_available():
            raise SuggesterUnavailableError("memory or cheap_llm is not available")
        await self._run_suggester()
        return await self.stores.automations.list_active_suggestions()

    def start_monitor(self) -> None:
        if self.stores.monitor is None:
            raise RuntimeError("Monitor state store is not initialized")

        self.monitor = Monitor(self.scheduler.fire_event)
        calendar_source = self.get_calendar_source()
        if calendar_source and isinstance(calendar_source, MultiCalendarSource):
            self.monitor.register(CalendarMonitor(calendar_source, state_store=self.stores.monitor))

        self.monitor.start()

    async def restart_monitor(self) -> None:
        if self.stores.monitor is None:
            return
        if self.monitor:
            await self.monitor.stop()
        self.start_monitor()

    async def get_scheduler_status(self) -> dict:
        if not self.scheduler:
            return {"status": "disabled", "running_tasks": 0, "registered_handlers": []}
        return await self.scheduler.get_status()

    async def get_outbox_status(self) -> dict:
        return await self.outbox_runtime.get_status()

    async def get_outbox_health(self) -> dict:
        return await self.outbox_runtime.get_health()

    async def replay_outbox_dead_events(self, event_ids: list[int]) -> dict:
        return await self.outbox_runtime.replay_dead_events(event_ids)

    async def prune_outbox_completed(self, *, before: datetime, limit: int) -> dict:
        return await self.outbox_runtime.prune_completed(before=before, limit=limit)
