"""SlackMonitor watcher behavior (Part C of the Slack message automations spec).

Drives SlackMonitor._poll() directly (one tick) — the run loop just calls _poll()
then sleeps, so a tick-at-a-time exercise covers the watcher without the loop.

Doubles: a fake Slack client (history_since/whoami), a fake automation store
(list_watched_slack_channels), and a recording emit sink. The MonitorStateStore
is the REAL store over an in-memory aiosqlite DB so cursor and history-window
persistence are exercised end to end.
"""

from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio

import arden.database as database
from arden.events.triggers import MessageReceived
from arden.integrations.base import IntegrationOperationError
from arden.integrations.slack.models import SlackChannel, SlackHistoryMessage, SlackHistoryPage, SlackIdentity
from arden.monitor.slack import SlackMonitor
from arden.monitor.store import MonitorStateStore

CHANNEL = "C123"
NS = f"slack:{CHANNEL}"
SELF_ID = "U_SELF"


class FakeSlackClient:
    """Matches the Slack domains used by the monitor."""

    def __init__(
        self,
        self_id: str = SELF_ID,
        history: dict[str, list[SlackHistoryMessage]] | None = None,
        history_page_size: int | None = None,
    ):
        self._self_id = self_id
        self._history = history or {}
        self._history_page_size = history_page_size
        self.history_calls: list[tuple[str, str | None, str | None]] = []
        self.whoami_calls = 0
        self.transport = SimpleNamespace(whoami=self.whoami)
        self.messages = SimpleNamespace(history_since=self.history_since)
        self.directory = SimpleNamespace(
            resolve_channel=self.resolve_channel,
            resolve_user_name=self.resolve_user_name,
        )

    async def whoami(self) -> SlackIdentity:
        self.whoami_calls += 1
        return SlackIdentity(user_ref=self._self_id, user_name="selfbot")

    async def history_since(
        self,
        channel: str,
        *,
        oldest: str,
        latest: str,
        limit: int = 100,
    ) -> SlackHistoryPage:
        self.history_calls.append((channel, oldest, latest))
        messages = self._history.get(channel, [])
        eligible = [
            message
            for message in messages
            if float(message.timestamp) > float(oldest) and float(message.timestamp) < float(latest)
        ]
        eligible.sort(key=lambda message: float(message.timestamp), reverse=True)
        page_size = min(limit, self._history_page_size or limit)
        provider_page = eligible[:page_size]
        has_more = len(eligible) > len(provider_page)
        oldest_first = tuple(reversed(provider_page))
        return SlackHistoryPage(
            messages=oldest_first,
            has_more=has_more,
            next_latest=oldest_first[0].timestamp if has_more else None,
        )

    async def resolve_channel(self, name: str) -> SlackChannel:
        return SlackChannel(ref=name, name=f"#{name}")

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


def _msg(
    ts: str,
    *,
    text: str = "hi",
    user: str = "U_PERSON",
    subtype: str | None = None,
    bot_id: str | None = None,
) -> SlackHistoryMessage:
    return SlackHistoryMessage(
        timestamp=ts,
        text=text,
        user_ref=user or None,
        thread_timestamp=None,
        subtype=subtype,
        bot_ref=bot_id,
    )


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
    assert slack.history_calls[0][:2] == (CHANNEL, "100.0")
    # Cursor advanced to the newest emitted message.
    assert (await state_store.get_state(NS))["last_ts"] == "350.0"


