from arden.config import Config
from arden.integrations.base import Integration, IntegrationConnectionSpec
from arden.integrations.gmail.client import MultiGmailSource
from arden.integrations.gmail.notifier import EmailNotifier
from arden.integrations.gmail.tools import email_read_tool, email_reply_tool, email_search_tool, email_send_tool
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
        "email_search": email_search_tool,
        "email_read": email_read_tool,
        "email_send": email_send_tool,
        "email_reply": email_reply_tool,
    },
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
