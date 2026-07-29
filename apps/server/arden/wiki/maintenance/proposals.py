"""Strict contracts for maintenance decisions stored in durable review rows."""

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    ValidationError,
    model_validator,
)

from arden.wiki.models import WikiMaintenancePageUpdate

_SHA256_PATTERN = r"^[0-9a-f]{64}$"


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("text must not be blank")
    return value


type NonBlankText = Annotated[StrictStr, Field(min_length=1), AfterValidator(_nonblank)]
type RevisionId = Annotated[StrictStr, Field(pattern=_SHA256_PATTERN)]
type ConcernKey = Annotated[StrictStr, Field(pattern=r"^[a-z0-9][a-z0-9._:-]{0,120}$")]


class WikiMaintenanceError(RuntimeError):
    """Maintenance could not safely review or apply one contiguous commit."""


class WikiMaintenanceUpdateDraft(BaseModel):
    """A model proposal addressed only by a run-local opaque page token."""

    model_config = ConfigDict(extra="forbid", strict=True)

    page_token: Annotated[NonBlankText, Field(max_length=20)]
    title: NonBlankText
    aliases: list[NonBlankText] = Field(default_factory=list)
    body: StrictStr


class WikiMaintenanceConcernDraft(BaseModel):
    """A stable human-review concern returned by the completion model."""

    model_config = ConfigDict(extra="forbid", strict=True)

    key: ConcernKey
    summary: NonBlankText
    proposal: NonBlankText


class WikiMaintenanceDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    outcome: Literal["no_change", "updates", "needs_review"]
    updates: list[WikiMaintenanceUpdateDraft] = Field(default_factory=list)
    concern: WikiMaintenanceConcernDraft | None = None

    @model_validator(mode="after")
    def validate_outcome_shape(self) -> Self:
        if self.outcome == "no_change" and (self.updates or self.concern is not None):
            raise ValueError("no_change must not contain updates or a concern")
        if self.outcome == "updates" and (not self.updates or self.concern is not None):
            raise ValueError("updates requires one or more updates and no concern")
        if self.outcome == "needs_review" and self.concern is None:
            raise ValueError("needs_review requires a concern")
        return self


class _PersistedUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    page_id: NonBlankText
    expected_version: RevisionId
    title: NonBlankText
    aliases: list[NonBlankText]
    body: StrictStr


class _PersistedProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["maintenance_updates"]
    reason: NonBlankText
    replay_fingerprint: RevisionId
    summary: NonBlankText
    updates: list[_PersistedUpdate] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_pages(self) -> Self:
        page_ids = [update.page_id for update in self.updates]
        if len(page_ids) != len(set(page_ids)):
            raise ValueError("proposal repeats a page")
        return self


@dataclass(frozen=True, slots=True)
class WikiMaintenanceExecutableUpdates:
    reason: str
    replay_fingerprint: str
    summary: str
    updates: tuple[WikiMaintenancePageUpdate, ...]


def fingerprint(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


def parse_maintenance_proposal(proposal_json: str) -> WikiMaintenanceExecutableUpdates:
    """Parse the private, execution-complete shape persisted with an Ask."""

    try:
        proposal = _PersistedProposal.model_validate_json(proposal_json)
    except (TypeError, ValidationError) as exc:
        raise WikiMaintenanceError("accepted maintenance proposal is malformed") from exc
    return WikiMaintenanceExecutableUpdates(
        reason=proposal.reason,
        replay_fingerprint=proposal.replay_fingerprint,
        summary=proposal.summary,
        updates=tuple(
            WikiMaintenancePageUpdate(
                page_id=update.page_id,
                expected_version=update.expected_version,
                title=update.title,
                aliases=tuple(update.aliases),
                body=update.body.encode(),
            )
            for update in proposal.updates
        ),
    )
