from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from pydantic import ValidationError

import arden.database as database
from arden.automation.models import Automation
from arden.automation.scheduler import Scheduler
from arden.automation.service import AutomationService
from arden.automation.store import AutomationStore
from arden.automation.triggers import TimeTrigger
from arden.context.models import SessionState
from arden.tools.automation import (
    CreateAutomationInput,
    CreateLoopInput,
    LoopDoneInput,
    ScheduleWakeupInput,
    approve_create_automation,
    approve_create_loop,
    create_automation,
    create_loop,
    loop_done,
    schedule_wakeup,
)
from arden.tools.core.base import Tool
from arden.tools.core.context import (
    BackgroundTaskRegistry,
    IOBridge,
    RunContext,
    ToolContext,
    ToolExecution,
)
from arden.tools.core.registry import ToolRegistry
from arden.tools.core.types import ToolAction, ToolPolicy, ToolScope


@pytest_asyncio.fixture
async def store_and_svc(tmp_path: Path):
    from arden.context.store import SessionStore
    from arden.services.session import SessionService

    conn = await database.connect(tmp_path / "automation.db")
    store = AutomationStore(conn)
    await store.init_schema()
    session_conn = await database.connect(tmp_path / "sessions.db")
    session_store = SessionStore(session_conn)
    await session_store.init_schema()
    session_service = SessionService(session_store)
    await session_service.provision(name="active chat", session_id="sess-1")
    sched = Scheduler(store=store, build_deps=lambda: None)
    svc = AutomationService(store=store, scheduler=sched, session_service=session_service)
    now = datetime.now(UTC)
    loop = Automation(
        task_id="loop-1",
        name="x",
        description=None,
        description_source=None,
        prompt="watch CI",
        model=None,
        triggers=[TimeTrigger(every="5m")],
        enabled=True,
        created_at=now,
        next_run_at=now + timedelta(minutes=5),
        last_run_at=None,
        last_result=None,
        running_since=None,
        auto_approve=True,
        kind="loop",
        thread_id="sess-1",
    )
    await store.save(loop)
    try:
        yield store, svc
    finally:
        await session_conn.close()
        await conn.close()


class _FakeTool(Tool):
    description = "t"
    policy = ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL)

    async def execute(self, execution, **kwargs):  # pragma: no cover
        raise NotImplementedError

    def to_dict(self, name):
        return {"name": name}


def _registry() -> ToolRegistry:
    # tool_scope patterns are validated against registered names — mirror the
    # runtime registry for the names these tests grant.
    registry = ToolRegistry()
    for name in ("slack_search", "slack_post_message", "gmail_search", "read_email", "archive_session"):
        registry.register(name, _FakeTool())
    return registry


def _execution(svc: AutomationService, loop_task_id: str | None, session_id: str = "sess-1") -> ToolExecution:
    ctx = ToolContext(
        session_state=SessionState(session_id=session_id, started_at=datetime.now(UTC)),
        registry=_registry(),
        run=RunContext(run_id="run-1", loop_task_id=loop_task_id),
        io=IOBridge(),
        services={"automation": svc},
        background_tasks=BackgroundTaskRegistry(session_id="sess-1"),
    )
    return ToolExecution(tool_id="t1", tool_name="schedule_wakeup", ctx=ctx)


@pytest.mark.asyncio
async def test_schedule_wakeup_updates_next_run(store_and_svc):
    store, svc = store_and_svc
    execution = _execution(svc, loop_task_id="loop-1")

    before = datetime.now(UTC)
    result = await schedule_wakeup(execution, ScheduleWakeupInput(delay_seconds=300))
    assert not result.is_error

    loop = await store.get("loop-1")
    assert loop.next_run_at is not None
    # next_run_at should be ~now + 300s
    delta = (loop.next_run_at - before).total_seconds()
    assert 295 <= delta <= 305


@pytest.mark.asyncio
async def test_schedule_wakeup_refuses_outside_loop(store_and_svc):
    _, svc = store_and_svc
    execution = _execution(svc, loop_task_id=None)

    result = await schedule_wakeup(execution, ScheduleWakeupInput(delay_seconds=60))
    assert result.is_error
    assert "loop" in result.content.lower()


