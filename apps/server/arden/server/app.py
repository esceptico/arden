import asyncio
import json
import signal
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from importlib.metadata import version

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from arden.agent import Role
from arden.areas.agent import INTAKE_ADDENDUM, is_custodian_task_id, render_work_context
from arden.areas.lifecycle import AreaLifecycleService, AreaPageService
from arden.areas.models import areas_from_records
from arden.areas.projection import area_automation_match
from arden.areas.service import AreaService
from arden.areas.work_models import AreaWorkSnapshot
from arden.automation.models import Automation
from arden.automation.prompts import AUTOMATION_PROMPT
from arden.automation.scheduler import (
    AUTOMATION_BUS_KEY,
    CompletedAgentRun,
    RunDeferred,
    RunSkipped,
    split_manual_flag,
)
from arden.constants import BACKGROUND_AGENT_MAX_SPAWN_ATTEMPTS
from arden.core.tool_result_files import prune_offload_store
from arden.events.internal import RunFailed
from arden.events.sse import AreasChangedEvent
from arden.llm.openai_codex_catalog import refresh_codex_models
from arden.logging import get_logger
from arden.operator.runner import RunRequest, run_agent, run_agent_streaming
from arden.server.app_control import AppControlService
from arden.server.bus import BusRegistry, prime_bus_cursor_from_store
from arden.server.middleware import AuthMiddleware, TracingMiddleware
from arden.server.routers.areas import asks_router
from arden.server.routers.areas import router as areas_router
from arden.server.routers.automation import router as automation_router
from arden.server.routers.canonical_memory import facts_router
from arden.server.routers.canonical_memory import wiki_router as wiki_pages_router
from arden.server.routers.chat import router as chat_router
from arden.server.routers.context import router as context_router
from arden.server.routers.dev_runtime import router as dev_runtime_router
from arden.server.routers.executor import router as executor_router
from arden.server.routers.gmail import router as gmail_router
from arden.server.routers.google import router as google_router
from arden.server.routers.loops import router as loops_router
from arden.server.routers.mcp import router as mcp_router
from arden.server.routers.ops import router as ops_router
from arden.server.routers.providers import router as providers_router
from arden.server.routers.runtime_info import router as runtime_info_router
from arden.server.routers.session import router as session_router
from arden.server.routers.settings import router as settings_router
from arden.server.routers.setup import router as setup_router
from arden.server.routers.skills import router as skills_router
from arden.server.routers.wiki import router as wiki_router
from arden.server.runtime import Runtime
from arden.services.chat import respawn_background_agent, resume_suspended_chat_run, submit_chat_message
from arden.tools.scopes import ScopeKey
from arden.wiki.migrate import migrate_title_links

_logger = get_logger(__name__)


async def _create_bus_registry(runtime: Runtime) -> BusRegistry:
    event_store = runtime.session_service.store if runtime.session_service else None
    bus_registry = BusRegistry(record_events=event_store.record_session_events if event_store else None)
    # Producers start during Runtime.connect() and may emit onto the global bus
    # before its HTTP stream is opened. Seed that bus from durable state now so
    # a restart never reuses sequence numbers already present in session_events.
    await prime_bus_cursor_from_store(bus_registry, AUTOMATION_BUS_KEY, event_store)
    return bus_registry


def _loop_target_id(automation: Automation) -> str | None:
    """Resolve the session id a loop targets for writes/gating."""
    return automation.thread_id


def _iteration_client_id(automation: Automation, automation_run_id: int) -> str:
    """Identify one durable scheduler attempt."""

    generation = automation.created_at.isoformat(timespec="microseconds")
    return f"loop:{automation.task_id}:{generation}:{automation.iteration_count + 1}:{automation_run_id}"


def _automation_tool_scope(automation: Automation) -> ScopeKey:
    """Return an automation's declared tool authority without widening it."""

    return automation.tool_scope


