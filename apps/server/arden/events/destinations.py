"""Where an agent can send the user inside the Arden app.

This is the payload vocabulary of `navigation_requested` (and the `open_in_app`
tool schema). Desktop mirror: apps/desktop/src/api/navigation.ts.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HomeDestination(_StrictModel):
    kind: Literal["home"]


class SessionDestination(_StrictModel):
    kind: Literal["session"]
    session_id: str = Field(min_length=1, max_length=200)


class SettingsDestination(_StrictModel):
    kind: Literal["settings"]
    tab: (
        Literal[
            "connection",
            "providers",
            "integrations",
            "models",
            "agent",
            "context",
            "tools",
            "mcp",
            "appearance",
            "archive",
        ]
        | None
    ) = None


class AutomationDestination(_StrictModel):
    kind: Literal["automation"]
    task_id: str | None = Field(default=None, min_length=1, max_length=200)


class MemoryDestination(_StrictModel):
    # No page path: the desktop can only open the memory surface itself
    # (`openMemory()`), and a field the applier always refuses would just
    # invite a guaranteed-failing tool call.
    kind: Literal["memory"]


class AreaDestination(_StrictModel):
    kind: Literal["area"]
    area_id: str = Field(min_length=1, max_length=200)


AppDestination = Annotated[
    HomeDestination
    | SessionDestination
    | SettingsDestination
    | AutomationDestination
    | MemoryDestination
    | AreaDestination,
    Field(discriminator="kind"),
]