@pytest.mark.asyncio
async def test_loop_done_disables_loop(store_and_svc):
    store, svc = store_and_svc
    execution = _execution(svc, loop_task_id="loop-1")

    result = await loop_done(execution, LoopDoneInput(reason="CI green"))
    assert not result.is_error

    loop = await store.get("loop-1")
    assert loop.enabled is False


@pytest.mark.asyncio
async def test_loop_done_refuses_outside_loop(store_and_svc):
    _, svc = store_and_svc
    execution = _execution(svc, loop_task_id=None)

    result = await loop_done(execution, LoopDoneInput(reason="x"))
    assert result.is_error


def test_schedule_wakeup_input_enforces_min_delay():
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        ScheduleWakeupInput(delay_seconds=59)


def test_automation_display_description_is_bounded():
    import pydantic

    from arden.tools.automation import UpdateAutomationInput

    with pytest.raises(pydantic.ValidationError):
        CreateAutomationInput(
            name="bounded",
            prompt="Run the bounded automation.",
            description="x" * 221,
            trigger_type="time",
            every="1h",
        )
    with pytest.raises(pydantic.ValidationError):
        UpdateAutomationInput(task_id="bounded", description="x" * 221)


def test_create_automation_schema_does_not_expose_session_binding():
    properties = CreateAutomationInput.model_json_schema()["properties"]

    assert "thread_id" not in properties
    assert "read_history" not in properties


# --- create_automation / create_loop tool wiring for channel-aware fields ---


@pytest.mark.asyncio
async def test_create_automation_idempotency_claim_dedupes(store_and_svc):
    store, svc = store_and_svc
    execution = _execution(svc, loop_task_id=None)

    args = CreateAutomationInput(
        name="daily brief",
        description="Posts the morning brief.",
        prompt="Post the morning brief.",
        trigger_type="time",
        at="09:00",
    )

    first = await create_automation(
        execution,
        args.model_copy(update={"idempotency_key": "daily-brief-1", "idempotency_scope": "global"}),
    )
    assert not first.is_error
    assert "Created" in first.content or "created" in first.content.lower()

    second = await create_automation(
        execution,
        args.model_copy(update={"idempotency_key": "daily-brief-1", "idempotency_scope": "global"}),
    )
    assert not second.is_error
    assert "Skipped" in second.content

    created = next(a for a in await store.list_all() if a.name == "daily brief")
    channels = [
        session
        for session in await svc.session_service.list_sessions(limit=10)
        if session["origin_automation_id"] == created.task_id
    ]
    assert len(channels) == 1
    assert channels[0]["session_id"] == created.thread_id


@pytest.mark.asyncio
async def test_create_automation_cannot_bind_an_arbitrary_session(store_and_svc):
    store, svc = store_and_svc
    execution = _execution(svc, loop_task_id=None)

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CreateAutomationInput.model_validate(
            {
                "name": "channel automation",
                "description": "Posts into its own channel.",
                "prompt": "Post into its own channel.",
                "trigger_type": "time",
                "every": "1h",
                "thread_id": "sess-target",
                "read_history": True,
                "tool_scope": ["slack_search", "slack_post_message"],
            }
        )
    args = CreateAutomationInput(
        name="channel automation",
        description="Posts into its own channel.",
        prompt="Post into its own channel.",
        trigger_type="time",
        every="1h",
        tool_scope=["slack_search", "slack_post_message"],
    )
    result = await create_automation(execution, args)
    assert not result.is_error

    rows = await store.list_all()
    created = next(a for a in rows if a.name == "channel automation")
    assert created.thread_id != "sess-target"
    assert created.read_history is True
    assert created.tool_scope == ["slack_search", "slack_post_message"]
    channel = await svc.session_service.load(created.thread_id)
    assert channel is not None
    assert channel.state.session_type == "channel"
    assert channel.state.origin_automation_id == created.task_id


