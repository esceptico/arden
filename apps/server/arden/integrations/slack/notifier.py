from typing import Self

from arden.integrations.base import IntegrationConnectionError, IntegrationOperationError
from arden.integrations.slack.client import SlackClient
from arden.notifiers.base import Notifier, NotifierContext

_MAX_MESSAGE_CHARS = 40_000


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
            raise IntegrationConnectionError(
                integration_id="slack",
                reason="not_configured",
                detail="SLACK_BOT_TOKEN is not configured.",
                retry_safe=True,
            )

        text = f"*{subject}*\n\n{body}".strip()
        if len(text) > _MAX_MESSAGE_CHARS:
            raise IntegrationOperationError(
                code="payload_too_large",
                safe_message="Slack notifications must not exceed 40,000 characters.",
                retryable=False,
            )
        client = SlackClient(bot_token=token)
        await client.messages.post_message(self._target, text)
