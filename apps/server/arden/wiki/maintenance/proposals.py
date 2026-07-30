"""Strict contracts for agent-owned Wiki Maintenance decisions."""

import json
from hashlib import sha256
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    model_validator,
)


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("text must not be blank")
    return value


type NonBlankText = Annotated[StrictStr, Field(min_length=1), AfterValidator(_nonblank)]


class WikiMaintenanceError(RuntimeError):
    """Maintenance could not safely review or apply one contiguous commit."""


class WikiMaintenanceUpdateDraft(BaseModel):
    """A model proposal addressed only by a run-local opaque page token."""

    model_config = ConfigDict(extra="forbid", strict=True)

    page_token: Annotated[NonBlankText, Field(max_length=20)]
    title: NonBlankText
    aliases: list[NonBlankText] = Field(default_factory=list)
    body: StrictStr


class WikiMaintenanceDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    outcome: Literal["no_change", "updates"]
    updates: list[WikiMaintenanceUpdateDraft] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_outcome_shape(self) -> Self:
        if self.outcome == "no_change" and self.updates:
            raise ValueError("no_change must not contain updates")
        if self.outcome == "updates" and not self.updates:
            raise ValueError("updates requires one or more updates")
        return self


def fingerprint(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()
