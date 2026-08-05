from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from arden.areas.display_contract import (
    DISPLAY_LINE_PATTERN,
    EVIDENCE_SUMMARY_MAX_CHARS,
    OUTCOME_TITLE_MAX_CHARS,
    SUCCESS_CRITERIA_MAX_CHARS,
    WORK_TEXT_MAX_CHARS,
)

OutcomeStatus = Literal["active", "paused", "completed", "cancelled"]
OutcomeSource = Literal["inferred", "user", "migration"]
WorkKind = Literal["loop", "action", "blocker"]
WorkStatus = Literal["active", "in_progress", "completed", "cancelled"]
WorkOwner = Literal["custodian", "user", "external"]


class AreaOutcome(BaseModel):
    outcome_id: str
    area_id: str
    stable_key: str
    title: str
    success_criteria: str
    status: OutcomeStatus
    priority: int = Field(ge=1, le=5)
    source: OutcomeSource
    created_at: str
    updated_at: str
    completed_at: str | None = None


class AreaWorkItem(BaseModel):
    item_id: str
    area_id: str
    stable_key: str
    outcome_id: str | None
    kind: WorkKind
    text: str
    status: WorkStatus
    owner: WorkOwner
    due_at: str | None = None
    next_attempt_at: str | None = None
    created_at: str
    updated_at: str
    completed_at: str | None = None


class AreaWorkEvent(BaseModel):
    event_id: int
    area_id: str
    outcome_id: str | None
    item_id: str | None
    run_ref: str | None
    event_type: str
    summary: str
    source_refs: list[str]
    created_at: str


class AreaWorkSnapshot(BaseModel):
    outcomes: list[AreaOutcome] = Field(default_factory=list)
    work_items: list[AreaWorkItem] = Field(default_factory=list)
    events: list[AreaWorkEvent] = Field(default_factory=list)


class OutcomeChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    op: Literal["create", "update", "complete", "cancel"]
    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    title: str | None = Field(
        default=None,
        min_length=1,
        max_length=OUTCOME_TITLE_MAX_CHARS,
        pattern=DISPLAY_LINE_PATTERN,
    )
    success_criteria: str | None = Field(default=None, min_length=1, max_length=SUCCESS_CRITERIA_MAX_CHARS)
    priority: int | None = Field(default=None, ge=1, le=5)
    expected_updated_at: str | None = None

    @model_validator(mode="after")
    def validate_operation(self):
        if self.op == "create" and (self.title is None or self.success_criteria is None or self.priority is None):
            raise ValueError("create outcome requires title, success_criteria, and priority")
        if self.op == "update" and self.title is None and self.success_criteria is None and self.priority is None:
            raise ValueError("update outcome requires at least one changed field")
        if self.op != "create" and self.expected_updated_at is None:
            raise ValueError("non-create outcome operation requires expected_updated_at")
        return self


class WorkChange(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    op: Literal["create", "update", "complete", "cancel"]
    key: str = Field(min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    outcome_key: str | None = Field(default=None, min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    kind: WorkKind | None = None
    text: str | None = Field(
        default=None,
        min_length=1,
        max_length=WORK_TEXT_MAX_CHARS,
        pattern=DISPLAY_LINE_PATTERN,
    )
    owner: WorkOwner | None = None
    due_at: str | None = None
    next_attempt_at: str | None = None
    expected_updated_at: str | None = None

    @model_validator(mode="after")
    def validate_operation(self):
        if self.op == "create" and (self.kind is None or self.text is None or self.owner is None):
            raise ValueError("create work item requires kind, text, and owner")
        if self.op == "update" and all(
            value is None for value in (self.outcome_key, self.text, self.owner, self.due_at, self.next_attempt_at)
        ):
            raise ValueError("update work item requires at least one changed field")
        if self.op != "create" and self.expected_updated_at is None:
            raise ValueError("non-create work operation requires expected_updated_at")
        return self


class EvidenceDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)

    target_type: Literal["area", "outcome", "work"]
    target_key: str | None = Field(default=None, min_length=1, max_length=80, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    event_type: Literal["created", "progress", "completed", "blocked", "updated", "cancelled"]
    summary: str = Field(
        min_length=1,
        max_length=EVIDENCE_SUMMARY_MAX_CHARS,
        pattern=DISPLAY_LINE_PATTERN,
    )
    source_refs: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_target(self):
        if self.target_type == "area" and self.target_key is not None:
            raise ValueError("Area evidence does not take target_key")
        if self.target_type != "area" and self.target_key is None:
            raise ValueError("Outcome/work evidence requires target_key")
        return self
