import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from arden.agent_surface.schedules import compile_schedules_to_automations
from arden.areas.agent import AreaCustodianReport, custodian_contract, record_area_run
from arden.areas.asks import AskStore
from arden.areas.custodian import CustodianStore
from arden.areas.models import Area, areas_from_records
from arden.automation.builtins import (
    FACT_CAPTURE_PROMPT,
    FACT_MAINTENANCE_PROMPT,
    WIKI_MAINTENANCE_PROMPT,
    seed_builtins,
)
from arden.automation.descriptions import AutomationDescriptionGenerator
from arden.automation.models import Automation
from arden.automation.predefined import seed_predefined_user_automations
from arden.automation.scheduler import CompletedAgentRun, Scheduler
from arden.automation.service import AutomationService
from arden.automation.triggers import TimeTrigger
from arden.config import Config
from arden.constants import (
    AREA_AGENT_DAILY_AT,
    AREA_AGENT_HANDLER,
    AREA_ASK_IGNORED_DAYS,
    AREAS_AGENT_STATE_FILE,
    AREAS_STATE_FILE,
    BUILTIN_MEMORY_CAPTURE_ID,
    BUILTIN_MEMORY_CONSOLIDATE_ID,
    BUILTIN_MEMORY_SYNTHESIZE_ID,
    BUILTIN_WIKI_MAINTENANCE_ID,
)
from arden.events.internal import RunCompleted, RunCompletionRejected, RunFailed
from arden.events.sse import AreasChangedEvent, MemoryChangedEvent
from arden.integrations.calendar.client import MultiCalendarSource
from arden.llm.base import CompletionClient
from arden.logging import get_logger
from arden.memory.facts.capture.runner import FactCapture, FactCaptureError
from arden.memory.facts.maintenance.agent import FactMaintenanceReviewService
from arden.memory.facts.maintenance.runner import FactMaintenance, FactMaintenanceReviewer
from arden.monitor.calendar import CalendarMonitor
from arden.monitor.service import Monitor
from arden.notifiers.service import NotifierService
from arden.operator.runner import OperatorDeps, RunRequest, run_agent
from arden.outbox import AutomationSettled
from arden.revisions.models import CollectionReport
from arden.server.runtime.outbox import RuntimeOutbox
from arden.server.stores import Stores
from arden.wiki.constants import WIKI_READ_PAGE_TOOL_NAME
from arden.wiki.maintenance.agent import WikiMaintenanceReviewService
from arden.wiki.maintenance.runner import WikiMaintenance, WikiMaintenanceReviewer
from arden.wiki.service import WikiService

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
        resolve_auxiliary_completion: Callable[[], tuple[CompletionClient, str, str | None]],
        project_wiki_state: Callable[[], Awaitable[None]],
        get_fact_capture: Callable[[], FactCapture | None] = lambda: None,
        get_fact_dream: Callable[[], object | None] = lambda: None,
        get_fact_maintenance: Callable[[FactMaintenanceReviewer], FactMaintenance | None] = lambda _reviewer: None,
        get_fact_synthesis: Callable[[], object | None] = lambda: None,
        get_wiki_maintenance: Callable[[WikiMaintenanceReviewer], WikiMaintenance | None] = lambda _reviewer: None,
        synthesis_is_current: Callable[[], Awaitable[bool]] | None = None,
        on_automation_finished: Callable[[str, bool], Awaitable[None]] | None = None,
        get_wiki_service: Callable[[], WikiService | None] = lambda: None,
        get_notifiers: Callable[[], NotifierService | None] = lambda: None,
        collect_managed_history: Callable[[], tuple[CollectionReport, CollectionReport]] | None = None,
    ):
        self.stores = stores
        self.config = config
        self.get_calendar_source = get_calendar_source
        self.get_slack_client = get_slack_client
        self.get_fact_capture = get_fact_capture
        self.fact_capture_review: FactCapture | None = None
        self.get_fact_dream = get_fact_dream
        self.get_fact_maintenance = get_fact_maintenance
        self.fact_maintenance_review: FactMaintenanceReviewService | None = None
        self.get_fact_synthesis = get_fact_synthesis
        self.get_wiki_maintenance = get_wiki_maintenance
        self.wiki_maintenance_review: WikiMaintenanceReviewService | None = None
        self.synthesis_is_current = synthesis_is_current
        self.on_automation_finished = on_automation_finished
        self.get_wiki_service = get_wiki_service
        self.build_operator_deps = build_operator_deps
        self.area_asks = AskStore(config.arden_dir / AREAS_STATE_FILE)
        self.custodians = CustodianStore(config.arden_dir / AREAS_AGENT_STATE_FILE)
        self.get_notifiers = get_notifiers
        self.collect_managed_history = collect_managed_history
        self.scheduler = Scheduler(
            store=stores.automations,
            build_deps=build_operator_deps,
            validate_completed_run=self._validate_completed_run,
        )
        self.automation_service = AutomationService(
            store=stores.automations,
            scheduler=self.scheduler,
            session_service=stores.sessions,
            get_slack_client=self.get_slack_client,
            description_generator=AutomationDescriptionGenerator(resolve_auxiliary_completion),
        )
        self.outbox_runtime = RuntimeOutbox(
            outbox_store=stores.outbox,
            scheduler=self.scheduler,
            on_area_run=self._on_area_run_completed,
            on_area_run_failed=self._on_area_run_failed,
            on_automation_settled=self._on_automation_settled,
            on_wiki_projection=project_wiki_state,
        )
        self.monitor: Monitor | None = None

    async def _on_automation_settled(self, event: AutomationSettled) -> None:
        if self.on_automation_finished is not None:
            await self.on_automation_finished(event.task_id, event.success)

    async def _validate_completed_run(self, automation: Automation, run_id: str | None) -> None:
        if automation.tool_scope != "wiki_producer":
            return
        if run_id is None:
            raise RuntimeError("wiki producer did not expose a chat run for completion proof")
        wiki = self.get_wiki_service()
        if wiki is None:
            raise RuntimeError("wiki producer completion proof is unavailable")
        snapshot = await asyncio.to_thread(wiki.snapshot)
        owned_titles = {
            record.page.title
            for record in snapshot.pages
            if record.page.metadata.get("producer_automation_id") == automation.task_id
        }
        if not owned_titles:
            raise RuntimeError("wiki producer has no owned page")
        calls = await self.stores.sessions.store.list_tool_calls(run_id=run_id)
        if any(
            call["tool_name"] == WIKI_READ_PAGE_TOOL_NAME
            and call["status"] == "success"
            and call["result_preview"] in owned_titles
            for call in calls
        ):
            return
        raise RuntimeError("wiki producer finished without successfully reading its owned page")

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

    async def request_fact_maintenance(self) -> bool:
        """Schedule fact maintenance."""

        return await self.scheduler.request_delayed_run(
            BUILTIN_MEMORY_CONSOLIDATE_ID,
            timedelta(0),
        )

    async def notify_wiki_changed(
        self,
        paths: list[str],
        revision: str | None,
        *,
        source_areas_by_path: dict[str, set[str]],
    ) -> None:
        await self.scheduler.emit_automation_event(MemoryChangedEvent(paths=paths, revision=revision))
        changed = set(paths)
        for record in await self.stores.sessions.list_areas():
            page_path = record.get("page_path")
            if (
                page_path is None
                or page_path not in changed
                or record["area_id"] in source_areas_by_path.get(page_path, set())
            ):
                continue
            await self.request_area_wake(record["area_id"], f"topic page edited ({page_path})")

    async def _on_area_run_completed(self, run_completed: RunCompleted) -> bool:
        """Project one trusted custodian report after durable chat completion."""
        try:
            return await self._project_area_report(
                run_id=run_completed.run_id,
                session_id=run_completed.session_id,
                automation_task_id=run_completed.automation_task_id,
                required=True,
            )
        except Exception:
            task_id = run_completed.automation_task_id
            if task_id is not None and task_id.startswith("area:"):
                self.custodians.abandon_delivery(
                    task_id.removeprefix("area:"),
                    run_id=run_completed.run_id,
                )
            raise

    async def _on_area_run_failed(self, run_failed: RunFailed) -> bool:
        """Finish projecting any report accepted before chat finalization failed."""
        accepted = await self._project_area_report(
            run_id=run_failed.run_id,
            session_id=run_failed.session_id,
            automation_task_id=run_failed.automation_task_id,
            required=False,
        )
        task_id = run_failed.automation_task_id
        if not accepted and task_id is not None and task_id.startswith("area:"):
            self.custodians.abandon_delivery(
                task_id.removeprefix("area:"),
                run_id=run_failed.run_id,
            )
        return accepted

    async def _project_area_report(
        self,
        *,
        run_id: str,
        session_id: str,
        automation_task_id: str | None,
        required: bool,
    ) -> bool:
        autos = await self.stores.automations.list_session_bound_by_session(session_id)
        for auto in autos:
            if not auto.task_id.startswith("area:") or automation_task_id != auto.task_id:
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
            run_ref = f"run:{run_id}"
            report_data = await self.stores.area_work.report(run_ref)
            if report_data is None:
                if required:
                    raise RunCompletionRejected("Area custodian ended without submitting a report")
                return False
            report = AreaCustodianReport.model_validate(report_data)
            created = record_area_run(
                self.area_asks,
                key,
                area_.page_path,
                report,
                run_ref=run_ref,
            )
            record = await self.stores.sessions.get_area(key)
            attention = (record or {}).get("attention") or "ambient"
            # Self-paced heartbeat: the run's own next-check (clamped +
            # quiet-decayed) replaces the pre-run trigger advance.
            next_run = self.custodians.record_run(
                key,
                report,
                run_ref=run_ref,
                attention=attention,
                paused=bool((record or {}).get("paused_at")),
            )
            await self.scheduler.reschedule_run(auto.task_id, next_run)
            lowered_attention = self.custodians.note_ignored_asks(
                key,
                ignored,
                attention=attention,
                run_ref=run_ref,
            )
            if lowered_attention is not None:
                await self.stores.sessions.update_area(key, attention=lowered_attention)
                _logger.info(
                    "Area %s attention stepped down to %s (asks unanswered)",
                    key,
                    lowered_attention,
                )
            await self._notify_asks(area_, record, created)
            await self.scheduler.emit_automation_event(AreasChangedEvent(keys=[key]))
            return True
        return False

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
        if service is None or not service.notifiers:
            # Say so: otherwise a correctly-raised ask that reached nobody is
            # indistinguishable from an agent that never raised one.
            _logger.warning(
                "Area asks not delivered — no notifier configured",
                area=area_.key,
                asks=[a.id for a in to_send],
            )
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
                else:
                    # The delivery record. `notification_log` is written by
                    # nothing any more, so without this line there is no way to
                    # tell an ask that reached the user from one that did not.
                    _logger.info(
                        "Area ask delivered",
                        area=area_.key,
                        ask_id=ask.id,
                        kind=ask.kind,
                        notifier=name,
                    )

    async def request_area_wake(self, area_id: str, description: str, *, engaged: bool = False) -> None:
        """A domain event happened: note it for the custodian and, budget and
        pause permitting, pull the next run earlier (debounced so a burst of
        events coalesces into one run).

        `engaged` marks the event as a human acting on this area rather than
        the system observing it.
        """
        record = await self.stores.sessions.get_area(area_id)
        if record is None or record.get("autonomy") is None:
            return  # no standing agent to wake
        attention = record.get("attention") or "ambient"
        if engaged and attention == "dormant":
            # Decay concluded the user had stopped answering; they just answered.
            # Nothing else raises attention — note_ignored_asks only lowers it —
            # so without this an area that goes quiet once stays quiet forever.
            await self.stores.sessions.update_area(area_id, attention="ambient")
            _logger.info("Area %s attention restored to ambient (user engaged: %s)", area_id, description)
            attention = "ambient"
        deadline = self.custodians.note_event(
            area_id,
            description,
            attention=attention,
            paused=bool(record.get("paused_at")),
        )
        if deadline is None:
            return
        task_id = f"area:{area_id}"
        if await self.scheduler.reschedule_run(task_id, deadline, only_if_earlier=True):
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
        self.scheduler.register_handler("memory_capture", self._run_memory_capture)
        self.scheduler.register_handler("memory_maintenance", self._run_fact_maintenance)
        self.scheduler.register_handler("memory_synthesize", self._run_memory_synthesis)
        self.scheduler.register_handler("memory_dream", self._run_memory_dream)
        self.scheduler.register_handler("wiki_maintenance", self._run_wiki_maintenance)
        self.scheduler.register_handler("managed_history_collection", self._run_managed_history_collection)
        memory_model = self.config.memory_model
        await seed_builtins(
            self.stores.automations,
            memory_model=memory_model,
            include_managed_history_collection=self.collect_managed_history is not None,
        )
        await seed_predefined_user_automations(self.stores.automations)
        await self._seed_area_automations()
        await compile_schedules_to_automations(".", self.stores.automations)
        await self.automation_service.backfill_channels()
        self.scheduler.start()
        self.outbox_runtime.start()

    async def _run_memory_capture(self, context: dict | None) -> str | CompletedAgentRun:
        capture = self.get_fact_capture()
        if capture is None:
            return "fact capture unavailable (no memory model configured)"
        if not await capture.has_work():
            return "fact capture idle"
        self.fact_capture_review = capture
        try:
            model = self.config.memory_model
            if not model:
                return "fact capture unavailable (no memory model configured)"
            request = RunRequest(
                prompt=FACT_CAPTURE_PROMPT,
                auto_approve=True,
                source_id=BUILTIN_MEMORY_CAPTURE_ID,
                model=model,
                skip_approvals=True,
                automation_id=BUILTIN_MEMORY_CAPTURE_ID,
                tool_scope="fact_capture",
            )
            agent_run = None
            while not capture.done:
                prior_decisions = capture.decided_batches
                agent_run = await run_agent(self.build_operator_deps(), request)
                if not capture.done and capture.decided_batches == prior_decisions:
                    raise FactCaptureError("fact capture agent exited before completing the review workflow")
            if agent_run is None:
                raise RuntimeError("fact capture agent did not start")
            result = capture.require_result()
            return CompletedAgentRun(agent_run.run_id, result.summary)
        finally:
            self.fact_capture_review = None

    async def _run_fact_maintenance(self, context: dict | None) -> str | CompletedAgentRun:
        review = FactMaintenanceReviewService.create(self.get_fact_maintenance)
        self.fact_maintenance_review = review
        if review is None:
            return "fact maintenance unavailable (no memory model configured)"
        try:
            model = self.config.memory_model
            if not model:
                return "fact maintenance unavailable (no memory model configured)"
            request = RunRequest(
                prompt=FACT_MAINTENANCE_PROMPT,
                auto_approve=True,
                source_id=BUILTIN_MEMORY_CONSOLIDATE_ID,
                model=model,
                skip_approvals=True,
                automation_id=BUILTIN_MEMORY_CONSOLIDATE_ID,
                tool_scope="fact_maintenance",
            )
            agent_run = None
            while not review.done:
                prior_decisions = review.accepted_decisions
                agent_run = await run_agent(self.build_operator_deps(), request)
                if not review.done and review.accepted_decisions == prior_decisions:
                    await review.require_completed()
            if agent_run is None:
                raise RuntimeError("fact maintenance agent did not start")
            result = await review.require_completed()
            if result.empty:
                return CompletedAgentRun(agent_run.run_id, "fact maintenance idle")
            return CompletedAgentRun(
                agent_run.run_id,
                f"fact maintenance: reviewed {result.reviewed_clusters}; "
                f"amended {result.amended_facts}; merged {result.merged_facts}",
            )
        finally:
            await review.aclose()
            self.fact_maintenance_review = None

    async def _run_memory_synthesis(self, context: dict | None) -> str | None:
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

    async def _run_memory_dream(self, context: dict | None) -> str:
        dream = self.get_fact_dream()
        if dream is None:
            return "memory dream unavailable (no memory model configured)"
        result = await dream.run()
        if result.empty:
            return "memory dream idle"
        state = "published" if result.published else "unchanged"
        return f"memory dream: {result.insight_count} insight(s); {state}"

    async def _run_managed_history_collection(self, context: dict | None) -> str:
        if self.collect_managed_history is None:
            raise RuntimeError("managed-history collection is unavailable without canonical facts and wiki")
        facts, wiki = await asyncio.to_thread(self.collect_managed_history)
        return (
            "managed history collection: "
            f"facts scanned {facts.scanned}, removed {facts.removed}, retained {facts.retained}, "
            f"bytes removed {facts.bytes_removed}; "
            f"wiki scanned {wiki.scanned}, removed {wiki.removed}, retained {wiki.retained}, "
            f"bytes removed {wiki.bytes_removed}"
        )

    async def _run_wiki_maintenance(self, context: dict | None) -> str | CompletedAgentRun:
        if self.synthesis_is_current is None or not await self.synthesis_is_current():
            task_id = context["task_id"] if context else None
            if task_id == BUILTIN_WIKI_MAINTENANCE_ID:
                await self.scheduler.request_delayed_run(task_id, _WIKI_MAINTENANCE_RETRY_DELAY)
            return "wiki maintenance deferred: synthesis is behind"

        model = self.config.memory_model
        if not model:
            return "wiki maintenance unavailable (no memory model configured)"
        request = RunRequest(
            prompt=WIKI_MAINTENANCE_PROMPT,
            auto_approve=True,
            source_id=BUILTIN_WIKI_MAINTENANCE_ID,
            model=model,
            skip_approvals=True,
            automation_id=BUILTIN_WIKI_MAINTENANCE_ID,
            tool_scope="wiki_maintenance",
        )
        results = []
        agent_run = None
        for _ in range(2):
            review = WikiMaintenanceReviewService.create(self.get_wiki_maintenance)
            self.wiki_maintenance_review = review
            if review is None:
                return "wiki maintenance unavailable (no memory model configured)"
            try:
                while not review.done:
                    prior_decisions = review.accepted_decisions
                    agent_run = await run_agent(self.build_operator_deps(), request)
                    if not review.done and review.accepted_decisions == prior_decisions:
                        await review.require_completed()
                results.append(await review.require_completed())
            finally:
                await review.aclose()
                self.wiki_maintenance_review = None
            if not results[-1].reload_required:
                break

        if agent_run is None:
            raise RuntimeError("wiki maintenance agent did not start")

        reviewed = sum(result.reviewed_commits for result in results)
        updated = sum(result.updated_pages for result in results)
        skipped = sum(result.skipped_oversized_reports for result in results)
        final = results[-1]
        if final.reload_required:
            state = "fresh-feed continuation deferred"
        elif final.empty:
            state = "idle"
        elif final.complete:
            state = "current"
        else:
            state = "incomplete"
        if final.reload_required:
            await self.scheduler.request_delayed_run(BUILTIN_WIKI_MAINTENANCE_ID, _WIKI_MAINTENANCE_RETRY_DELAY)
        result = f"wiki maintenance: {state}; reviewed {reviewed}; updated {updated}"
        if skipped:
            result += f"; skipped oversized reports {skipped}"
        return CompletedAgentRun(agent_run.run_id, result)

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
        memory, and the observe contract is a scope key resolved from
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
            await self.automation_service.create(
                task_id=task_id,
                name=channel_name,
                description=display_description,
                prompt=contract.description,
                triggers=[trigger],
                auto_approve=contract.auto_approve,
                tool_scope=contract.tool_scope,
                area_id=area_.key,
            )
            if paused:
                await self.stores.automations.set_enabled(task_id, False)
            _logger.info("Seeded area channel automation: %s (at=%s)", task_id, run_at)
            return

        repaired = False
        if existing.handler == AREA_AGENT_HANDLER or existing.thread_id is None:
            channel, _ = await self.automation_service._provision_channel(
                channel_name,
                task_id,
                area_id=area_.key,
            )
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