async def _automation_chat_model(runtime: Runtime, automation: Automation) -> str | None:
    """Prefer the scheduled automation's explicit model over its channel default."""

    if automation.model is not None:
        return automation.model
    return await runtime.resolve_session_chat_model(_loop_target_id(automation))


def _get_or_create_session_lock(locks: dict[str, asyncio.Lock], session_id: str) -> asyncio.Lock:
    lock = locks.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        locks[session_id] = lock
    return lock


async def _recover_interrupted_chat_runs(
    runtime: Runtime,
    resume: Callable[[str, str], Awaitable[dict[str, str] | None]],
) -> None:
    for interrupted_run in await runtime.session_service.store.list_interrupted_chat_runs():
        metadata = interrupted_run["metadata"]
        automation_task_id = metadata.get("automation_id")
        is_automation_run = bool(automation_task_id) and metadata.get("loop_task_id") == automation_task_id
        if is_automation_run:
            await runtime.stores.automations.bind_interrupted_chat_run(
                automation_task_id,
                interrupted_run["run_id"],
                interrupted_run["session_id"],
            )
        try:
            resumed = await resume(
                interrupted_run["run_id"],
                interrupted_run["session_id"],
            )
        except Exception:
            _logger.exception("Failed to resume interrupted run %s", interrupted_run["run_id"])
            resumed = None
        if resumed is not None and resumed["run_id"] == interrupted_run["run_id"]:
            if is_automation_run and not await runtime.stores.automations.refresh_detached_chat_run(
                automation_task_id,
                interrupted_run["run_id"],
                interrupted_run["session_id"],
                datetime.now(UTC),
            ):
                raise RuntimeError(f"resumed automation chat {interrupted_run['run_id']} has no detached run binding")
            continue
        if not is_automation_run:
            continue
        await runtime.automation.outbox_runtime.handle_run_failed(
            RunFailed(
                run_id=interrupted_run["run_id"],
                session_id=interrupted_run["session_id"],
                error="chat run could not resume after server restart",
                automation_task_id=automation_task_id,
            )
        )


async def _enqueue_background_respawns(session_store, outbox_store) -> None:
    """Boot pass: re-enqueue interrupted detached agents from their stored
    SpawnSpec. The attempt counter is incremented durably BEFORE the enqueue so
    a crash between the two can only under-spawn, never spawn unbounded."""
    for run_row in await session_store.list_respawnable_background_agent_runs(
        max_attempts=BACKGROUND_AGENT_MAX_SPAWN_ATTEMPTS,
    ):
        try:
            attempt = await session_store.increment_background_agent_spawn_attempts(
                run_row["session_id"],
                run_row["task_id"],
            )
            await outbox_store.enqueue_agent_run_requested(
                session_id=run_row["session_id"],
                task_id=run_row["task_id"],
                attempt=attempt,
            )
        except Exception:
            _logger.exception("Failed to enqueue background agent respawn %s", run_row["task_id"])


