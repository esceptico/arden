from arden.integrations.slack.directory import SlackDirectory
from arden.integrations.slack.media import SlackMedia
from arden.integrations.slack.messages import SlackMessages
from arden.integrations.slack.transport import SlackTransport


class SlackClient:
    """Composition root for Slack's transport and typed domain adapters."""

    name = "slack"

    def __init__(self, bot_token: str | None = None, user_token: str | None = None):
        self.transport = SlackTransport(bot_token=bot_token, user_token=user_token)
        self.directory = SlackDirectory(self.transport)
        self.messages = SlackMessages(self.transport, self.directory)
        self.media = SlackMedia(self.transport, self.directory)

    async def verify_connection(self) -> None:
        await self.transport.auth_test()
