import asyncio
import json
import signal
from contextlib import asynccontextmanager
from importlib.metadata import version

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ntrp.agent import Role
from ntrp.automation.models import Automation
from ntrp.automation.output_schemas import resolve_output_schema
from ntrp.automation.prompts import AUTOMATION_PROMPT, AUTOMATION_SUFFIX
from ntrp.automation.scheduler import AUTOMATION_BUS_KEY
from ntrp.constants import SLICES_FILE
from ntrp.core.tool_result_files import prune_offload_store
from ntrp.events.sse import MemoryChangedEvent, SlicesChangedEvent
from ntrp.logging import get_logger
from ntrp.memory.pages import parse_page
from ntrp.operator.runner import RunRequest, run_agent, run_agent_streaming
from ntrp.server.bus import BusRegistry, prime_bus_cursor_from_store
from ntrp.server.middleware import AuthMiddleware, TracingMiddleware
from ntrp.server.routers.automation import router as automation_router
from ntrp.server.routers.chat import router as chat_router
from ntrp.server.routers.context import router as context_router
from ntrp.server.routers.dev_runtime import router as dev_runtime_router
from ntrp.server.routers.gmail import router as gmail_router
from ntrp.server.routers.loops import router as loops_router
from ntrp.server.routers.mcp import router as mcp_router
from ntrp.server.routers.memory import router as memory_router
from ntrp.server.routers.ops import router as ops_router
from ntrp.server.routers.providers import router as providers_router
from ntrp.server.routers.runtime_info import router as runtime_info_router
from ntrp.server.routers.session import router as session_router
from ntrp.server.routers.settings import router as settings_router
from ntrp.server.routers.setup import router as setup_router
from ntrp.server.routers.skills import router as skills_router
from ntrp.server.routers.slices import router as slices_router
from ntrp.server.runtime import Runtime
from ntrp.services.chat import submit_chat_message
from ntrp.slices.migrate import migrate_slices_to_projects
from ntrp.slices.models import slices_from_projects
from ntrp.slices.projection import slice_automation_match
from ntrp.slices.service import SliceService

_logger = get_logger(__name__)


def _loop_target_id(automation: Automation) -> str | None:
    """Resolve the session id a loop targets for writes/gating."""
    return automation.thread_id


