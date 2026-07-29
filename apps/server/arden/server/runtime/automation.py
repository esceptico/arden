from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from arden.agent_surface.schedules import compile_schedules_to_automations
from arden.areas.agent import AreaCustodianReport, custodian_contract, record_area_run
from arden.areas.asks import AskStore
from arden.areas.custodian import CustodianStore
from arden.areas.models import Area, areas_from_records
from arden.automation.builtins import seed_builtins
from arden.automation.scheduler import Scheduler
from arden.automation.service import AutomationService
from arden.automation.triggers import TimeTrigger
from arden.config import Config
from arden.constants import (
    AREA_AGENT_DAILY_AT,
    AREA_AGENT_HANDLER,
    AREA_ASK_IGNORED_DAYS,
    AREAS_AGENT_STATE_FILE,
    AREAS_STATE_FILE,
    BUILTIN_MEMORY_SYNTHESIZE_ID,
    BUILTIN_WIKI_MAINTENANCE_ID,
)
from arden.events.sse import AreasChangedEvent, MemoryChangedEvent
from arden.integrations.calendar.client import MultiCalendarSource
from arden.logging import get_logger
from arden.monitor.calendar import CalendarMonitor
from arden.monitor.service import Monitor
from arden.operator.runner import OperatorDeps
from arden.server.indexer import Indexer
from arden.server.runtime.outbox import RuntimeOutbox
from arden.server.stores import Stores

_logger = get_logger(__name__)
_WIKI_MAINTENANCE_RETRY_DELAY = timedelta(minutes=1)


