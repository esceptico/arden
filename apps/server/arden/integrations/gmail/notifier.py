import asyncio
from typing import Self

from arden.notifiers.base import Notifier, NotifierContext


class EmailNotifier(Notifier):
    channel = "email"

    @classmethod
    def from_config(cls, config: dict, ctx: NotifierContext) -> Self:
        return cls(ctx=ctx, from_account=config["from_account"], to_address=config["to_address"])

    def __init__(self, ctx: NotifierContext, from_account: str, to_address: str):
        self._ctx = ctx
        self._from_account = from_account
        self._to_address = to_address

    async def send(self, subject: str, body: str) -> None:
        gmail = self._ctx.get_source("gmail")
        if not gmail:
            raise RuntimeError("Gmail source not available")

        await asyncio.to_thread(
            gmail.send_email,
            account=self._from_account,
            to=self._to_address,
            subject=subject,
            body=body,
            html=True,
        )