@pytest.mark.asyncio
async def test_backlog_scans_one_durable_window_per_tick_without_skipping(
    state_store: MonitorStateStore,
    monkeypatch,
):
    monkeypatch.setattr("arden.monitor.slack._now_ts", lambda: "500.0")
    await state_store.set_state(NS, {"last_ts": "100.0"})
    slack = FakeSlackClient(
        history={
            CHANNEL: [
                _msg("150.0", text="first"),
                _msg("250.0", text="second"),
                _msg("350.0", text="third"),
                _msg("450.0", text="fourth"),
            ]
        },
        history_page_size=2,
    )
    sink = RecordingSink()

    monitor = _make_monitor(slack, state_store, channels=[CHANNEL])
    monitor._emit_event = sink
    await monitor._poll()

    assert sink.events == []
    assert await state_store.get_state(NS) == {
        "last_ts": "100.0",
        "history_latest_stack": ["500.0", "350.0"],
    }

    # A new monitor instance resumes the durable scan at its oldest pending window.
    monitor = _make_monitor(slack, state_store, channels=[CHANNEL])
    monitor._emit_event = sink
    await monitor._poll()
    assert [event.ts for event in sink.events] == ["150.0", "250.0"]
    assert await state_store.get_state(NS) == {
        "last_ts": "250.0",
        "history_latest_stack": ["500.0"],
    }

    await monitor._poll()
    assert [event.ts for event in sink.events] == ["150.0", "250.0", "350.0", "450.0"]
    assert await state_store.get_state(NS) == {"last_ts": "450.0"}
    assert slack.history_calls == [
        (CHANNEL, "100.0", "500.0"),
        (CHANNEL, "100.0", "350.0"),
        (CHANNEL, "250.0", "500.0"),
    ]


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
async def test_trailing_skipped_messages_advance_cursor(state_store: MonitorStateStore):
    await state_store.set_state(NS, {"last_ts": "100.0"})
    slack = FakeSlackClient(
        history={
            CHANNEL: [
                _msg("110.0", user="U_PERSON", text="real"),
                _msg("120.0", bot_id="B1", user="", text="from a bot"),
                _msg("130.0", subtype="channel_join", user="U_PERSON", text="joined"),
            ]
        }
    )
    sink = RecordingSink()
    monitor = _make_monitor(slack, state_store, channels=[CHANNEL])
    monitor._emit_event = sink

    await monitor._poll()

    assert [event.text for event in sink.events] == ["real"]
    assert (await state_store.get_state(NS))["last_ts"] == "130.0"


@pytest.mark.asyncio
async def test_human_thread_broadcast_is_emitted(state_store: MonitorStateStore):
    await state_store.set_state(NS, {"last_ts": "100.0"})
    message = _msg("110.0", subtype="thread_broadcast", user="U_PERSON", text="broadcast reply")
    message = SlackHistoryMessage(
        timestamp=message.timestamp,
        text=message.text,
        user_ref=message.user_ref,
        thread_timestamp="105.0",
        subtype=message.subtype,
        bot_ref=message.bot_ref,
    )
    slack = FakeSlackClient(history={CHANNEL: [message]})
    sink = RecordingSink()
    monitor = _make_monitor(slack, state_store, channels=[CHANNEL])
    monitor._emit_event = sink

    await monitor._poll()

    assert [(event.text, event.thread_ts) for event in sink.events] == [("broadcast reply", "105.0")]


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
    assert [call[:2] for call in slack.history_calls] == [(CHANNEL, "100.0"), (CHANNEL, "150.0")]


@pytest.mark.asyncio
async def test_provider_error_retains_channel_cursor_for_retry(state_store: MonitorStateStore):
    await state_store.set_state(NS, {"last_ts": "100.0"})
    slack = FakeSlackClient()

    async def fail_history(
        _channel: str,
        *,
        oldest: str,
        latest: str,
        limit: int = 100,
    ):
        raise IntegrationOperationError(code="provider_error", safe_message="Slack failed", retryable=True)

    slack.messages.history_since = fail_history
    sink = RecordingSink()
    monitor = _make_monitor(slack, state_store, channels=[CHANNEL])
    monitor._emit_event = sink

    await monitor._poll()

    assert sink.events == []
    assert (await state_store.get_state(NS))["last_ts"] == "100.0"


@pytest.mark.asyncio
async def test_unexpected_channel_bug_is_not_swallowed(state_store: MonitorStateStore):
    await state_store.set_state(NS, {"last_ts": "100.0"})
    slack = FakeSlackClient()

    async def fail_history(
        _channel: str,
        *,
        oldest: str,
        latest: str,
        limit: int = 100,
    ):
        raise RuntimeError("programming bug")

    slack.messages.history_since = fail_history
    monitor = _make_monitor(slack, state_store, channels=[CHANNEL])
    monitor._emit_event = RecordingSink()

    with pytest.raises(RuntimeError, match="programming bug"):
        await monitor._poll()