class AutomationRuntime:
    def __init__(
        self,
        *,
        stores: Stores,
        config: Config,
        build_operator_deps: Callable[[], OperatorDeps],
        get_calendar_source: Callable[[], object | None],
        get_slack_client: Callable[[], object | None],
        get_cheap_llm: Callable[[], object | None],
        cheap_model: str | None,
        indexer: Indexer | None,
        get_fact_dream: Callable[[], object | None] = lambda: None,
        get_fact_maintenance: Callable[[], object | None] = lambda: None,
        get_fact_synthesis: Callable[[], object | None] = lambda: None,
        get_wiki_maintenance: Callable[[], object | None] = lambda: None,
        synthesis_is_current: Callable[[], Awaitable[bool]] | None = None,
        project_wiki_health: Callable[[], Awaitable[None]] | None = None,
        on_automation_finished: Callable[[str, bool], Awaitable[None]] | None = None,
        get_notifiers: Callable[[], object | None] = lambda: None,
    ):
        self.stores = stores
        self.config = config
        self.get_calendar_source = get_calendar_source
        self.get_slack_client = get_slack_client
        self.get_fact_dream = get_fact_dream
        self.get_fact_maintenance = get_fact_maintenance
        self.get_fact_synthesis = get_fact_synthesis
        self.get_wiki_maintenance = get_wiki_maintenance
        self.synthesis_is_current = synthesis_is_current
        self.project_wiki_health = project_wiki_health
        self.build_operator_deps = build_operator_deps
        self.area_asks = AskStore(config.arden_dir / AREAS_STATE_FILE)
        self.custodians = CustodianStore(config.arden_dir / AREAS_AGENT_STATE_FILE)
        self.get_notifiers = get_notifiers
        self.scheduler = Scheduler(
            store=stores.automations,
            build_deps=build_operator_deps,
            on_run_finished=on_automation_finished,
        )
        self.automation_service = AutomationService(
            store=stores.automations,
            scheduler=self.scheduler,
            session_service=stores.sessions,
            get_slack_client=self.get_slack_client,
            get_cheap_llm=get_cheap_llm,
            description_model=cheap_model,
        )
        self.outbox_runtime = RuntimeOutbox(
            outbox_store=stores.outbox,
            automation_store=stores.automations,
            scheduler=self.scheduler,
            indexer=indexer,
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

    async def request_fact_synthesis(self) -> bool:
        """Coalesce fact commits into the canonical synthesis run."""

        return await self.scheduler.request_delayed_run(
            BUILTIN_MEMORY_SYNTHESIZE_ID,
            timedelta(minutes=5),
        )

    async def request_wiki_maintenance(self) -> bool:
        """Resume durable maintenance immediately after a user decision."""

        return await self.scheduler.request_delayed_run(
            BUILTIN_WIKI_MAINTENANCE_ID,
            timedelta(0),
        )

    async def notify_wiki_maintenance_reviews_changed(self, revision: str | None) -> None:
        """Invalidate the durable review queue in every open desktop."""

        await self.scheduler.emit_automation_event(
            MemoryChangedEvent(
                paths=[],
                revision=revision,
                review_required=True,
            )
        )

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
            "memory_maintenance",
            self._build_fact_maintenance_handler(),
        )
        self.scheduler.register_handler(
            "memory_synthesize",
            self._build_memory_synthesize_handler(),
        )
        self.scheduler.register_handler(
            "memory_dream",
            self._build_memory_dream_handler(),
        )
        self.scheduler.register_handler(
            "wiki_maintenance",
            self._build_wiki_maintenance_handler(),
        )
        await seed_builtins(self.stores.automations)
        await self._seed_area_automations()
        await compile_schedules_to_automations(".", self.stores.automations)
        await self.automation_service.backfill_channels()
        self.scheduler.start()
        self.outbox_runtime.start()

    def _build_fact_maintenance_handler(self):
        async def handler(context: dict | None) -> str | None:
            maintenance = self.get_fact_maintenance()
            if maintenance is None:
                return "fact maintenance unavailable (no memory model configured)"
            result = await maintenance.run()
            if result.empty:
                return "fact maintenance idle"
            return (
                f"fact maintenance: reviewed {result.reviewed_clusters}; "
                f"amended {result.amended_facts}; merged {result.merged_facts}"
            )

        return handler

    def _build_memory_synthesize_handler(self):
        async def handler(context: dict | None) -> str | None:
            try:
                synthesis = self.get_fact_synthesis()
                if synthesis is None:
                    return "fact synthesis unavailable (no memory model configured)"
                result = await synthesis.run()
                if result.empty:
                    return "fact synthesis idle"
                return (
                    f"fact synthesis: {result.published_pages} page(s) published"
                    f"; archived {result.skipped_archived}; under threshold {result.skipped_under_threshold}"
                )
            finally:
                await self._refresh_wiki_health()

        return handler

    def _build_memory_dream_handler(self):
        async def handler(context: dict | None) -> str:
            try:
                dream = self.get_fact_dream()
                if dream is None:
                    return "memory dream unavailable (no memory model configured)"
                result = await dream.run()
                if result.empty:
                    return "memory dream idle"
                state = "published" if result.published else "unchanged"
                return f"memory dream: {result.insight_count} insight(s); {state}"
            finally:
                await self._refresh_wiki_health()

        return handler

    def _build_wiki_maintenance_handler(self):
        async def handler(context: dict | None) -> str:
            refresh_health = False
            try:
                if self.synthesis_is_current is None or not await self.synthesis_is_current():
                    task_id = context.get("task_id") if isinstance(context, dict) else None
                    if task_id == BUILTIN_WIKI_MAINTENANCE_ID:
                        await self.scheduler.request_delayed_run(task_id, _WIKI_MAINTENANCE_RETRY_DELAY)
                    return "wiki maintenance deferred: synthesis is behind"

                maintenance = self.get_wiki_maintenance()
                if maintenance is None:
                    return "wiki maintenance unavailable (no memory model configured)"

                results = []
                for _ in range(2):
                    result = await maintenance.run()
                    results.append(result)
                    if not result.reload_required:
                        break

                reviewed = sum(result.reviewed_commits for result in results)
                updated = sum(result.updated_pages for result in results)
                final = results[-1]
                if final.blocked:
                    state = "needs user review"
                elif final.reload_required:
                    state = "fresh-feed continuation deferred"
                elif final.empty:
                    state = "idle"
                elif final.complete:
                    state = "current"
                else:
                    state = "incomplete"
                if final.blocked:
                    # The review row is durable, but no vault file changed. Notify an
                    # already-open desktop to refetch its review list immediately.
                    await self.notify_wiki_maintenance_reviews_changed(final.feed_target_revision)
                refresh_health = final.complete or final.empty
                return f"wiki maintenance: {state}; reviewed {reviewed}; updated {updated}"
            finally:
                if refresh_health:
                    await self._refresh_wiki_health()

        return handler

    async def _refresh_wiki_health(self) -> None:
        if self.project_wiki_health is None:
            return
        await self.project_wiki_health()

    @staticmethod
    def _area_run_at(index: int) -> str:
        """Stagger the daily slots 5 minutes apart. Identical times made all
        agents stampede the LLM/embedding providers at once every morning —
        the observed 503 cascade under parallel load."""
        hour, minute = (int(p) for p in AREA_AGENT_DAILY_AT.split(":"))
        total = hour * 60 + minute + index * 5
        return f"{total // 60 % 24:02d}:{total % 60:02d}"

    @staticmethod
    def _area_display_description(area_: Area) -> str:
        """Canonical concise summary from the Automations mockup."""
        return f"Keeps the {area_.title} area current and surfaces decisions that need you."

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
        display_description = self._area_display_description(area_)
        if existing is None:
            channel = await self.automation_service._provision_channel(channel_name, task_id, area_id=area_.key)
            await self.automation_service.create(
                task_id=task_id,
                name=channel_name,
                description=display_description,
                prompt=contract.description,
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
        needs_display_description = existing.description is None
        needs_description_source = needs_display_description or existing.description_source is None
        changed = (
            repaired
            or stale_result
            or existing.name != channel_name
            or existing.prompt != contract.description
            or needs_display_description
            or needs_description_source
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
        # A generated/manual summary is user-facing product copy. Keep it
        # across area syncs; only seed the mockup's concise copy for migrated
        # rows that intentionally have none.
        if needs_display_description:
            existing.description = display_description
        if needs_description_source:
            existing.description_source = "manual"
        existing.prompt = contract.description
        existing.auto_approve = contract.auto_approve
        existing.tool_scope = contract.tool_scope
        existing.output_schema = "area_custodian"
        existing.enabled = desired_enabled
        await self.stores.automations.update_metadata(existing)

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
