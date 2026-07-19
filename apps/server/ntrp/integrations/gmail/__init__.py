from ntrp.config import Config
from ntrp.integrations.base import Integration, IntegrationConnectionSpec
from ntrp.integrations.gmail.client import MultiGmailSource
from ntrp.integrations.gmail.notifier import EmailNotifier
from ntrp.integrations.gmail.tools import emails_tool, read_email_tool, send_email_tool
from ntrp.integrations.google_auth.auth import discover_gmail_tokens


def _build(config: Config) -> MultiGmailSource | None:
    if not config.google:
        return None
    token_paths = discover_gmail_tokens()
    if not token_paths:
        return None
    source = MultiGmailSource(token_paths=token_paths, days_back=config.gmail_days)
    return source if source.sources else None


GMAIL = Integration(
    id="gmail",
    label="Gmail",
    tools={"emails": emails_tool, "read_email": read_email_tool, "send_email": send_email_tool},
    notifier_class=EmailNotifier,
    build=_build,
    connection=IntegrationConnectionSpec(
        connection_id="google",
        capability="Search, read, and send email",
        action="oauth",
        enabled=lambda config: config.google,
        configured=lambda _config: bool(discover_gmail_tokens()),
    ),
)
