from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HomeDestination(_StrictModel):
    kind: Literal["home"]


class SessionDestination(_StrictModel):
    kind: Literal["session"]
    session_id: str = Field(min_length=1, max_length=200)


class SettingsDestination(_StrictModel):
    kind: Literal["settings"]
    tab: Literal[
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
    ] | None = None


class AutomationDestination(_StrictModel):
    kind: Literal["automation"]
    task_id: str | None = Field(default=None, min_length=1, max_length=200)


class MemoryDestination(_StrictModel):
    kind: Literal["memory"]
    path: str | None = Field(default=None, min_length=1, max_length=1000)


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


class CommandChoice(_StrictModel):
    label: str = Field(min_length=1, max_length=120)
    query: str = Field(min_length=1, max_length=500)


class CommandOutcome(_StrictModel):
    status: Literal["completed", "needs_input", "unsupported", "failed"]
    summary: str = Field(min_length=1, max_length=500)
    destination: AppDestination | None = None
    prompt: str | None = Field(default=None, min_length=1, max_length=500)
    choices: list[CommandChoice] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def validate_status_contract(self):
        if self.status == "needs_input" and self.prompt is None:
            raise ValueError("needs_input requires prompt")
        if self.status != "needs_input" and (self.prompt is not None or self.choices):
            raise ValueError("prompt and choices are only valid for needs_input")
        return self


class CommandRunRequest(_StrictModel):
    query: str = Field(min_length=1, max_length=2000)
    client_id: str = Field(min_length=1, max_length=200)
    current_destination: AppDestination | None = None


class CommandRunResponse(_StrictModel):
    run_id: str
    session_id: str
    status: str
