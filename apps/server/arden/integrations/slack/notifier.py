from typing import Self

import aiohttp

from arden.notifiers.base import Notifier, NotifierContext

_API_URL = "https://slack.com/api/chat.postMessage"
_MAX_BLOCK_CHARS = 3000


def _split(text: str, limit: int) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= limit:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    return chunks


async def _send(session: aiohttp.ClientSession, token: str, channel: str, text: str) -> None:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"}
    for chunk in _split(text, _MAX_BLOCK_CHARS):
        payload = {"channel": channel, "text": chunk}
        async with session.post(_API_URL, json=payload, headers=headers) as resp:
            data = await resp.json()
            if not data.get("ok"):
                raise RuntimeError(f"Slack send failed: {data.get('error', 'unknown')}")


class SlackNotifier(Notifier):
    channel = "slack"

    @classmethod
    def from_config(cls, config: dict, ctx: NotifierContext) -> Self:
        return cls(ctx=ctx, target=config["channel"])

    def __init__(self, ctx: NotifierContext, target: str):
        self._ctx = ctx
        self._target = target

    async def send(self, subject: str, body: str) -> None:
        token = self._ctx.get_config_value("slack_bot_token")
        if not token:
            raise RuntimeError("SLACK_BOT_TOKEN not set in config")

        text = f"*{subject}*\n\n{body}".strip()
        async with aiohttp.ClientSession() as session:
            await _send(session, token, self._target, text)