def _install_shutdown_handlers(runtime: Runtime, bus_registry: BusRegistry) -> None:
    """Intercept SIGINT/SIGTERM to close SSE streams before uvicorn's timeout.

    Uvicorn waits for HTTP connections to close before running lifespan
    teardown, but SSE streams never finish on their own.  We wrap the
    existing signal handlers to push a sentinel into every SSE queue and
    cancel active runs first, so connections close promptly.
    Pattern from sse-starlette (AppStatus).
    """
    for sig in (signal.SIGINT, signal.SIGTERM):
        original = signal.getsignal(sig)

        def _handler(signum: int, frame, _orig=original) -> None:
            bus_registry.close_all_sync()
            if callable(_orig):
                _orig(signum, frame)

        signal.signal(sig, _handler)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await refresh_codex_models()
    runtime = Runtime()
    await runtime.connect()
    # Bound the durable tool-result store on startup: prune offloaded results past
    # the retention window so it can't accumulate across sessions (it had grown to
    # ~5GB, which made an agent grepping it never converge — the CPU runaway).
    # Best-effort: a store-permission/IO error must not block serving requests.
    try:
        await asyncio.to_thread(prune_offload_store)
    except Exception:
        _logger.warning("tool-result store prune failed on startup; continuing", exc_info=True)
    # Titles are display-only now; stored title-style wikilinks become path links once.
    if runtime.wiki_service is not None:
        try:
            await asyncio.to_thread(migrate_title_links, runtime.wiki_service)
        except Exception:
            _logger.warning("wiki title-link migration failed on startup; continuing", exc_info=True)
    bus_registry = await _create_bus_registry(runtime)
    runtime.scheduler.set_bus_registry(bus_registry)

    # Route session lifecycle events (SESSION_CREATED / SESSION_ACTIVITY)
    # onto the global automation stream so the sidebar reflects sessions
    # the user didn't open themselves (automation channels, agent spawns)
    # without a reload.
    if runtime.session_service:

        async def _publish_session_event(event):
            await bus_registry.get_or_create(AUTOMATION_BUS_KEY).emit(event)

        runtime.session_service.set_event_sink(_publish_session_event)

    # Areas: a container's automations/sessions/asks, keyed by area_id
    # (areas and areas are one concept; an area = an area with
    # capabilities). Asks live under ~/.arden next to the other flat-file
    # stores; callables read live off the session/automation stores so
    # overview()/detail() never go stale. Reuse AutomationRuntime's
    # instances (not fresh ones) — AskStore caches in memory after __init__,
    # so a second instance would never see agent-nominated asks the area
    # agent handler upserts through AutomationRuntime.area_asks.
    area_asks = runtime.automation.area_asks
    app.state.area_lifecycle = AreaLifecycleService(
        sessions=runtime.session_service,
        sync_custodian=runtime.automation.sync_area_custodian,
        disable_custodian=runtime.automation.disable_area_custodian,
    )
    app.state.area_pages = AreaPageService(
        wiki=runtime.wiki_service,
        sessions=runtime.session_service,
        lifecycle=app.state.area_lifecycle,
        project_wiki_state=runtime.project_wiki_change_after_commit,
    )

    def _area_get_page(page_path: str):
        if runtime.wiki_service is None:
            raise KeyError(page_path)
        record = next(
            (candidate for candidate in runtime.wiki_service.readable_pages() if candidate.resource.path == page_path),
            None,
        )
        if record is None:
            raise KeyError(page_path)
        return record.page

    # AreaService's injected callables are synchronous (Task 5's contract),
    # but the session/automation stores are async-only. A per-request async
    # hydrate snapshots the live data into these closures right before each
    # sync AreaService call, so the callables stay plain sync lookups over
    # a fresh snapshot instead of needing their own event loop.
    area_snapshot: dict[str, object] = {
        "sessions": [],
        "approvals": [],
        "automations": [],
        "automation_runs": {},
        "records": [],
        "areas": [],
        "work": {},
        "work_brief": {"done": [], "in_progress": []},
    }

    async def hydrate_area_snapshot() -> None:
        sessions = await runtime.session_service.list_sessions(limit=1000) if runtime.session_service else []
        # Pending approvals only exist under a live run, so scan the in-memory
        # active runs instead of querying every session row.
        approvals: list[dict] = []
        if runtime.session_service:
            for session_id in {run.session_id for run in runtime.run_registry.list_active_runs()}:
                approvals.extend(await runtime.session_service.store.list_pending_tool_approvals(session_id))
        automations = await runtime.stores.automations.list_all() if runtime.stores else []
        automation_runs = await runtime.stores.automations.latest_runs() if runtime.stores else {}
        areas = await runtime.session_service.list_areas() if runtime.session_service else []
        area_ids = {area["area_id"] for area in areas}
        work_rows = await asyncio.gather(*(runtime.stores.area_work.snapshot(area_id) for area_id in area_ids))
        area_snapshot["sessions"] = sessions
        area_snapshot["approvals"] = approvals
        area_snapshot["automations"] = automations
        area_snapshot["automation_runs"] = automation_runs
        area_snapshot["records"] = areas
        area_snapshot["areas"] = areas_from_records(areas)
        area_snapshot["work"] = dict(zip(area_ids, work_rows, strict=True))
        area_snapshot["work_brief"] = await runtime.stores.area_work.brief(area_ids)

    def _area_pending_approvals() -> list[dict]:
        return area_snapshot["approvals"]

    def _area_session_area(session_id: str) -> str | None:
        area_ids = {s.key for s in area_snapshot["areas"]}
        for row in area_snapshot["sessions"]:
            if row["session_id"] == session_id:
                pid = row.get("area_id")
                return pid if pid in area_ids else None
        return None

    def _area_automations(key: str) -> list[dict]:
        return [
            {
                "name": a.name,
                "task_id": a.task_id,
                "thread_id": a.thread_id,
                "enabled": a.enabled,
                "last_result": a.last_result,
                "last_run_at": a.last_run_at.isoformat() if a.last_run_at else None,
                "running_since": a.running_since.isoformat() if a.running_since else None,
                "next_run_at": a.next_run_at.isoformat() if a.next_run_at else None,
                "latest_run": area_snapshot["automation_runs"].get(a.task_id),
            }
            for a in area_snapshot["automations"]
            if area_automation_match(a.task_id, key)
        ]

    def _area_sessions(key: str) -> list[dict]:
        # Primary conversations only — the room's activity list shows what
        # the USER talked about, not child agent sessions or the agent's own
        # channel (mirrors the sidebar's primary-session filter).
        return [
            row
            for row in area_snapshot["sessions"]
            if row.get("area_id") == key and row.get("session_type") == "chat" and not row.get("parent_session_id")
        ]

    def _get_area_record(area_id: str) -> dict | None:
        for p in area_snapshot["records"]:
            if p["area_id"] == area_id:
                return p
        return None

    def _custodian_state(key: str) -> dict:
        cust = runtime.automation.custodians
        state = dict(cust.state(key))
        state["runs_today_display"] = cust.runs_today(key)
        return state

    app.state.area_service = AreaService(
        areas=lambda: area_snapshot["areas"],
        asks=area_asks,
        get_page=_area_get_page,
        pending_approvals=_area_pending_approvals,
        session_area=_area_session_area,
        area_automations=_area_automations,
        area_sessions=_area_sessions,
        get_area=_get_area_record,
        custodian_state=_custodian_state,
        area_work=lambda key: area_snapshot["work"].get(key, AreaWorkSnapshot()),
        work_brief=lambda area_ids: area_snapshot["work_brief"],
    )
    app.state.hydrate_area_snapshot = hydrate_area_snapshot

    async def _publish_areas_changed(keys: list[str]) -> None:
        await bus_registry.get_or_create(AUTOMATION_BUS_KEY).emit(AreasChangedEvent(keys=keys))

    app.state.emit_areas_changed = _publish_areas_changed

    # Per-session write locks. The post dispatcher holds one for its full
    # lifetime so two concurrent post-mode dispatches against the same
    # target session can't trample each other's tail-end load→save window.
    session_write_locks: dict[str, asyncio.Lock] = {}

    async def _dispatch_session_message(
        session_id: str,
        message: str,
        client_id: str | None = None,
        skip_approvals: bool | None = False,
        images: list[dict] | None = None,
        tool_scope: ScopeKey | None = None,
        chat_model: str | None = None,
        loop_task_id: str | None = None,
        automation_id: str | None = None,
        queue_if_busy: bool = True,
    ) -> str | None:
        if chat_model is None:
            chat_model = await runtime.resolve_session_chat_model(session_id)
        result = await submit_chat_message(
            runtime.run_registry,
            lambda: runtime.build_chat_deps(chat_model=chat_model),
            bus_registry,
            message=message,
            session_id=session_id,
            skip_approvals=skip_approvals,
            images=images,
            client_id=client_id,
            session_service=runtime.session_service,
            tool_scope=tool_scope,
            loop_task_id=loop_task_id,
            automation_id=automation_id,
            queue_if_busy=queue_if_busy,
        )
        if result.get("status") == "busy":
            raise RunDeferred(f"target session {session_id} is busy")
        if not isinstance(result, dict) or result.get("status") != "running":
            return None
        return result.get("run_id")

    async def _dispatch_iteration(
        automation: Automation,
        context: str | dict | None,
        automation_run_id: int,
    ) -> str:
        # Iteration loops are autonomous: the user already approved the
        # loop at creation (via the create_loop approval card), so
        # subsequent iterations should skip per-tool approvals. Matches
        # the same auto_approve→skip_approvals convention the regular
        # automation path uses in scheduler._run_agent.
        #
        # When the run was triggered by an event (e.g. a Slack message),
        # `context` carries the rendered event block — fold it into the
        # turn via AUTOMATION_PROMPT, mirroring scheduler._run_agent. With
        # no context the prompt collapses to the bare instructions, so
        # non-event iterations submit exactly what they did before.
        manual, context = split_manual_flag(context)
        ctx_str = json.dumps(context) if isinstance(context, dict) else context
        area_id = automation.task_id.removeprefix("area:")
        iteration = automation.iteration_count + 1
        client_id = _iteration_client_id(automation, automation_run_id)
        message = AUTOMATION_PROMPT.render(prompt=automation.prompt, context=ctx_str) if ctx_str else automation.prompt
        skip_approvals = automation.auto_approve
        custodian_task = is_custodian_task_id(automation.task_id)
        if custodian_task:
            delivery = runtime.automation.custodians.pending_delivery(area_id, iteration)
            if delivery is None:
                record = await runtime.session_service.get_area(area_id)
                attention = (record or {}).get("attention") or "ambient"
                work_snapshot = await runtime.stores.area_work.snapshot(area_id)

                quiet_streak = runtime.automation.custodians.quiet_streak(area_id)

                def build_message(woken_by: tuple[str, ...]) -> str:
                    parts = [
                        render_work_context(work_snapshot),
                        f"CONSECUTIVE QUIET RUNS (no asks, no material progress): {quiet_streak}",
                    ]
                    if woken_by:
                        parts.append(
                            "WOKEN BY (events since your last run):\n" + "\n".join(f"- {event}" for event in woken_by)
                        )
                    if iteration == 1 and runtime.automation.custodians.needs_intake(area_id):
                        parts.append(INTAKE_ADDENDUM.strip())
                    run_context = "\n\n".join(([ctx_str] if ctx_str else []) + parts)
                    return AUTOMATION_PROMPT.render(prompt=automation.prompt, context=run_context)

                allowed, delivery = runtime.automation.custodians.begin_or_resume_delivery(
                    area_id,
                    iteration=iteration,
                    client_id=client_id,
                    attention=attention,
                    manual=manual,
                    skip_approvals=skip_approvals,
                    build_message=build_message,
                )
                if not allowed or delivery is None:
                    raise RunSkipped("autonomous daily run cap reached")
            client_id = delivery.client_id
            message = delivery.message
            skip_approvals = delivery.skip_approvals
        try:
            run_id = await _dispatch_session_message(
                _loop_target_id(automation) or "",
                message,
                client_id=client_id,
                skip_approvals=skip_approvals,
                tool_scope=_automation_tool_scope(automation),
                chat_model=await _automation_chat_model(runtime, automation),
                loop_task_id=automation.task_id,
                automation_id=automation.task_id,
                queue_if_busy=False,
            )
        except BaseException:
            if custodian_task:
                runtime.automation.custodians.abandon_delivery(area_id, client_id=client_id)
            raise
        if run_id is None:
            if custodian_task:
                runtime.automation.custodians.abandon_delivery(area_id, client_id=client_id)
            raise RuntimeError(f"Iteration automation {automation.task_id} did not start a chat run")
        if custodian_task:
            runtime.automation.custodians.bind_delivery_run(area_id, client_id=client_id, run_id=run_id)
        return run_id

    runtime.scheduler.set_iteration_dispatcher(_dispatch_iteration)
    app.state.request_area_wake = runtime.automation.request_area_wake
    runtime.dispatch_session_message = _dispatch_session_message
    runtime.app_control = AppControlService(
        bus_registry=bus_registry,
        dispatch_session_message=_dispatch_session_message,
        asks=area_asks,
    )

    async def _resume_suspended_chat_run(run_id: str, session_id: str) -> object:
        chat_model = await runtime.resolve_session_chat_model(session_id)
        return await resume_suspended_chat_run(
            runtime.run_registry,
            lambda: runtime.build_chat_deps(chat_model=chat_model),
            bus_registry,
            run_id=run_id,
            session_id=session_id,
            session_service=runtime.session_service,
        )

    runtime.resume_suspended_chat_run = _resume_suspended_chat_run

    async def _respawn_background_agent(session_id: str, task_id: str) -> None:
        chat_model = await runtime.resolve_session_chat_model(session_id)
        await respawn_background_agent(
            runtime.run_registry,
            lambda: runtime.build_chat_deps(chat_model=chat_model),
            bus_registry,
            session_id=session_id,
            task_id=task_id,
            session_service=runtime.session_service,
        )

    runtime.respawn_background_agent = _respawn_background_agent
    runtime.outbox_runtime.set_agent_run_handler(_respawn_background_agent)

    async def _dispatch_post(automation: Automation, context: str | dict | None = None) -> CompletedAgentRun:
        # Post mode: run the agent fresh (no session history), then write
        # the agent's final text into the target session as an assistant
        # message. The chat UI picks it up on the next history fetch —
        # no live SSE for now (can be wired later if needed).
        #
        # The whole body runs under a per-session write lock so concurrent
        # post-mode dispatches against the same target session serialize
        # their load→save windows. SessionStore also serializes chat save /
        # progress writes per session, so post-vs-chat history writes share
        # the same one-writer-at-a-time model.
        if not runtime.session_service:
            raise RuntimeError("Post automation session service is unavailable")
        target_id = _loop_target_id(automation)
        if not target_id:
            raise RuntimeError(f"Post automation {automation.task_id} has no target session")

        async with _get_or_create_session_lock(session_write_locks, target_id):
            _, context = split_manual_flag(context)
            ctx_str = json.dumps(context) if isinstance(context, dict) else context
            prompt = AUTOMATION_PROMPT.render(prompt=automation.prompt, context=ctx_str)
            request = RunRequest(
                prompt=prompt,
                auto_approve=automation.auto_approve,
                source_id=automation.task_id,
                model=automation.model,
                skip_approvals=automation.auto_approve,
                automation_id=automation.task_id,
                tool_scope=_automation_tool_scope(automation),
            )

            deps = runtime.build_operator_deps()
            if bus_registry:
                event_store = runtime.session_service.store if runtime.session_service else None
                await prime_bus_cursor_from_store(bus_registry, AUTOMATION_BUS_KEY, event_store)
                bus = bus_registry.get_or_create(AUTOMATION_BUS_KEY)
                run_result = await run_agent_streaming(deps, request, bus, automation.task_id)
            else:
                run_result = await run_agent(deps, request)
            text = run_result.output
            if text:
                # Append as an assistant message into the target session.
                data = await runtime.session_service.load(target_id)
                if data is not None:
                    data.messages.append({"role": Role.ASSISTANT, "content": text})
                    await runtime.session_service.save_progress(data.state, data.messages)
            return CompletedAgentRun(run_result.run_id, text)

    runtime.scheduler.set_post_dispatcher(_dispatch_post)

    def _loop_can_fire(automation: Automation) -> bool:
        # Skip the tick if the loop's target session has an active user
        # run. Applies to both modes:
        #  • Iteration would queue the loop prompt into the active run's
        #    inject_queue, rendering inside the user's "Worked" collapse
        #    instead of as a fresh chat turn.
        #  • Post would race the in-flight run on session_service writes.
        # handle_run_completed fires deferred loops the moment the
        # session goes idle.
        # Target ID is thread_id — the sole session binding after field
        # consolidation.
        target_id = _loop_target_id(automation)
        if not target_id:
            return True
        active = runtime.run_registry.get_accepting_run(target_id)
        return active is None

    runtime.scheduler.set_loop_fire_gate(_loop_can_fire)
    await _recover_interrupted_chat_runs(runtime, _resume_suspended_chat_run)
    await runtime.start_scheduler()
    runtime.start_monitor()
    for completion in await runtime.session_service.store.list_undelivered_background_completions():
        try:
            session_id = completion["session_id"]
            registry = runtime.run_registry.get_background_registry(session_id)
            registry.claim_completion = runtime.session_service.store.claim_background_agent_completion
            registry.mark_completion_delivered = runtime.session_service.store.mark_background_completion_delivered

            async def _redeliver(messages: list[dict], target_session_id: str = session_id) -> None:
                saved = await runtime.session_service.load(target_session_id)
                saved_client_ids = {
                    message.get("client_id")
                    for message in (saved.messages if saved else [])
                    if message.get("client_id")
                }
                for message in messages:
                    client_id = message.get("client_id")
                    if client_id in saved_client_ids:
                        continue
                    content = message.get("content")
                    if isinstance(content, str) and content:
                        await _dispatch_session_message(
                            target_session_id,
                            content,
                            client_id if isinstance(client_id, str) else None,
                            True,
                        )

            registry.on_result = _redeliver
            await prime_bus_cursor_from_store(bus_registry, session_id, runtime.session_service.store)
            await registry.deliver_result(
                task_id=completion["task_id"],
                result=completion.get("result_text") or "",
                label=completion["command"],
                status=completion["status"],
                emit=bus_registry.get_or_create(session_id).emit,
                child_session_id=completion.get("child_session_id"),
                parent_tool_call_id=completion.get("parent_tool_call_id"),
                agent_type=completion.get("agent_type"),
                wait=completion.get("wait"),
            )
        except Exception:
            _logger.exception("Failed to redeliver background completion %s", completion["completion_id"])
    await _enqueue_background_respawns(runtime.session_service.store, runtime.stores.outbox)
    app.state.runtime = runtime
    app.state.bus_registry = bus_registry
    _install_shutdown_handlers(runtime, bus_registry)

    try:
        yield
    except asyncio.CancelledError:
        pass

    await bus_registry.close_all()
    await runtime.close()


app = FastAPI(
    title="Arden",
    description="Personal entropy reduction system - API server",
    version=version("arden"),
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


app.add_middleware(AuthMiddleware)
app.add_middleware(TracingMiddleware)


app.include_router(gmail_router)
app.include_router(google_router)
app.include_router(automation_router)
app.include_router(chat_router)
app.include_router(context_router)
app.include_router(dev_runtime_router)
app.include_router(executor_router)
app.include_router(ops_router)
app.include_router(providers_router)
app.include_router(runtime_info_router)
app.include_router(setup_router)
app.include_router(session_router)
app.include_router(settings_router)
app.include_router(skills_router)
app.include_router(mcp_router)
app.include_router(loops_router)
app.include_router(facts_router)
app.include_router(wiki_pages_router)
app.include_router(wiki_router)
app.include_router(areas_router)
app.include_router(asks_router)