@pytest.mark.asyncio
async def test_approve_create_automation_shows_tool_scope(store_and_svc):
    _, svc = store_and_svc
    execution = _execution(svc, loop_task_id=None)
    args = CreateAutomationInput(
        name="scoped sender",
        description="Posts a reviewed update.",
        prompt="Post a reviewed update.",
        trigger_type="time",
        every="1h",
        auto_approve=True,
        tool_scope=["slack_search", "slack_post_message"],
    )

    info = await approve_create_automation(execution, args)

    assert info is not None
    assert "Tools: slack_search, slack_post_message" in (info.preview or "")


@pytest.mark.asyncio
async def test_update_automation_changes_tool_scope(store_and_svc):
    from arden.tools.automation import UpdateAutomationInput, update_automation

    store, svc = store_and_svc
    execution = _execution(svc, loop_task_id=None)
    await create_automation(
        execution,
        CreateAutomationInput(
            name="scope update",
            description="Reads the source first.",
            prompt="Read the source first.",
            trigger_type="time",
            every="1h",
        ),
    )
    created = next(a for a in await store.list_all() if a.name == "scope update")

    result = await update_automation(
        execution,
        UpdateAutomationInput(task_id=created.task_id, tool_scope=["gmail_search", "read_email"]),
    )

    assert not result.is_error
    updated = await store.get(created.task_id)
    assert updated.tool_scope == ["gmail_search", "read_email"]


class _FakeSlack:
    async def resolve_channel(self, name: str) -> tuple[str, str]:
        return f"C-{name}", name

    async def resolve_user(self, name: str) -> dict[str, str]:
        return {"id": f"U-{name}", "name": name}


@pytest.mark.asyncio
async def test_create_automation_message_trigger_resolves_channels(tmp_path: Path):
    from arden.automation.triggers import MessageTrigger
    from arden.context.store import SessionStore
    from arden.services.session import SessionService

    conn = await database.connect(tmp_path / "automation.db")
    store = AutomationStore(conn)
    await store.init_schema()
    session_conn = await database.connect(tmp_path / "sessions.db")
    session_store = SessionStore(session_conn)
    await session_store.init_schema()
    svc = AutomationService(
        store=store,
        scheduler=Scheduler(store=store, build_deps=lambda: None),
        session_service=SessionService(session_store),
        get_slack_client=lambda: _FakeSlack(),
    )
    execution = _execution(svc, loop_task_id=None)
    args = CreateAutomationInput(
        name="bug watch",
        description="Triages bug reports.",
        prompt="Triage bugs.",
        trigger_type="message",
        channels=["feel-good-inc", "eng-bugs"],
        from_user="sam",
        contains=["bug", "error"],
    )

    result = await create_automation(execution, args)
    assert not result.is_error, result.content

    triggers = [t for a in await store.list_all() for t in a.triggers if isinstance(t, MessageTrigger)]
    assert len(triggers) == 1
    trigger = triggers[0]
    assert trigger.channel_ids == ["C-feel-good-inc", "C-eng-bugs"]
    assert trigger.from_user_id == "U-sam"
    assert trigger.contains == ["bug", "error"]
    await session_conn.close()
    await conn.close()


@pytest.mark.asyncio
async def test_update_automation_to_message_trigger_resolves_channels(tmp_path: Path):
    from arden.automation.triggers import MessageTrigger
    from arden.context.store import SessionStore
    from arden.services.session import SessionService
    from arden.tools.automation import UpdateAutomationInput, update_automation

    conn = await database.connect(tmp_path / "automation.db")
    store = AutomationStore(conn)
    await store.init_schema()
    session_conn = await database.connect(tmp_path / "sessions.db")
    session_store = SessionStore(session_conn)
    await session_store.init_schema()
    svc = AutomationService(
        store=store,
        scheduler=Scheduler(store=store, build_deps=lambda: None),
        session_service=SessionService(session_store),
        get_slack_client=lambda: _FakeSlack(),
    )
    execution = _execution(svc, loop_task_id=None)
    await create_automation(
        execution,
        CreateAutomationInput(
            name="watch",
            description="Triages new items.",
            prompt="Triage new items.",
            trigger_type="time",
            every="1h",
        ),
    )
    task_id = next(a.task_id for a in await store.list_all())

    result = await update_automation(
        execution,
        UpdateAutomationInput(task_id=task_id, trigger_type="message", channels=["eng-bugs"], contains=["bug"]),
    )
    assert not result.is_error, result.content

    updated = await store.get(task_id)
    msg = [t for t in updated.triggers if isinstance(t, MessageTrigger)]
    assert len(msg) == 1
    assert msg[0].channel_ids == ["C-eng-bugs"]
    assert msg[0].contains == ["bug"]
    await session_conn.close()
    await conn.close()


