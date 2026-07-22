"""MessageTrigger end-to-end: dataclass (de)serialization, store query gates,
and the scheduler.fire_event message branch.

The fire_event tests drive the *full* event path (match → claim → enqueue),
not the static `_message_trigger_passes` helper. To keep them synchronous and
free of a real dispatcher, the watcher automation is saved already-running
(`running_since` set): fire_event still matches and enqueues, but
`_start_next_queued_event_if_idle` early-returns when `try_mark_running` fails,
so the enqueued event is observable in the queue without any background run.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio

import arden.database as database
from arden.automation.models import Automation
from arden.automation.scheduler import Scheduler
from arden.automation.store import AutomationStore
from arden.automation.triggers import MessageTrigger, TimeTrigger, parse_triggers
from arden.events.triggers import MessageReceived


@pytest_asyncio.fixture
async def store(tmp_path: Path):
    conn = await database.connect(tmp_path / "automation.db")
    s = AutomationStore(conn)
    await s.init_schema()
    yield s
    await conn.close()


def _trigger(
    *,
    source: str = "slack",
    channel_id: str = "C1",
    channel_name: str = "bugs",
    from_user_id: str | None = None,
    from_user_name: str | None = None,
    contains: list[str] | None = None,
) -> MessageTrigger:
    return MessageTrigger(
        source=source,
        channels=[{"id": channel_id, "name": channel_name}],
        from_user_id=from_user_id,
        from_user_name=from_user_name,
        contains=contains or [],
    )


def _event(
    *,
    source: str = "slack",
    channel_id: str = "C1",
    user_id: str = "U1",
    text: str = "the deploy is broken",
) -> MessageReceived:
    return MessageReceived(
        source=source,
        channel_id=channel_id,
        channel_name="bugs",
        user_id=user_id,
        user_name="alice",
        text=text,
        ts="1700000000.000100",
        thread_ts=None,
        permalink=None,
    )


async def _save_watcher(
    store: AutomationStore,
    task_id: str,
    trigger: MessageTrigger,
    *,
    enabled: bool = True,
    running: bool = False,
) -> None:
    now = datetime.now(UTC)
    await store.save(
        Automation(
            task_id=task_id,
            name="watcher",
            description="triage",
            model=None,
            triggers=[trigger],
            enabled=enabled,
            created_at=now,
            next_run_at=None,
            last_run_at=None,
            last_result=None,
            running_since=now if running else None,
            auto_approve=True,
            kind="automation",
            thread_id=f"sess-{task_id}",
        )
    )


# --- (1) MessageTrigger params() / parse_triggers round-trip ---


def test_params_emits_all_fields():
    trigger = _trigger(
        channel_id="C42",
        channel_name="alerts",
        from_user_id="U9",
        from_user_name="bob",
        contains=["broken", "down"],
    )

    assert trigger.params() == {
        "source": "slack",
        "channels": [{"id": "C42", "name": "alerts"}],
        "from_user_id": "U9",
        "from_user_name": "bob",
        "contains": ["broken", "down"],
    }


def test_one_shot_is_false():
    assert _trigger().one_shot is False


def test_parse_triggers_round_trips_message_trigger():
    original = _trigger(
        channel_id="C7",
        channel_name="bugs",
        from_user_id="U1",
        from_user_name="alice",
        contains=["crash"],
    )
    # parse_triggers reads what the store writes: {"type": ..., **params()}.
    raw = (
        '[{"type": "message", "source": "slack", '
        '"channels": [{"id": "C7", "name": "bugs"}], '
        '"from_user_id": "U1", "from_user_name": "alice", '
        '"contains": ["crash"]}]'
    )

    parsed = parse_triggers(raw)

    assert parsed == [original]
    assert isinstance(parsed[0], MessageTrigger)


def test_parse_triggers_defaults_optional_fields():
    raw = '[{"type": "message", "source": "slack", "channels": [{"id": "C1", "name": "bugs"}]}]'

    parsed = parse_triggers(raw)

    assert len(parsed) == 1
    trigger = parsed[0]
    assert isinstance(trigger, MessageTrigger)
    assert trigger.from_user_id is None
    assert trigger.from_user_name is None
    assert trigger.contains == []


def test_parse_triggers_mixed_union_preserves_types():
    raw = (
        '[{"type": "time", "at": "09:00"}, '
        '{"type": "message", "source": "slack", "channels": [{"id": "C1", "name": "bugs"}]}]'
    )

    parsed = parse_triggers(raw)

    assert [type(t) for t in parsed] == [TimeTrigger, MessageTrigger]


# --- (2) Store query gates ---


@pytest.mark.asyncio
async def test_list_message_triggered_filters_by_source_channel_and_enabled(store: AutomationStore):
    await _save_watcher(store, "match", _trigger(source="slack", channel_id="C1"))
    await _save_watcher(store, "wrong-channel", _trigger(source="slack", channel_id="C2"))
    await _save_watcher(store, "wrong-source", _trigger(source="telegram", channel_id="C1"))
    await _save_watcher(store, "disabled", _trigger(source="slack", channel_id="C1"), enabled=False)

    matched = await store.list_message_triggered("slack", "C1")

    assert {a.task_id for a in matched} == {"match"}


@pytest.mark.asyncio
async def test_list_message_triggered_ignores_non_message_triggers(store: AutomationStore):
    now = datetime.now(UTC)
    await store.save(
        Automation(
            task_id="time-only",
            name="time",
            description="x",
            model=None,
            triggers=[TimeTrigger(at="09:00")],
            enabled=True,
            created_at=now,
            next_run_at=now,
            last_run_at=None,
            last_result=None,
            running_since=None,
            auto_approve=False,
        )
    )

    matched = await store.list_message_triggered("slack", "C1")

    assert matched == []


@pytest.mark.asyncio
async def test_list_watched_slack_channels_returns_distinct_enabled_slack_channels(store: AutomationStore):
    await _save_watcher(store, "a", _trigger(source="slack", channel_id="C1"))
    await _save_watcher(store, "b", _trigger(source="slack", channel_id="C1"))  # dup channel
    await _save_watcher(store, "c", _trigger(source="slack", channel_id="C2"))
    await _save_watcher(store, "disabled", _trigger(source="slack", channel_id="C3"), enabled=False)
    await _save_watcher(store, "telegram", _trigger(source="telegram", channel_id="C4"))

    channels = await store.list_watched_slack_channels()

    assert sorted(channels) == ["C1", "C2"]


@pytest.mark.asyncio
async def test_list_watched_slack_channels_empty_when_no_triggers(store: AutomationStore):
    assert await store.list_watched_slack_channels() == []


@pytest.mark.asyncio
async def test_list_message_triggered_matches_any_channel_in_list(store: AutomationStore):
    multi = MessageTrigger(source="slack", channels=[{"id": "C1", "name": "bugs"}, {"id": "C2", "name": "alerts"}])
    await _save_watcher(store, "multi", multi)

    assert {a.task_id for a in await store.list_message_triggered("slack", "C1")} == {"multi"}
    assert {a.task_id for a in await store.list_message_triggered("slack", "C2")} == {"multi"}
    assert await store.list_message_triggered("slack", "C9") == []


@pytest.mark.asyncio
async def test_list_watched_includes_every_channel_in_a_multi_trigger(store: AutomationStore):
    multi = MessageTrigger(source="slack", channels=[{"id": "C1", "name": "bugs"}, {"id": "C2", "name": "alerts"}])
    await _save_watcher(store, "multi", multi)

    assert sorted(await store.list_watched_slack_channels()) == ["C1", "C2"]


# --- (3) scheduler.fire_event message branch gating ---


async def _fire_and_collect_enqueued(store: AutomationStore, event: MessageReceived) -> set[str]:
    """Run fire_event, then return the set of task_ids that have a pending
    queued event. Watcher automations are saved already-running, so fire_event
    enqueues without spinning up a real dispatch."""
    sched = Scheduler(store=store, build_deps=lambda: None)
    await sched.fire_event(event)
    return set(await store.list_tasks_with_pending_events())


@pytest.mark.asyncio
async def test_fire_event_enqueues_on_from_user_match(store: AutomationStore):
    await _save_watcher(store, "watch", _trigger(channel_id="C1", from_user_id="U1"), running=True)

    enqueued = await _fire_and_collect_enqueued(store, _event(channel_id="C1", user_id="U1"))

    assert enqueued == {"watch"}


@pytest.mark.asyncio
async def test_fire_event_skips_on_from_user_mismatch(store: AutomationStore):
    await _save_watcher(store, "watch", _trigger(channel_id="C1", from_user_id="U1"), running=True)

    enqueued = await _fire_and_collect_enqueued(store, _event(channel_id="C1", user_id="U2"))

    assert enqueued == set()


@pytest.mark.asyncio
async def test_fire_event_contains_any_of_case_insensitive(store: AutomationStore):
    await _save_watcher(store, "watch", _trigger(channel_id="C1", contains=["Broken", "down"]), running=True)

    matched = await _fire_and_collect_enqueued(store, _event(channel_id="C1", text="the deploy is BROKEN"))
    assert matched == {"watch"}


@pytest.mark.asyncio
async def test_fire_event_contains_no_keyword_present_skips(store: AutomationStore):
    await _save_watcher(store, "watch", _trigger(channel_id="C1", contains=["broken", "down"]), running=True)

    matched = await _fire_and_collect_enqueued(store, _event(channel_id="C1", text="everything is fine"))
    assert matched == set()


@pytest.mark.asyncio
async def test_fire_event_empty_contains_passes_all(store: AutomationStore):
    await _save_watcher(store, "watch", _trigger(channel_id="C1", contains=[]), running=True)

    matched = await _fire_and_collect_enqueued(store, _event(channel_id="C1", text="anything at all"))
    assert matched == {"watch"}


@pytest.mark.asyncio
async def test_fire_event_matches_a_second_channel_in_the_list(store: AutomationStore):
    multi = MessageTrigger(source="slack", channels=[{"id": "C1", "name": "bugs"}, {"id": "C2", "name": "alerts"}])
    await _save_watcher(store, "watch", multi, running=True)

    enqueued = await _fire_and_collect_enqueued(store, _event(channel_id="C2"))
    assert enqueued == {"watch"}


@pytest.mark.asyncio
async def test_fire_event_enqueues_hardened_context(store: AutomationStore):
    """The enqueued context is the event's rendered, anti-injection-wrapped text."""
    await _save_watcher(store, "watch", _trigger(channel_id="C1"), running=True)

    sched = Scheduler(store=store, build_deps=lambda: None)
    await sched.fire_event(_event(channel_id="C1", text="prod is on fire"))

    claimed = await store.claim_next_event("watch", datetime.now(UTC))
    assert claimed is not None
    _, context, _ = claimed
    assert "prod is on fire" in context
    assert "UNTRUSTED" in context


@pytest.mark.asyncio
async def test_fire_event_dedupes_same_message(store: AutomationStore):
    """Re-firing the same message (same event_key) does not enqueue twice."""
    await _save_watcher(store, "watch", _trigger(channel_id="C1"), running=True)
    sched = Scheduler(store=store, build_deps=lambda: None)
    event = _event(channel_id="C1")

    await sched.fire_event(event)
    await sched.fire_event(event)

    now = datetime.now(UTC)
    first = await store.claim_next_event("watch", now)
    second = await store.claim_next_event("watch", now + timedelta(seconds=1))
    assert first is not None
    assert second is None
