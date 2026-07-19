from ntrp.config import Config
from ntrp.integrations.base import Integration, IntegrationConnectionSpec
from ntrp.integrations.calendar.client import MultiCalendarSource
from ntrp.integrations.calendar.tools import (
    calendar_tool,
    create_calendar_event_tool,
    delete_calendar_event_tool,
    edit_calendar_event_tool,
)
from ntrp.integrations.google_auth.auth import discover_calendar_tokens


def _build(config: Config) -> MultiCalendarSource | None:
    if not config.google:
        return None
    token_paths = discover_calendar_tokens()
    if not token_paths:
        return None
    source = MultiCalendarSource(token_paths=token_paths, days_back=7, days_ahead=30)
    return source if source.sources else None


CALENDAR = Integration(
    id="calendar",
    label="Google Calendar",
    tools={
        "calendar": calendar_tool,
        "create_calendar_event": create_calendar_event_tool,
        "edit_calendar_event": edit_calendar_event_tool,
        "delete_calendar_event": delete_calendar_event_tool,
    },
    build=_build,
    connection=IntegrationConnectionSpec(
        connection_id="google",
        capability="Read and manage calendar events",
        action="oauth",
        enabled=lambda config: config.google,
        configured=lambda _config: bool(discover_calendar_tokens()),
    ),
)
