import asyncio
from typing import Self

from arden.integrations.base import IntegrationConnectionError
from arden.integrations.gmail.client import MultiGmailSource
from arden.notifiers.base import Notifier, NotifierContext


class EmailNotifier(Notifier):
    channel = "email"

    @classmethod
    def from_config(cls, config: dict, ctx: NotifierContext) -> Self:
        return cls(ctx=ctx, account_ref=config["from_account"], recipient=config["to_address"])

    def __init__(self, ctx: NotifierContext, account_ref: str, recipient: str):
        self._ctx = ctx
        self._account_ref = account_ref
        self._recipient = recipient

    async def send(self, subject: str, body: str) -> None:
        gmail = self._ctx.get_source("gmail")
        if not isinstance(gmail, MultiGmailSource):
            raise IntegrationConnectionError(
                integration_id="gmail",
                reason="not_configured",
                detail="Gmail is not connected.",
                retry_safe=True,
                account_ref=self._account_ref,
            )

        prepared = await asyncio.to_thread(
            gmail.prepare_send,
            account_ref=self._account_ref,
            recipient=self._recipient,
            subject=subject,
            body=body,
            html=True,
        )
        await asyncio.to_thread(gmail.send_email, prepared)
