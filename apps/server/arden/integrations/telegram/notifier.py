from typing import Self

import aiohttp
from telegramify_markdown import TextInterpreter, telegramify

from arden.notifiers.base import Notifier, NotifierContext

_API_URL = "https://api.telegram.org/bot{token}/sendMessage"
_MAX_WORD_COUNT = 3900
_PARSE_MODE = "MarkdownV2"


async def _send(session: aiohttp.ClientSession, content: str, token: str, chat_id: str):
    parts = await telegramify(content, interpreters_use=[TextInterpreter()], max_word_count=_MAX_WORD_COUNT)
    chunks = [p.content for p in parts if p.content]  # text interpreter only returns text parts

    url = _API_URL.format(token=token)
    for chunk in chunks:
        payload = {"chat_id": chat_id, "text": chunk, "parse_mode": _PARSE_MODE}
        async with session.post(url, json=payload) as resp:
            if resp.status == 200:
                continue
            raise RuntimeError(f"Telegram send failed: {await resp.text()}")


class TelegramNotifier(Notifier):
    channel = "telegram"

    @classmethod
    def from_config(cls, config: dict, ctx: NotifierContext) -> Self:
        return cls(ctx=ctx, user_id=config["user_id"])

    def __init__(self, ctx: NotifierContext, user_id: str):
        self._ctx = ctx
        self._user_id = user_id

    async def send(self, subject: str, body: str) -> None:
        token = self._ctx.get_config_value("telegram_bot_token")
        if not token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN not set in config")

        source = f"# {subject}\n\n{body}".strip()
        async with aiohttp.ClientSession() as session:
            await _send(session, source, token, self._user_id)
