from typing import Literal

from pydantic import BaseModel, Field

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

