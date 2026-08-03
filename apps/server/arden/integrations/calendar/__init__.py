from arden.config import Config
from arden.integrations.base import Integration, IntegrationConnectionSpec
from arden.integrations.calendar.client import MultiCalendarSource
from arden.integrations.calendar.tools import (
    calendar_tool,
    create_calendar_event_tool,
    delete_calendar_event_tool,
    edit_calendar_event_tool,
)
from arden.integrations.google_auth.auth import google_account_store, scopes_for_google_service


def _build(config: Config) -> MultiCalendarSource | None:
    if not config.integration_enabled("calendar"):
        return None
    store = google_account_store()
    token_paths = [store.token_path(account, "calendar") for account in store.accounts_for("calendar")]
    if not token_paths:
        return None
    source = MultiCalendarSource(token_paths=token_paths, days_back=7, days_ahead=30)
    return source if source.sources else None


CALENDAR = Integration(
    id="calendar",
    label="Google Calendar",
    tools={
        "calendar_search": calendar_tool,
        "calendar_create_event": create_calendar_event_tool,
        "calendar_edit_event": edit_calendar_event_tool,
        "calendar_delete_event": delete_calendar_event_tool,
    },
    build=_build,
    connection=IntegrationConnectionSpec(
        connection_id="calendar",
        capability="Read and manage calendar events",
        action="oauth",
        enabled=lambda config: config.integration_enabled("calendar"),
        configured=lambda _config: google_account_store().is_bound("calendar"),
        required_scopes=tuple(scopes_for_google_service("calendar")),
    ),
)
