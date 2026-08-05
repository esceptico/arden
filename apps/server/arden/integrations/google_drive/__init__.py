from arden.config import Config
from arden.integrations.base import Integration, IntegrationConnectionSpec
from arden.integrations.google_auth.auth import google_account_store, scopes_for_google_service
from arden.integrations.google_drive.client import GoogleDriveClient, MultiGoogleDriveClient
from arden.integrations.google_drive.tools import DRIVE_TOOLS


def _build(config: Config) -> MultiGoogleDriveClient | None:
    if not config.integration_enabled("google_drive"):
        return None
    store = google_account_store()
    clients = [
        GoogleDriveClient(store.token_path(account, "google_drive"), account.email)
        for account in store.accounts_for("google_drive")
    ]
    return MultiGoogleDriveClient(clients) if clients else None


GOOGLE_DRIVE = Integration(
    id="google_drive",
    label="Google Drive",
    tools=DRIVE_TOOLS,
    build=_build,
    connection=IntegrationConnectionSpec(
        connection_id="google_drive",
        capability="Search, read, create, and edit Google Docs and Sheets",
        action="oauth",
        enabled=lambda config: config.integration_enabled("google_drive"),
        configured=lambda _config: google_account_store().is_bound("google_drive"),
        required_scopes=tuple(scopes_for_google_service("google_drive")),
    ),
)
