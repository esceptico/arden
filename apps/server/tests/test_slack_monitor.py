"""SlackMonitor watcher behavior (Part C of the Slack message automations spec).

Drives SlackMonitor._poll() directly (one tick) — the run loop just calls _poll()
then sleeps, so a tick-at-a-time exercise covers the watcher without the loop.

Doubles: a fake Slack client (history_since/whoami), a fake automation store
(list_watched_slack_channels), and a recording emit sink. The MonitorStateStore
is the REAL store over an in-memory aiosqlite DB so cursor persistence is exercised
end to end (namespace "slack:{channel_id}", key "last_ts").
"""

from pathlib import Path

import pytest
import pytest_asyncio

import arden.database as database
from arden.events.triggers import MessageReceived
from arden.monitor.slack import SlackMonitor
from arden.monitor.store import MonitorStateStore

CHANNEL = "C123"
NS = f"slack:{CHANNEL}"
SELF_ID = "U_SELF"


class FakeSlackClient:
    """Matches the bits of SlackClient that SlackMonitor calls: whoami(),
    history_since(channel, oldest=..., limit=...) returning oldest -> newest,
    and the resolve_channel/resolve_user_name display lookups."""

    def __init__(self, self_id: str = SELF_ID, history: dict[str, list[dict]] | None = None):
        self._self_id = self_id
        self._history = history or {}
        self.history_calls: list[tuple[str, str | None]] = []
        self.whoami_calls = 0

    async def whoami(self) -> dict[str, str]:
        self.whoami_calls += 1
        return {"user_id": self._self_id, "user": "selfbot"}

    async def history_since(self, channel: str, oldest: str | None = None, limit: int = 200) -> list[dict]:
        self.history_calls.append((channel, oldest))
        msgs = self._history.get(channel, [])
        if oldest is None:
            return list(msgs)
        return [m for m in msgs if m["ts"] > oldest]

    async def resolve_channel(self, name: str) -> tuple[str, str]:
        return name, f"#{name}"

    async def resolve_user_name(self, user_id: str) -> str:
        return f"name-{user_id}"


class FakeAutomationStore:
    def __init__(self, channels: list[str]):
        self._channels = channels

    async def list_watched_slack_channels(self) -> list[str]:
        return list(self._channels)


class RecordingSink:
    """Records emitted events. Optionally raises on a given (zero-based) call index
    to model an emit failure mid-batch."""

    def __init__(self, fail_on_index: int | None = None):
        self.events: list[MessageReceived] = []
        self._fail_on_index = fail_on_index
        self._calls = 0

    async def __call__(self, event: MessageReceived) -> None:
        idx = self._calls
        self._calls += 1
        if self._fail_on_index is not None and idx == self._fail_on_index:
            raise RuntimeError("emit boom")
        self.events.append(event)


def _msg(ts: str, *, text: str = "hi", user: str = "U_PERSON", **extra) -> dict:
    return {"ts": ts, "text": text, "user": user, **extra}


@pytest_asyncio.fixture
async def state_store(tmp_path: Path):
    conn = await database.connect(tmp_path / "monitor.db")
    store = MonitorStateStore(conn)
    await store.init_schema()
    yield store
    await conn.close()


def _make_monitor(slack: FakeSlackClient, state_store: MonitorStateStore, channels: list[str]) -> SlackMonitor:
    return SlackMonitor(slack, state_store, FakeAutomationStore(channels))


@pytest.mark.asyncio
async def test_no_watched_channels_is_idle(state_store: MonitorStateStore):
    slack = FakeSlackClient(history={CHANNEL: [_msg("100.0")]})
    sink = RecordingSink()
    monitor = _make_monitor(slack, state_store, channels=[])
    monitor._emit_event = sink

    await monitor._poll()

    assert sink.events == []
    assert slack.whoami_calls == 0
    assert slack.history_calls == []