def _get_or_create_session_lock(locks: dict[str, asyncio.Lock], session_id: str) -> asyncio.Lock:
    lock = locks.get(session_id)
    if lock is None:
        lock = asyncio.Lock()
        locks[session_id] = lock
    return lock


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
    runtime = Runtime()
    await runtime.connect()
    runtime.start_indexing()
    # Bound the durable tool-result store on startup: prune offloaded results past
    # the retention window so it can't accumulate across sessions (it had grown to
    # ~5GB, which made an agent grepping it never converge — the CPU runaway).
    # Best-effort: a store-permission/IO error must not block serving requests.
    try:
        await asyncio.to_thread(prune_offload_store)
    except Exception:
        _logger.warning("tool-result store prune failed on startup; continuing", exc_info=True)
    bus_registry = BusRegistry(
        record_events=runtime.session_service.store.record_session_events if runtime.session_service else None,
    )
    runtime.scheduler.set_bus_registry(bus_registry)

    # Route session lifecycle events (SESSION_CREATED / SESSION_ACTIVITY)
    # onto the global automation stream so the sidebar reflects sessions
    # the user didn't open themselves (automation channels, agent spawns)
    # without a reload.
    if runtime.session_service:

        async def _publish_session_event(event):
            await bus_registry.get_or_create(AUTOMATION_BUS_KEY).emit(event)

        runtime.session_service.set_event_sink(_publish_session_event)

        # Slices↔projects unification: one-shot fold of slices.json into the
        # projects table (capabilities + re-keyed asks/automations/sessions).
        # No-op once the file has been renamed to .migrated.
        await migrate_slices_to_projects(
            slices_file=runtime.config.ntrp_dir / SLICES_FILE,
            session_service=runtime.session_service,
            ask_store=runtime.automation.slice_asks,
            automation_store=runtime.stores.automations,
            session_store=runtime.session_service.store,
        )

    # Live memory vault: the store polls the memory dir for external edits
    # (Obsidian, feed automations, git) and fans each absorbed batch out on the
    # global stream so the desktop memory view refreshes itself — no restarts.
    async def _publish_memory_changed(paths: list[str]) -> None:
        await bus_registry.get_or_create(AUTOMATION_BUS_KEY).emit(MemoryChangedEvent(paths=paths))

    runtime.knowledge.start_memory_watch(_publish_memory_changed)

    # Slices: a container's automations/sessions/asks, keyed by project_id
    # (slices and projects are one concept; a slice = a project with
    # capabilities). Asks live under ~/.ntrp next to the other flat-file
    # stores; callables read live off the session/automation stores so
    # overview()/detail() never go stale. Reuse AutomationRuntime's
    # instances (not fresh ones) — AskStore caches in memory after __init__,
    # so a second instance would never see agent-nominated asks the slice
    # agent handler upserts through AutomationRuntime.slice_asks.
    slice_asks = runtime.automation.slice_asks

    def _slice_get_page(page_path: str):
        full_path = runtime.config.memory_artifacts_dir / page_path
        text = full_path.read_text(encoding="utf-8") if full_path.exists() else ""
        return parse_page(text)

    # SliceService's injected callables are synchronous (Task 5's contract),
    # but the session/automation stores are async-only. A per-request async
    # hydrate snapshots the live data into these closures right before each
    # sync SliceService call, so the callables stay plain sync lookups over
    # a fresh snapshot instead of needing their own event loop.
    slice_snapshot: dict[str, object] = {
        "sessions": [], "approvals": [], "automations": [], "projects": [], "slices": [],
    }

    async def hydrate_slice_snapshot() -> None:
        sessions = await runtime.session_service.list_sessions(limit=1000) if runtime.session_service else []
        # Pending approvals only exist under a live run, so scan the in-memory
        # active runs instead of querying every session row.
        approvals: list[dict] = []
        if runtime.session_service:
            for session_id in {run.session_id for run in runtime.run_registry.list_active_runs()}:
                approvals.extend(await runtime.session_service.store.list_pending_tool_approvals(session_id))
        automations = await runtime.stores.automations.list_all() if runtime.stores else []
        projects = await runtime.session_service.list_projects() if runtime.session_service else []
        slice_snapshot["sessions"] = sessions
        slice_snapshot["approvals"] = approvals
        slice_snapshot["automations"] = automations
        slice_snapshot["projects"] = projects
        slice_snapshot["slices"] = slices_from_projects(projects)

    def _slice_pending_approvals() -> list[dict]:
        return slice_snapshot["approvals"]

    def _slice_session_slice(session_id: str) -> str | None:
        slice_ids = {s.key for s in slice_snapshot["slices"]}
        for row in slice_snapshot["sessions"]:
            if row["session_id"] == session_id:
                pid = row.get("project_id")
                return pid if pid in slice_ids else None
        return None

    def _slice_automations(key: str) -> list[dict]:
        return [
            {
                "name": a.name,
                "task_id": a.task_id,
                "thread_id": a.thread_id,
                "last_result": a.last_result,
                "last_run_at": a.last_run_at.isoformat() if a.last_run_at else None,
                "running_since": a.running_since.isoformat() if a.running_since else None,
                "next_run_at": a.next_run_at.isoformat() if a.next_run_at else None,
            }
            for a in slice_snapshot["automations"]
            if slice_automation_match(a.task_id, key)
        ]

    def _slice_sessions(key: str) -> list[dict]:
        # Primary conversations only — the room's activity list shows what
        # the USER talked about, not child agent sessions or the agent's own
        # channel (mirrors the sidebar's primary-session filter).
        return [
            row
            for row in slice_snapshot["sessions"]
            if row.get("project_id") == key
            and row.get("session_type") == "chat"
            and not row.get("parent_session_id")
        ]

    def _slice_get_project(project_id: str) -> dict | None:
        for p in slice_snapshot["projects"]:
            if p["project_id"] == project_id:
                return p
        return None

    app.state.slice_suggestions = runtime.automation.slice_suggestions
    app.state.slice_service = SliceService(
        slices=lambda: slice_snapshot["slices"],
        asks=slice_asks,
        get_page=_slice_get_page,
        pending_approvals=_slice_pending_approvals,
        session_slice=_slice_session_slice,
        slice_automations=_slice_automations,
        slice_sessions=_slice_sessions,
        get_project=_slice_get_project,
    )
    app.state.hydrate_slice_snapshot = hydrate_slice_snapshot

    async def _publish_slices_changed(keys: list[str]) -> None:
        await bus_registry.get_or_create(AUTOMATION_BUS_KEY).emit(SlicesChangedEvent(keys=keys))

    app.state.emit_slices_changed = _publish_slices_changed

    # Per-session write locks. The post dispatcher holds one for its full
    # lifetime so two concurrent post-mode dispatches against the same
    # target session can't trample each other's tail-end load→save window.
    session_write_locks: dict[str, asyncio.Lock] = {}

    async def _dispatch_session_message(
        session_id: str,
        message: str,
        client_id: str | None = None,
        skip_approvals: bool | None = False,
        tool_scope: tuple[str, ...] | None = None,
        output_schema: type[BaseModel] | None = None,
    ) -> str | None:
        chat_model = await runtime.resolve_session_chat_model(session_id)
        result = await submit_chat_message(
            runtime.run_registry,
            lambda: runtime.build_chat_deps(chat_model=chat_model),
            bus_registry,
            message=message,
            session_id=session_id,
            skip_approvals=skip_approvals,
            client_id=client_id,
            session_service=runtime.session_service,
            tool_scope=tool_scope,
            output_schema=output_schema,
        )
        return result.get("run_id") if isinstance(result, dict) else None

    async def _dispatch_iteration(automation: Automation, context: str | dict | None = None) -> str | None:
        # Iteration loops are autonomous: the user already approved the
        # loop at creation (via the create_loop approval card), so
        # subsequent iterations should skip per-tool approvals. Matches
        # the same auto_approve→skip_approvals convention the regular
        # automation path uses in scheduler._run_agent.
        #
        # When the run was triggered by an event (e.g. a Slack message),
        # `context` carries the rendered event block — fold it into the
        # turn via AUTOMATION_PROMPT, mirroring scheduler._run_agent. With
        # no context the prompt collapses to the bare description, so
        # non-event iterations submit exactly what they did before.
        ctx_str = json.dumps(context) if isinstance(context, dict) else context
        message = (
            AUTOMATION_PROMPT.render(description=automation.description, context=ctx_str)
            if context
            else automation.description
        )
        return await _dispatch_session_message(
            _loop_target_id(automation) or "",
            message,
            client_id=f"loop:{automation.task_id}:{automation.iteration_count + 1}",
            skip_approvals=automation.auto_approve,
            tool_scope=tuple(automation.tool_scope) if automation.tool_scope else None,
            output_schema=resolve_output_schema(automation.output_schema),
        )

    runtime.scheduler.set_iteration_dispatcher(_dispatch_iteration)
    runtime.dispatch_session_message = _dispatch_session_message

    async def _dispatch_post(automation: Automation, context: str | dict | None = None) -> str | None:
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
            return None
        target_id = _loop_target_id(automation)
        if not target_id:
            return None

        async with _get_or_create_session_lock(session_write_locks, target_id):
            ctx_str = json.dumps(context) if isinstance(context, dict) else context
            prompt = AUTOMATION_PROMPT.render(description=automation.description, context=ctx_str)
            request = RunRequest(
                prompt=prompt,
                prompt_suffix=AUTOMATION_SUFFIX,
                auto_approve=automation.auto_approve,
                source_id=automation.task_id,
                model=automation.model,
                skip_approvals=automation.auto_approve,
                automation_id=automation.task_id,
                tool_scope=tuple(automation.tool_scope) if automation.tool_scope else None,
                output_schema=resolve_output_schema(automation.output_schema),
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
            if not text:
                return None

            # Append as an assistant message into the target session.
            data = await runtime.session_service.load(target_id)
            if data is None:
                return text
            data.messages.append({"role": Role.ASSISTANT, "content": text})
            await runtime.session_service.save_progress(data.state, data.messages)
            return text

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
    await runtime.start_scheduler()
    runtime.start_monitor()
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
    title="ntrp",
    description="Personal entropy reduction system - API server",
    version=version("ntrp"),
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
app.include_router(automation_router)
app.include_router(chat_router)
app.include_router(context_router)
app.include_router(dev_runtime_router)
app.include_router(ops_router)
app.include_router(providers_router)
app.include_router(runtime_info_router)
app.include_router(setup_router)
app.include_router(session_router)
app.include_router(settings_router)
app.include_router(skills_router)
app.include_router(mcp_router)
app.include_router(loops_router)
app.include_router(memory_router)
app.include_router(slices_router)
