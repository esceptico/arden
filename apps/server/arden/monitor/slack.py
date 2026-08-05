import asyncio
from datetime import UTC, datetime

from arden.automation.store import AutomationStore
from arden.constants import SLACK_MONITOR_POLL_INTERVAL
from arden.events.triggers import MessageReceived
from arden.integrations.base import IntegrationConnectionError, IntegrationOperationError
from arden.integrations.slack.client import SlackClient
from arden.logging import get_logger
from arden.monitor.service import MonitorEventSink
from arden.monitor.store import MonitorStateStore

_logger = get_logger(__name__)

_HISTORY_LATEST_STACK = "history_latest_stack"


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
        channels = await self._automation_store.list_watched_slack_channels()
        if not channels:
            return

        try:
            self_ref = (await self._slack.transport.whoami()).user_ref
        except (IntegrationConnectionError, IntegrationOperationError):
            _logger.exception("Failed to resolve Slack identity")
            return

        for channel_ref in channels:
            try:
                await self._poll_channel(channel_ref, self_ref)
            except (IntegrationConnectionError, IntegrationOperationError):
                _logger.exception("Failed to poll Slack channel %s; cursor retained", channel_ref)

    async def _poll_channel(self, channel_ref: str, self_ref: str) -> None:
        ns = f"slack:{channel_ref}"
        state = await self._state_store.get_state(ns)
        last_ts = state.get("last_ts")

        if last_ts is None:
            await self._state_store.set_state(ns, {"last_ts": _now_ts()})
            return
        if not isinstance(last_ts, str) or not last_ts:
            raise RuntimeError(f"Slack monitor state {ns!r} has an invalid last timestamp")

        # last_ts is the committed high-water mark. The stack contains exclusive
        # upper bounds still being scanned/replayed, oldest pending window last.
        raw_latest_stack = state.get(_HISTORY_LATEST_STACK)
        if raw_latest_stack is None:
            latest_stack = [_now_ts()]
            state[_HISTORY_LATEST_STACK] = latest_stack
            await self._state_store.set_state(ns, state)
        elif not isinstance(raw_latest_stack, list) or not all(
            isinstance(timestamp, str) and timestamp for timestamp in raw_latest_stack
        ):
            raise RuntimeError(f"Slack monitor state {ns!r} has an invalid history window")
        else:
            latest_stack = raw_latest_stack

        if not latest_stack:
            raise RuntimeError(f"Slack monitor state {ns!r} has an empty history window")

        page = await self._slack.messages.history_since(
            channel_ref,
            oldest=last_ts,
            latest=latest_stack[-1],
        )
        if page.has_more:
            if page.next_latest is None:
                raise RuntimeError("Slack history page omitted its continuation timestamp")
            latest_stack.append(page.next_latest)
            await self._state_store.set_state(ns, state)
            return

        channel = await self._slack.directory.resolve_channel(channel_ref)

        for message in page.messages:
            should_emit = message.subtype in {None, "file_share", "me_message", "thread_broadcast"}
            user_ref = message.user_ref
            if should_emit and message.bot_ref is None and user_ref != self_ref:
                if user_ref is None:
                    raise RuntimeError("Slack user message has no user ref")
                event = MessageReceived(
                    source="slack",
                    channel_id=channel_ref,
                    channel_name=channel.name,
                    user_id=user_ref,
                    user_name=await self._slack.directory.resolve_user_name(user_ref),
                    text=message.text,
                    ts=message.timestamp,
                    thread_ts=message.thread_timestamp,
                    permalink=None,
                )
                try:
                    await self._emit_event(event)
                except Exception:
                    _logger.exception("Failed to emit Slack message event for %s", channel_ref)
                    return
            state["last_ts"] = message.timestamp
            await self._state_store.set_state(ns, state)

        latest_stack.pop()
        if latest_stack:
            await self._state_store.set_state(ns, state)
        else:
            await self._state_store.set_state(ns, {"last_ts": state["last_ts"]})
