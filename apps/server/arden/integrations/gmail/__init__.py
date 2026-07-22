from arden.config import Config
from arden.integrations.base import Integration, IntegrationConnectionSpec
from arden.integrations.gmail.client import MultiGmailSource
from arden.integrations.gmail.notifier import EmailNotifier
from arden.integrations.gmail.tools import emails_tool, read_email_tool, reply_email_tool, send_email_tool
from arden.integrations.google_auth.auth import google_account_store, scopes_for_google_service


def _build(config: Config) -> MultiGmailSource | None:
    if not config.integration_enabled("gmail"):
        return None
    store = google_account_store()
    token_paths = [store.token_path(account, "gmail") for account in store.accounts_for("gmail")]
    if not token_paths:
        return None
    source = MultiGmailSource(token_paths=token_paths, days_back=config.gmail_days)
    return source if source.sources else None


GMAIL = Integration(
    id="gmail",
    label="Gmail",
    tools={
        "emails": emails_tool,
        "read_email": read_email_tool,
        "send_email": send_email_tool,
        "reply_email": reply_email_tool,
    },
    command_tool_names=frozenset({"emails", "read_email", "send_email", "reply_email"}),
    notifier_class=EmailNotifier,
    build=_build,
    connection=IntegrationConnectionSpec(
        connection_id="gmail",
        capability="Search, read, and send email",
        action="oauth",
        enabled=lambda config: config.integration_enabled("gmail"),
        configured=lambda _config: google_account_store().is_bound("gmail"),
        required_scopes=tuple(scopes_for_google_service("gmail")),
    ),
)
