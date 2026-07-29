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
    RootModel,
    StrictStr,
    ValidationError,
    model_validator,
)

from arden.wiki.constants import TOPIC_PAGE_COLLISION_EVIDENCE_PREFIX
from arden.wiki.models import WikiMaintenancePageUpdate

_SHA256_PATTERN = r"^[0-9a-f]{64}$"


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("text must not be blank")
    return value


def _ordinary_concern_key(value: str) -> str:
    if value.startswith(TOPIC_PAGE_COLLISION_EVIDENCE_PREFIX):
        raise ValueError("concern key uses a reserved backend prefix")
    return value


type NonBlankText = Annotated[StrictStr, Field(min_length=1), AfterValidator(_nonblank)]
type RevisionId = Annotated[StrictStr, Field(pattern=_SHA256_PATTERN)]
type ConcernKey = Annotated[
    StrictStr,
    Field(pattern=r"^[a-z0-9][a-z0-9._:-]{0,120}$"),
    AfterValidator(_ordinary_concern_key),
]


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


class WikiMaintenanceMergeDraft(BaseModel):
    """A duplicate-page merge addressed only by prepared page tokens."""

    model_config = ConfigDict(extra="forbid", strict=True)

    canonical_page_token: Annotated[NonBlankText, Field(max_length=20)]
    loser_page_token: Annotated[NonBlankText, Field(max_length=20)]


class WikiMaintenanceDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    outcome: Literal["no_change", "updates", "needs_review"]
    updates: list[WikiMaintenanceUpdateDraft] = Field(default_factory=list)
    concern: WikiMaintenanceConcernDraft | None = None
    merge: WikiMaintenanceMergeDraft | None = None

    @model_validator(mode="after")
    def validate_outcome_shape(self) -> Self:
        if self.outcome == "no_change" and (self.updates or self.concern is not None or self.merge is not None):
            raise ValueError("no_change must not contain updates, a concern, or a merge")
        if self.outcome == "updates" and (not self.updates or self.concern is not None or self.merge is not None):
            raise ValueError("updates requires one or more updates and no concern or merge")
        if self.outcome == "needs_review" and (self.concern is None or (self.merge is not None and self.updates)):
            raise ValueError("needs_review requires a concern and at most one executable proposal")
        return self


class _PersistedUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    page_id: NonBlankText
    expected_version: RevisionId
    title: NonBlankText
    aliases: list[NonBlankText]
    body: StrictStr


class _PersistedProposalBase(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    reason: NonBlankText
    replay_fingerprint: RevisionId
    summary: NonBlankText


class _PersistedUpdatesProposal(_PersistedProposalBase):
    kind: Literal["maintenance_updates"]
    updates: list[_PersistedUpdate] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_unique_pages(self) -> Self:
        page_ids = [update.page_id for update in self.updates]
        if len(page_ids) != len(set(page_ids)):
            raise ValueError("proposal repeats a page")
        return self


class _PersistedPageMergeProposal(_PersistedProposalBase):
    kind: Literal["page_merge"]
    canonical_page_id: NonBlankText
    canonical_expected_version: RevisionId
    canonical_title: NonBlankText
    loser_page_id: NonBlankText
    loser_expected_version: RevisionId
    loser_title: NonBlankText
    link_count: int = Field(ge=0)
    page_count: int = Field(ge=0)
    redirect_count: Literal[0]

    @model_validator(mode="after")
    def validate_distinct_pages(self) -> Self:
        if self.canonical_page_id == self.loser_page_id:
            raise ValueError("page merge proposal must use distinct pages")
        return self


type PersistedProposalValue = Annotated[
    _PersistedUpdatesProposal | _PersistedPageMergeProposal,
    Field(discriminator="kind"),
]


class _PersistedProposal(RootModel[PersistedProposalValue]):
    pass


@dataclass(frozen=True, slots=True)
class WikiMaintenanceExecutableUpdates:
    reason: str
    replay_fingerprint: str
    summary: str
    updates: tuple[WikiMaintenancePageUpdate, ...]


@dataclass(frozen=True, slots=True)
class WikiMaintenanceExecutableMerge:
    reason: str
    replay_fingerprint: str
    summary: str
    canonical_page_id: str
    canonical_expected_version: str
    canonical_title: str
    loser_page_id: str
    loser_expected_version: str
    loser_title: str
    link_count: int
    page_count: int
    redirect_count: Literal[0]


type WikiMaintenanceExecutableProposal = WikiMaintenanceExecutableUpdates | WikiMaintenanceExecutableMerge


def fingerprint(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


def parse_maintenance_proposal(proposal_json: str) -> WikiMaintenanceExecutableProposal:
    """Parse the private, execution-complete shape persisted with an Ask."""

    try:
        proposal = _PersistedProposal.model_validate_json(proposal_json).root
    except (TypeError, ValidationError) as exc:
        raise WikiMaintenanceError("accepted maintenance proposal is malformed") from exc
    if isinstance(proposal, _PersistedPageMergeProposal):
        return WikiMaintenanceExecutableMerge(
            reason=proposal.reason,
            replay_fingerprint=proposal.replay_fingerprint,
            summary=proposal.summary,
            canonical_page_id=proposal.canonical_page_id,
            canonical_expected_version=proposal.canonical_expected_version,
            canonical_title=proposal.canonical_title,
            loser_page_id=proposal.loser_page_id,
            loser_expected_version=proposal.loser_expected_version,
            loser_title=proposal.loser_title,
            link_count=proposal.link_count,
            page_count=proposal.page_count,
            redirect_count=proposal.redirect_count,
        )
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