@pytest.mark.asyncio
async def test_create_automation_defaults_parent_from_loop_ctx(store_and_svc):
    store, svc = store_and_svc
    execution = _execution(svc, loop_task_id="loop-1")

    args = CreateAutomationInput(
        name="child auto",
        description="Runs follow-up work from the loop.",
        prompt="Run follow-up work from the loop.",
        trigger_type="time",
        every="2h",
    )
    result = await create_automation(execution, args)
    assert not result.is_error

    rows = await store.list_all()
    child = next(a for a in rows if a.name == "child auto")
    assert child.parent_automation_id == "loop-1"


@pytest.mark.asyncio
async def test_create_loop_infers_parent_from_loop_ctx(store_and_svc):
    store, svc = store_and_svc
    execution = _execution(svc, loop_task_id="loop-1")

    result = await create_loop(
        execution,
        CreateLoopInput(prompt="watch CI again", every="5m"),
    )
    assert not result.is_error

    rows = await store.list_all()
    child = next(a for a in rows if a.prompt == "watch CI again")
    assert child.parent_automation_id == "loop-1"


@pytest.mark.asyncio
async def test_create_loop_explicit_parent_overrides_ctx(store_and_svc):
    store, svc = store_and_svc
    execution = _execution(svc, loop_task_id="loop-1")

    result = await create_loop(
        execution,
        CreateLoopInput(
            prompt="watch CI yet again",
            every="5m",
            parent_automation_id="explicit-parent",
        ),
    )
    assert not result.is_error

    rows = await store.list_all()
    child = next(a for a in rows if a.prompt == "watch CI yet again")
    assert child.parent_automation_id == "explicit-parent"


@pytest.mark.asyncio
async def test_create_loop_attaches_the_exact_current_chat(store_and_svc):
    store, svc = store_and_svc
    await svc.session_service.provision(name="other chat", session_id="sess-other")
    execution = _execution(svc, loop_task_id=None, session_id="sess-1")

    result = await create_loop(execution, CreateLoopInput(prompt="watch CI", every="5m"))

    assert not result.is_error
    loop = next(a for a in await store.list_all() if a.kind == "loop" and a.prompt == "watch CI")
    assert loop.thread_id == "sess-1"


@pytest.mark.asyncio
async def test_create_loop_rejects_missing_current_chat(store_and_svc):
    _, svc = store_and_svc
    execution = _execution(svc, loop_task_id=None, session_id="missing-chat")

    result = await create_loop(execution, CreateLoopInput(prompt="watch CI", every="5m"))

    assert result.is_error
    assert result.outcome.error.code == "invalid_arguments"


@pytest.mark.asyncio
async def test_create_loop_rejects_archived_current_chat(store_and_svc):
    _, svc = store_and_svc
    assert await svc.session_service.archive("sess-1")
    execution = _execution(svc, loop_task_id=None)

    result = await create_loop(execution, CreateLoopInput(prompt="watch CI", every="5m"))

    assert result.is_error
    assert result.outcome.error.code == "invalid_arguments"


@pytest.mark.asyncio
async def test_create_automation_run_scope_missing_parent_errors(store_and_svc):
    """idempotency_scope='run' with a non-existent parent must fail loudly,
    not silently collapse to global scope."""
    _, svc = store_and_svc
    execution = _execution(svc, loop_task_id=None)

    args = CreateAutomationInput(
        name="orphan",
        description="Tests a missing parent.",
        prompt="This should fail because its parent is missing.",
        trigger_type="time",
        at="09:00",
        parent_automation_id="ghost",
        idempotency_key="k1",
        idempotency_scope="run",
    )
    result = await create_automation(execution, args)
    assert result.is_error
    assert "ghost" in result.content
    assert "run" in result.content


