from arden.config import Config
from arden.integrations.base import Integration, IntegrationConnectionError, IntegrationConnectionSpec
from arden.integrations.gmail.client import MultiGmailSource
from arden.integrations.gmail.notifier import EmailNotifier
from arden.integrations.gmail.read_tools import email_read_tool, email_search_tool
from arden.integrations.gmail.write_tools import email_reply_tool, email_send_tool
from arden.integrations.google_auth.accounts import validate_google_account_email
from arden.integrations.google_auth.auth import google_account_store, scopes_for_google_service


def _build(config: Config) -> MultiGmailSource | None:
    if not config.integration_enabled("gmail"):
        return None
    store = google_account_store()
    accounts = store.accounts_for("gmail")
    if not accounts:
        return None
    try:
        account_refs = [validate_google_account_email(account.email) for account in accounts]
    except (TypeError, ValueError) as exc:
        raise IntegrationConnectionError(
            integration_id="gmail",
            reason="auth_required",
            detail="Reconnect Gmail: connected account references are invalid.",
            retry_safe=False,
        ) from exc
    account_sources = [
        (account_ref, store.token_path(account, "gmail"))
        for account, account_ref in zip(accounts, account_refs, strict=True)
    ]
    return MultiGmailSource(account_sources=account_sources)


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
