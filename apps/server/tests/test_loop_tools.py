from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio
from pydantic import ValidationError

import arden.database as database
from arden.automation.models import Automation, create_automation_ref
from arden.automation.scheduler import Scheduler
from arden.automation.service import AutomationService
from arden.automation.store import AutomationStore
from arden.automation.triggers import TimeTrigger
from arden.context.models import SessionState
from arden.integrations.slack.models import SlackChannel, SlackUser
from arden.tools.automation import (
    AutomationCreateInput,
    AutomationDeleteInput,
    AutomationListRunsInput,
    AutomationResultInput,
    AutomationRunInput,
    AutomationUpdateInput,
    LoopCreateInput,
    LoopDoneInput,
    LoopScheduleWakeupInput,
    approve_automation_create,
    approve_loop_create,
    automation_create,
    automation_list_runs_tool,
    automation_result,
    loop_create,
    loop_done,
    loop_schedule_wakeup,
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
        automation_ref=create_automation_ref("x", "loop-1"),
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
    for name in ("slack_search", "slack_post_message", "gmail_search", "email_read", "session_archive"):
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
    return ToolExecution(tool_id="t1", tool_name="loop_schedule_wakeup", ctx=ctx)


@pytest.mark.asyncio
async def test_schedule_wakeup_updates_next_run(store_and_svc):
    store, svc = store_and_svc
    execution = _execution(svc, loop_task_id="loop-1")

    before = datetime.now(UTC)
    result = await loop_schedule_wakeup(execution, LoopScheduleWakeupInput(delay_seconds=300))
    assert not result.is_error
    loop = await store.get("loop-1")
    assert loop.next_run_at is not None
    # next_run_at should be ~now + 300s
    delta = (loop.next_run_at - before).total_seconds()
    assert 295 <= delta <= 305


@pytest.mark.asyncio
async def test_list_automation_runs_exposes_trigger_result_and_channel(store_and_svc):
    store, svc = store_and_svc
    started = datetime.now(UTC)
    run_id = await store.record_run_start("loop-1", started)
    await store.settle_run(
        task_id="loop-1",
        run_id=run_id,
        status="completed",
        result="checked CI",
        error=None,
        ended_at=started + timedelta(seconds=1),
        next_run=None,
        expected_next_run=None,
        preserve_external_reschedule=False,
        preserve_result=False,
        disable_one_shot=False,
    )

    result = await automation_list_runs_tool.execute(
        _execution(svc, loop_task_id=None),
        since=started - timedelta(minutes=1),
    )

    assert not result.is_error
    assert result.data["entries"] == [
        {
            "automation_ref": create_automation_ref("x", "loop-1"),
            "name": "x",
            "started_at": started.isoformat(),
            "ended_at": (started + timedelta(seconds=1)).isoformat(),
            "status": "completed",
            "configured_trigger": "every 5m",
            "result": "checked CI",
            "error": None,
            "channel": "sess-1",
        }
    ]


@pytest.mark.asyncio
async def test_schedule_wakeup_refuses_outside_loop(store_and_svc):
    _, svc = store_and_svc
    execution = _execution(svc, loop_task_id=None)

    result = await loop_schedule_wakeup(execution, LoopScheduleWakeupInput(delay_seconds=60))
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
        LoopScheduleWakeupInput(delay_seconds=59)


def test_automation_display_description_is_bounded():
    import pydantic

    from arden.tools.automation import AutomationUpdateInput

    with pytest.raises(pydantic.ValidationError):
        AutomationCreateInput(
            name="bounded",
            prompt="Run the bounded automation.",
            description="x" * 221,
            trigger_type="time",
            every="1h",
            idempotency_key="bounded",
        )
    with pytest.raises(pydantic.ValidationError):
        AutomationUpdateInput(automation_ref="bounded~000000", description="x" * 221)


def test_create_automation_schema_does_not_expose_session_binding():
    properties = AutomationCreateInput.model_json_schema()["properties"]

    assert "thread_id" not in properties
    assert "read_history" not in properties


def test_automation_tools_expose_refs_not_internal_ids():
    for model in (
        AutomationUpdateInput,
        AutomationDeleteInput,
        AutomationResultInput,
        AutomationRunInput,
        AutomationListRunsInput,
    ):
        properties = model.model_json_schema()["properties"]
        assert "automation_ref" in properties
        assert "task_id" not in properties

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AutomationResultInput.model_validate({"task_id": "internal-task-id"})

    loop_properties = LoopCreateInput.model_json_schema()["properties"]
    assert "parent_automation_ref" in loop_properties
    assert "parent_automation_id" not in loop_properties
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        LoopCreateInput.model_validate(
            {"prompt": "watch CI", "every": "5m", "parent_automation_id": "internal-task-id"}
        )


@pytest.mark.asyncio
async def test_internal_automation_id_is_not_a_public_ref_alias(store_and_svc):
    _, svc = store_and_svc

    result = await automation_result(
        _execution(svc, loop_task_id=None),
        AutomationResultInput(automation_ref="loop-1"),
    )

    assert result.is_error
    assert result.outcome.error.code == "not_found"


def test_create_automation_requires_retry_safe_idempotency():
    with pytest.raises(ValidationError, match="idempotency_key"):
        AutomationCreateInput(
            name="unsafe",
            prompt="Run once.",
            trigger_type="time",
            every="1h",
        )

    with pytest.raises(ValidationError, match="attempt_n is required"):
        AutomationCreateInput(
            name="attempt",
            prompt="Run once per attempt.",
            trigger_type="time",
            every="1h",
            idempotency_key="attempt",
            idempotency_scope="attempt",
        )


# --- create_automation / create_loop tool wiring for channel-aware fields ---


@pytest.mark.asyncio
async def test_create_automation_idempotency_claim_dedupes(store_and_svc):
    store, svc = store_and_svc
    execution = _execution(svc, loop_task_id=None)

    args = AutomationCreateInput(
        name="daily brief",
        description="Posts the morning brief.",
        prompt="Post the morning brief.",
        trigger_type="time",
        at="09:00",
        idempotency_key="daily-brief-1",
    )

    first = await automation_create(execution, args)
    assert not first.is_error
    assert "Created" in first.content or "created" in first.content.lower()

    second = await automation_create(execution, args)
    assert not second.is_error
    assert "already exists" in second.content.lower()

    created = next(a for a in await store.list_all() if a.name == "daily brief")
    assert created.automation_ref in second.content
    assert created.task_id not in second.content
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
        AutomationCreateInput.model_validate(
            {
                "name": "channel automation",
                "description": "Posts into its own channel.",
                "prompt": "Post into its own channel.",
                "trigger_type": "time",
                "every": "1h",
                "thread_id": "sess-target",
                "read_history": True,
                "all_tools": True,
                "idempotency_key": "channel-automation",
            }
        )
    args = AutomationCreateInput(
        name="channel automation",
        description="Posts into its own channel.",
        prompt="Post into its own channel.",
        trigger_type="time",
        every="1h",
        all_tools=True,
        idempotency_key="channel-automation",
    )
    result = await automation_create(execution, args)
    assert not result.is_error

    rows = await store.list_all()
    created = next(a for a in rows if a.name == "channel automation")
    assert created.thread_id != "sess-target"
    assert created.read_history is True
    assert created.tool_scope == "all"
    channel = await svc.session_service.load(created.thread_id)
    assert channel is not None
    assert channel.state.session_type == "channel"
    assert channel.state.origin_automation_id == created.task_id


@pytest.mark.asyncio
async def test_approve_create_automation_shows_tool_scope(store_and_svc):
    _, svc = store_and_svc
    execution = _execution(svc, loop_task_id=None)
    args = AutomationCreateInput(
        name="scoped sender",
        description="Posts a reviewed update.",
        prompt="Post a reviewed update.",
        trigger_type="time",
        every="1h",
        auto_approve=True,
        all_tools=True,
        idempotency_key="scoped-sender",
    )

    info = await approve_automation_create(execution, args)

    assert info is not None
    assert "Auto-approve: yes (skips approval for granted tools; does not grant access)" in (info.preview or "")
    assert "Tools: every tool" in (info.preview or "")


@pytest.mark.asyncio
async def test_update_automation_changes_tool_scope(store_and_svc):
    from arden.tools.automation import AutomationUpdateInput, automation_update

    store, svc = store_and_svc
    execution = _execution(svc, loop_task_id=None)
    await automation_create(
        execution,
        AutomationCreateInput(
            name="scope update",
            description="Reads the source first.",
            prompt="Read the source first.",
            trigger_type="time",
            every="1h",
            idempotency_key="scope-update",
        ),
    )
    created = next(a for a in await store.list_all() if a.name == "scope update")

    result = await automation_update(
        execution,
        AutomationUpdateInput(automation_ref=created.automation_ref, all_tools=True),
    )

    assert not result.is_error
    updated = await store.get(created.task_id)
    assert updated.tool_scope == "all"


class _FakeSlackDirectory:
    async def resolve_channel(self, name: str) -> SlackChannel:
        return SlackChannel(ref=f"C-{name}", name=name)

    async def resolve_user(self, name: str) -> SlackUser:
        return SlackUser(ref=f"U-{name}", name=name, username=name, email=None, title=None)


class _FakeSlack:
    def __init__(self):
        self.directory = _FakeSlackDirectory()


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
    args = AutomationCreateInput(
        name="bug watch",
        description="Triages bug reports.",
        prompt="Triage bugs.",
        trigger_type="message",
        channels=["feel-good-inc", "eng-bugs"],
        from_user="sam",
        contains=["bug", "error"],
        idempotency_key="bug-watch",
    )

    result = await automation_create(execution, args)
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
    from arden.tools.automation import AutomationUpdateInput, automation_update

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
    await automation_create(
        execution,
        AutomationCreateInput(
            name="watch",
            description="Triages new items.",
            prompt="Triage new items.",
            trigger_type="time",
            every="1h",
            idempotency_key="watch",
        ),
    )
    created = next(a for a in await store.list_all())

    result = await automation_update(
        execution,
        AutomationUpdateInput(
            automation_ref=created.automation_ref,
            trigger_type="message",
            channels=["eng-bugs"],
            contains=["bug"],
        ),
    )
    assert not result.is_error, result.content

    updated = await store.get(created.task_id)
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

    args = AutomationCreateInput(
        name="child auto",
        description="Runs follow-up work from the loop.",
        prompt="Run follow-up work from the loop.",
        trigger_type="time",
        every="2h",
        idempotency_key="child-auto",
    )
    result = await automation_create(execution, args)
    assert not result.is_error

    rows = await store.list_all()
    child = next(a for a in rows if a.name == "child auto")
    assert child.parent_automation_id == "loop-1"


@pytest.mark.asyncio
async def test_create_automation_attempt_scope_uses_attempt_number(store_and_svc):
    store, svc = store_and_svc
    fired_at = datetime.now(UTC)
    await store.update_last_run("loop-1", fired_at, fired_at + timedelta(minutes=5))
    execution = _execution(svc, loop_task_id="loop-1")

    result = await automation_create(
        execution,
        AutomationCreateInput(
            name="attempt child",
            description="Runs once for this retry attempt.",
            prompt="Run the attempt-scoped child.",
            trigger_type="time",
            every="2h",
            idempotency_key="attempt-child",
            idempotency_scope="attempt",
            attempt_n=2,
        ),
    )

    assert not result.is_error
    child = next(a for a in await store.list_all() if a.name == "attempt child")
    assert child.parent_automation_id == "loop-1"


@pytest.mark.asyncio
async def test_create_loop_infers_parent_from_loop_ctx(store_and_svc):
    store, svc = store_and_svc
    execution = _execution(svc, loop_task_id="loop-1")

    result = await loop_create(
        execution,
        LoopCreateInput(prompt="watch CI again", every="5m"),
    )
    assert not result.is_error

    rows = await store.list_all()
    child = next(a for a in rows if a.prompt == "watch CI again")
    assert child.parent_automation_id == "loop-1"


@pytest.mark.asyncio
async def test_create_loop_explicit_parent_overrides_ctx(store_and_svc):
    store, svc = store_and_svc
    execution = _execution(svc, loop_task_id="loop-1")
    explicit_parent = await svc.create(
        name="explicit parent",
        prompt="Own child automations.",
        description="Owns child automations.",
        trigger_type="time",
        every="1h",
        thread_id="sess-1",
    )

    result = await loop_create(
        execution,
        LoopCreateInput(
            prompt="watch CI yet again",
            every="5m",
            parent_automation_ref=explicit_parent.automation_ref,
        ),
    )
    assert not result.is_error

    rows = await store.list_all()
    child = next(a for a in rows if a.prompt == "watch CI yet again")
    assert child.parent_automation_id == explicit_parent.task_id


@pytest.mark.asyncio
async def test_create_loop_attaches_the_exact_current_chat(store_and_svc):
    store, svc = store_and_svc
    await svc.session_service.provision(name="other chat", session_id="sess-other")
    execution = _execution(svc, loop_task_id=None, session_id="sess-1")

    result = await loop_create(execution, LoopCreateInput(prompt="watch CI", every="5m"))

    assert not result.is_error
    loop = next(a for a in await store.list_all() if a.kind == "loop" and a.prompt == "watch CI")
    assert loop.thread_id == "sess-1"


@pytest.mark.asyncio
async def test_create_loop_rejects_missing_current_chat(store_and_svc):
    _, svc = store_and_svc
    execution = _execution(svc, loop_task_id=None, session_id="missing-chat")

    result = await loop_create(execution, LoopCreateInput(prompt="watch CI", every="5m"))

    assert result.is_error
    assert result.outcome.error.code == "invalid_arguments"


@pytest.mark.asyncio
async def test_create_loop_rejects_archived_current_chat(store_and_svc):
    _, svc = store_and_svc
    assert await svc.session_service.archive("sess-1")
    execution = _execution(svc, loop_task_id=None)

    result = await loop_create(execution, LoopCreateInput(prompt="watch CI", every="5m"))

    assert result.is_error
    assert result.outcome.error.code == "invalid_arguments"


@pytest.mark.asyncio
async def test_create_automation_run_scope_missing_parent_errors(store_and_svc):
    """idempotency_scope='run' with a non-existent parent must fail loudly,
    not silently collapse to global scope."""
    _, svc = store_and_svc
    execution = _execution(svc, loop_task_id=None)

    args = AutomationCreateInput(
        name="orphan",
        description="Tests a missing parent.",
        prompt="This should fail because its parent is missing.",
        trigger_type="time",
        at="09:00",
        parent_automation_ref="ghost~000000",
        idempotency_key="k1",
        idempotency_scope="run",
    )
    result = await automation_create(execution, args)
    assert result.is_error
    assert "ghost~000000" in result.content


@pytest.mark.asyncio
async def test_create_loop_attempt_scope_missing_parent_errors(store_and_svc):
    """Same protection on loop_create."""
    _, svc = store_and_svc
    execution = _execution(svc, loop_task_id=None)

    result = await loop_create(
        execution,
        LoopCreateInput(
            prompt="x",
            every="5m",
            parent_automation_ref="ghost~000000",
            idempotency_key="k1",
            idempotency_scope="attempt",
            attempt_n=0,
        ),
    )
    assert result.is_error
    assert "ghost~000000" in result.content


@pytest.mark.asyncio
async def test_approve_create_automation_flags_missing_parent(store_and_svc):
    """Approval preview should surface the same missing-parent conflict
    that execute will hit."""
    _, svc = store_and_svc
    execution = _execution(svc, loop_task_id=None)

    args = AutomationCreateInput(
        name="orphan",
        description="Tests a missing parent warning.",
        prompt="This should warn because its parent is missing.",
        trigger_type="time",
        at="09:00",
        parent_automation_ref="ghost~000000",
        idempotency_key="k1",
        idempotency_scope="run",
    )
    info = await approve_automation_create(execution, args)
    assert info is not None
    assert "ghost~000000" in info.preview
    assert "invalid" in info.preview.lower()


@pytest.mark.asyncio
async def test_approve_create_loop_flags_missing_parent(store_and_svc):
    """Same preview-vs-execute alignment for loop_create."""
    _, svc = store_and_svc
    execution = _execution(svc, loop_task_id=None)

    args = LoopCreateInput(
        prompt="watch",
        every="5m",
        parent_automation_ref="ghost~000000",
        idempotency_key="k1",
        idempotency_scope="run",
    )
    info = await approve_loop_create(execution, args)
    assert info is not None
    assert "ghost~000000" in info.preview
    assert "invalid" in info.preview.lower()


@pytest.mark.asyncio
async def test_the_agent_can_only_choose_read_only_or_everything(store_and_svc):
    """The model no longer authors a tool list, so a typo'd or over-broad name
    cannot reach the store at all — the field is a bool and the stored value is
    a key the code owns."""
    store, svc = store_and_svc
    execution = _execution(svc, loop_task_id=None)

    assert "tool_scope" not in AutomationCreateInput.model_fields
    with pytest.raises(ValidationError):
        AutomationCreateInput.model_validate(
            {
                "name": "x",
                "prompt": "x",
                "trigger_type": "time",
                "every": "1d",
                "idempotency_key": "x",
                "tool_scope": ["session_archive"],
            }
        )

    for all_tools, expected in ((False, "read_only"), (True, "all")):
        result = await automation_create(
            execution,
            AutomationCreateInput(
                name=f"lever-{expected}",
                description="Reads things.",
                prompt="Read things.",
                trigger_type="time",
                every="1d",
                all_tools=all_tools,
                idempotency_key=f"lever-{expected}",
            ),
        )
        assert not result.is_error
        created = next(a for a in await store.list_all() if a.name == f"lever-{expected}")
        assert created.tool_scope == expected
