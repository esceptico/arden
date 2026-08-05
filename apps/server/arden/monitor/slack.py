import asyncio
from datetime import UTC, datetime
from typing import cast

from arden.automation.store import AutomationStore
from arden.constants import SLACK_MONITOR_POLL_INTERVAL
from arden.events.triggers import MessageReceived
from arden.integrations.base import IntegrationOperationError
from arden.integrations.slack.client import SlackClient
from arden.logging import get_logger
from arden.monitor.service import MonitorEventSink
from arden.monitor.store import MonitorStateStore

_logger = get_logger(__name__)


def _now_ts() -> str:
    return f"{datetime.now(UTC).timestamp():.6f}"


class SlackMonitor:
    """Polls watched Slack channels and publishes MessageReceived trigger events."""

    def __init__(self, slack: SlackClient, state_store: MonitorStateStore, automation_store: AutomationStore):
        self._slack = slack
        self._state_store = state_store
        self._automation_store = automation_store
        self._emit_event: MonitorEventSink | None = None
        self._task: asyncio.Task | None = None

    def start(self, emit_event: MonitorEventSink) -> None:
        if self._task is not None and not self._task.done():
            return
        self._emit_event = emit_event
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    async def _run(self) -> None:
        await self._loop()

    async def _loop(self) -> None:
        while True:
            await self._poll()
            await asyncio.sleep(SLACK_MONITOR_POLL_INTERVAL)

    async def _poll(self) -> None:
        if not self._emit_event:
            return
        try:
            channels = await self._automation_store.list_watched_slack_channels()
        except Exception:
            _logger.exception("Failed to list watched Slack channels")
            return
        if not channels:
            return

        try:
            self_id = (await self._slack.whoami()).user_ref
        except Exception:
            _logger.exception("Failed to resolve Slack identity")
            return

        for channel_id in channels:
            try:
                await self._poll_channel(channel_id, self_id)
            except IntegrationOperationError:
                _logger.exception("Failed to poll Slack channel %s; cursor retained", channel_id)

    async def _poll_channel(self, channel_id: str, self_id: str) -> None:
        ns = f"slack:{channel_id}"
        state = await self._state_store.get_state(ns)
        last_ts = state.get("last_ts")

        if not last_ts:
            await self._state_store.set_state(ns, {"last_ts": _now_ts()})
            return

        messages = await self._slack.history_since(channel_id, oldest=last_ts)

        channel = await self._slack.resolve_channel(channel_id)

        for message in messages:
            if message.subtype or message.bot_ref:
                continue
            user_id = message.user_ref
            if user_id == self_id:
                continue

            user_id = cast("str", user_id)
            event = MessageReceived(
                source="slack",
                channel_id=channel_id,
                channel_name=channel.name,
                user_id=user_id,
                user_name=await self._slack.resolve_user_name(user_id),
                text=message.text,
                ts=message.timestamp,
                thread_ts=message.thread_timestamp,
                permalink=None,
            )
            try:
                await self._emit_event(event)
            except Exception:
                _logger.exception("Failed to emit Slack message event for %s", channel_id)
                return
            await self._state_store.set_state(ns, {"last_ts": message.timestamp})
