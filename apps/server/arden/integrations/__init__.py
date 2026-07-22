from arden.integrations.base import (
    Integration,
    IntegrationField,
    IntegrationHealth,
    ToolProviderStatus,
)
from arden.integrations.calendar import CALENDAR
from arden.integrations.core import CORE_INTEGRATIONS
from arden.integrations.gmail import GMAIL
from arden.integrations.google_drive import GOOGLE_DRIVE
from arden.integrations.registry import IntegrationRegistry
from arden.integrations.slack import SLACK
from arden.integrations.telegram import TELEGRAM
from arden.integrations.web import WEB

ALL_INTEGRATIONS: list[Integration] = [
    *CORE_INTEGRATIONS,
    GMAIL,
    CALENDAR,
    GOOGLE_DRIVE,
    WEB,
    SLACK,
    TELEGRAM,
]

__all__ = [
    "ALL_INTEGRATIONS",
    "Integration",
    "IntegrationField",
    "IntegrationHealth",
    "IntegrationRegistry",
    "ToolProviderStatus",
]
