"""End-to-end integration test for the channel-aware automation fan-out.

The canonical user story: an hourly watcher discovers items, spawns a
per-item monitor (each with its own channel session + idempotency key),
and the monitors post status updates into their channel sessions.

This test exercises the real building blocks together (store + service +
scheduler + session service), stubbing only the LLM agent in the post
dispatcher.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import pytest_asyncio

import arden.database as database
from arden.automation.models import Automation
from arden.automation.scheduler import Scheduler
from arden.automation.service import AutomationService
from arden.automation.store import AutomationStore
from arden.context.store import SessionStore
from arden.services.session import SessionService


@pytest_asyncio.fixture
async def store(tmp_path: Path):
    conn = await database.connect(tmp_path / "automation.db")
    s = AutomationStore(conn)
    await s.init_schema()
    yield s
    await conn.close()


@pytest_asyncio.fixture
async def session_service(tmp_path: Path):
    conn = await database.connect(tmp_path / "sessions.db")
    store = SessionStore(conn)
    await store.init_schema()
    yield SessionService(store)
    await conn.close()


@pytest.mark.asyncio
async def test_watcher_fanout_dedup_cascade(store: AutomationStore, session_service: SessionService):
    # Real scheduler with a stub post dispatcher that mimics what
    # app.py wires: run "agent", append assistant message into the
    # target session.
    posted: list[tuple[str, str]] = []

    async def stub_post(automation: Automation, context: str | dict | None = None) -> str | None:
        target_id = automation.thread_id
        assert target_id is not None
        text = f"status for {automation.task_id}"
        data = await session_service.load(target_id)
        assert data is not None
        data.messages.append({"role": "assistant", "content": text})
        await session_service.save_progress(data.state, data.messages)
        posted.append((target_id, text))
        return text

    scheduler = Scheduler(store=store, build_deps=lambda: None)
    scheduler.set_post_dispatcher(stub_post)
    svc = AutomationService(store=store, scheduler=scheduler, session_service=session_service)

    # ---- Step 1: create the watcher (parent) ----------------------------
    watcher = await svc.create(
        name="watcher",
        description="Scans incoming offers.",
        prompt="Scan incoming offers.",
        trigger_type="time",
        every="1h",
    )
    assert watcher is not None

    # ---- Step 2: watcher "fires" → spawn 3 channel sessions + children --
    # The watcher's first fire timestamp scopes the idempotency claims:
    # any re-fire under the same parent_fire_at is deduped; a future fire
    # of the same watcher could re-spawn children for the same items.
    # Children are created via `svc.create(thread_id=..., read_history=False)`
    # — this is the agent-tool path. The scheduler routes session-bound
    # automations (any row with thread_id set) through the post dispatcher
    # regardless of `kind`, so kind="automation" works just like a loop.
    fire_at = datetime(2026, 5, 13, 10, 0, tzinfo=UTC).isoformat()
    items = ["offer-A", "offer-B", "offer-C"]
    children_by_item: dict[str, Automation] = {}
    for item in items:
        channel_state = session_service.create(
            name=f"Watching {item}",
            session_type="channel",
            origin_automation_id=watcher.task_id,
        )
        await session_service.save(channel_state, [])

        child = await svc.create(
            name=f"check {item}",
            description=f"Checks {item} every four hours.",
            prompt=f"Check {item} every four hours.",
            trigger_type="time",
            every="4h",
            thread_id=channel_state.session_id,
            read_history=False,
            parent_automation_id=watcher.task_id,
            idempotency_key=item,
            idempotency_scope="run",
            parent_fire_at=fire_at,
        )
        assert child is not None, f"first create for {item} should succeed"
        assert child.thread_id == channel_state.session_id
        assert child.kind == "automation"  # NOT "loop" — agent-tool path
        assert child.parent_automation_id == watcher.task_id
        assert child.read_history is False  # post mode
        assert child.prompt == f"Check {item} every four hours."
        children_by_item[item] = child

    # ---- Step 3: verify 3 children + 3 channel sessions exist -----------
    children = await svc.list_children(watcher.task_id)
    assert len(children) == 3
    assert {c.idempotency_key for c in children} == set(items)

    # Each child's target session should be a real channel owned by this
    # watcher.
    for child in children:
        target_id = child.thread_id
        data = await session_service.load(target_id)
        assert data is not None
        assert data.state.session_type == "channel"
        assert data.state.origin_automation_id == watcher.task_id

    # ---- Step 4: re-fire watcher with same items → idempotency blocks --
    # Same parent_fire_at → same claim namespace → all 3 items deduped.
    for item in items:
        dup = await svc.create(
            name=f"check {item} (re-fire)",
            description=f"Rechecks {item} after the watcher fires.",
            prompt=f"Check {item} again after the watcher fires.",
            trigger_type="time",
            every="4h",
            thread_id="ignored",
            read_history=False,
            parent_automation_id=watcher.task_id,
            idempotency_key=item,
            idempotency_scope="run",
            parent_fire_at=fire_at,
        )
        assert dup is None, f"re-fire for {item} must be deduped"

    children_after_refire = await svc.list_children(watcher.task_id)
    assert len(children_after_refire) == 3

    # ---- Step 5: fire one channel automation → post lands in session --
    # All 3 children have next_run_at on a 4h schedule. Force the chosen
    # one into the past so the scheduler picks only it up; leave the
    # others on their future next_run_at.
    chosen = children_by_item["offer-A"]
    await store.set_next_run(chosen.task_id, datetime.now(UTC) - timedelta(seconds=1))

    await scheduler._tick()
    for t in list(scheduler._running):
        await t

    # The post should have landed as an assistant message in the channel.
    target_id = chosen.thread_id
    channel_data = await session_service.load(target_id)
    assert channel_data is not None
    assistant_msgs = [m for m in channel_data.messages if m.get("role") == "assistant"]
    assert len(assistant_msgs) == 1
    assert assistant_msgs[0]["content"] == f"status for {chosen.task_id}"
    assert posted == [(target_id, f"status for {chosen.task_id}")]

    # Iteration count was bumped on the fired child only.
    reloaded_chosen = await store.get(chosen.task_id)
    assert reloaded_chosen.iteration_count == 1
    for item, child in children_by_item.items():
        if item == "offer-A":
            continue
        other = await store.get(child.task_id)
        assert other.iteration_count == 0

    # ---- Step 6: delete watcher → all 3 children disabled --------------
    disabled = await svc.delete(watcher.task_id)
    assert disabled == 3

    final_children = await svc.list_children(watcher.task_id)
    assert len(final_children) == 3
    assert all(not c.enabled for c in final_children)

    with pytest.raises(KeyError):
        await svc.get(watcher.task_id)