@pytest.mark.asyncio
async def test_cold_start_emits_nothing_and_records_cursor(state_store: MonitorStateStore):
    # History exists, but with no prior state the watcher must NOT backfill.
    slack = FakeSlackClient(history={CHANNEL: [_msg("100.0"), _msg("200.0")]})
    sink = RecordingSink()
    monitor = _make_monitor(slack, state_store, channels=[CHANNEL])
    monitor._emit_event = sink

    await monitor._poll()

    assert sink.events == []
    # No history fetch on cold start; only a cursor gets persisted.
    assert slack.history_calls == []
    state = await state_store.get_state(NS)
    assert state.get("last_ts")


@pytest.mark.asyncio
async def test_new_messages_emit_oldest_to_newest(state_store: MonitorStateStore):
    await state_store.set_state(NS, {"last_ts": "100.0"})
    slack = FakeSlackClient(
        history={
            CHANNEL: [
                _msg("150.0", text="first", user="U_A"),
                _msg("250.0", text="second", user="U_B"),
                _msg("350.0", text="third", user="U_C"),
            ]
        }
    )
    sink = RecordingSink()
    monitor = _make_monitor(slack, state_store, channels=[CHANNEL])
    monitor._emit_event = sink

    await monitor._poll()

    assert [e.ts for e in sink.events] == ["150.0", "250.0", "350.0"]
    assert [e.text for e in sink.events] == ["first", "second", "third"]
    e = sink.events[0]
    assert isinstance(e, MessageReceived)
    assert e.source == "slack"
    assert e.channel_id == CHANNEL
    assert e.user_id == "U_A"
    # History fetched starting at the stored cursor.
    assert slack.history_calls == [(CHANNEL, "100.0")]
    # Cursor advanced to the newest emitted message.
    assert (await state_store.get_state(NS))["last_ts"] == "350.0"


@pytest.mark.asyncio
async def test_bot_own_and_subtype_messages_are_skipped(state_store: MonitorStateStore):
    await state_store.set_state(NS, {"last_ts": "100.0"})
    slack = FakeSlackClient(
        history={
            CHANNEL: [
                _msg("110.0", user="U_PERSON", text="real"),
                _msg("120.0", bot_id="B1", user="", text="from a bot"),
                _msg("130.0", user=SELF_ID, text="my own post"),
                _msg("140.0", subtype="channel_join", user="U_PERSON", text="joined"),
                _msg("150.0", user="U_PERSON", text="another real"),
            ]
        }
    )
    sink = RecordingSink()
    monitor = _make_monitor(slack, state_store, channels=[CHANNEL])
    monitor._emit_event = sink

    await monitor._poll()

    assert [e.text for e in sink.events] == ["real", "another real"]
    # Cursor still advances across skipped messages (last seen ts).
    assert (await state_store.get_state(NS))["last_ts"] == "150.0"


@pytest.mark.asyncio
async def test_cursor_does_not_advance_past_failed_emit_and_re_emits_next_tick(state_store: MonitorStateStore):
    await state_store.set_state(NS, {"last_ts": "100.0"})
    history = {
        CHANNEL: [
            _msg("150.0", text="m1"),
            _msg("250.0", text="m2"),
            _msg("350.0", text="m3"),
        ]
    }
    slack = FakeSlackClient(history=history)
    # Second emit (index 1, the "m2" message) raises.
    sink = RecordingSink(fail_on_index=1)
    monitor = _make_monitor(slack, state_store, channels=[CHANNEL])
    monitor._emit_event = sink

    # Tick 1: m1 emits and advances cursor; m2 raises -> channel stops for the tick.
    await monitor._poll()

    assert [e.ts for e in sink.events] == ["150.0"]
    assert (await state_store.get_state(NS))["last_ts"] == "150.0"

    # Tick 2: no more failures. history_since(oldest="150.0") yields m2, m3.
    # Re-emit of m2 is safe (dedup is the scheduler's job); cursor reaches m3.
    await monitor._poll()

    assert [e.ts for e in sink.events] == ["150.0", "250.0", "350.0"]
    assert (await state_store.get_state(NS))["last_ts"] == "350.0"
    # Verify the second fetch used the cursor left after the failed batch.
    assert slack.history_calls == [(CHANNEL, "100.0"), (CHANNEL, "150.0")]