@pytest.mark.asyncio
async def test_create_loop_attempt_scope_missing_parent_errors(store_and_svc):
    """Same protection on create_loop."""
    _, svc = store_and_svc
    execution = _execution(svc, loop_task_id=None)

    result = await create_loop(
        execution,
        CreateLoopInput(
            prompt="x",
            every="5m",
            parent_automation_id="ghost",
            idempotency_key="k1",
            idempotency_scope="attempt",
            attempt_n=0,
        ),
    )
    assert result.is_error
    assert "ghost" in result.content
    assert "attempt" in result.content


@pytest.mark.asyncio
async def test_approve_create_automation_flags_missing_parent(store_and_svc):
    """Approval preview should surface the same missing-parent conflict
    that execute will hit."""
    _, svc = store_and_svc
    execution = _execution(svc, loop_task_id=None)

    args = CreateAutomationInput(
        name="orphan",
        description="Tests a missing parent warning.",
        prompt="This should warn because its parent is missing.",
        trigger_type="time",
        at="09:00",
        parent_automation_id="ghost",
        idempotency_key="k1",
        idempotency_scope="run",
    )
    info = await approve_create_automation(execution, args)
    assert info is not None
    assert "ghost" in info.preview
    assert "missing" in info.preview.lower() or "will fail" in info.preview.lower()


@pytest.mark.asyncio
async def test_approve_create_loop_flags_missing_parent(store_and_svc):
    """Same preview-vs-execute alignment for create_loop."""
    _, svc = store_and_svc
    execution = _execution(svc, loop_task_id=None)

    args = CreateLoopInput(
        prompt="watch",
        every="5m",
        parent_automation_id="ghost",
        idempotency_key="k1",
        idempotency_scope="run",
    )
    info = await approve_create_loop(execution, args)
    assert info is not None
    assert "ghost" in info.preview
    assert "missing" in info.preview.lower() or "will fail" in info.preview.lower()


@pytest.mark.asyncio
async def test_create_automation_rejects_unknown_tool_scope_names(store_and_svc):
    # A typo'd scope pattern must fail loudly with candidates — storing it
    # would silently strip the automation of the tool its author meant.
    _, svc = store_and_svc
    execution = _execution(svc, loop_task_id=None)

    result = await create_automation(
        execution,
        CreateAutomationInput(
            name="typo scope",
            description="Archives chats.",
            prompt="Archive old chats.",
            trigger_type="time",
            every="1d",
            tool_scope=["archve_session"],
        ),
    )

    assert result.is_error
    assert result.outcome.error.code == "invalid_arguments"
    assert "archve_session" in result.content
    assert "archive_session" in result.content  # the suggestion


@pytest.mark.asyncio
async def test_create_automation_accepts_prefix_scope_patterns(store_and_svc):
    store, svc = store_and_svc
    execution = _execution(svc, loop_task_id=None)

    result = await create_automation(
        execution,
        CreateAutomationInput(
            name="prefix scope",
            description="Posts to Slack.",
            prompt="Post to Slack.",
            trigger_type="time",
            every="1d",
            tool_scope=["slack_*"],
        ),
    )

    assert not result.is_error
    created = next(a for a in await store.list_all() if a.name == "prefix scope")
    assert created.tool_scope == ["slack_*"]


@pytest.mark.asyncio
async def test_update_automation_rejects_unknown_tool_scope_names(store_and_svc):
    from arden.tools.automation import UpdateAutomationInput, update_automation

    store, svc = store_and_svc
    execution = _execution(svc, loop_task_id=None)
    await create_automation(
        execution,
        CreateAutomationInput(
            name="scope guard",
            description="Reads email.",
            prompt="Read email.",
            trigger_type="time",
            every="1d",
        ),
    )
    created = next(a for a in await store.list_all() if a.name == "scope guard")

    result = await update_automation(
        execution,
        UpdateAutomationInput(task_id=created.task_id, tool_scope=["gmail_serch"]),
    )

    assert result.is_error
    assert "gmail_search" in result.content
    assert (await store.get(created.task_id)).tool_scope is None
