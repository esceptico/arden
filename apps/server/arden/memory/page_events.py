"""Exact page revisions and append-only page edit event records."""

from __future__ import annotations

import difflib
import hashlib
import json
import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, field_validator

from arden.memory.brand_markers import PAGE_EDIT_EVENT_MARKER, PAGE_EDIT_EVENT_MARKERS
from arden.memory.models import Kind, SourceRef
from arden.memory.reconciler import RecordOperation
from arden.memory.scopes import MemoryScope


def page_revision(content: bytes) -> str:
    if not isinstance(content, bytes):
        raise TypeError("page revision content must be bytes")
    return hashlib.sha256(content).hexdigest()


def unified_patch(base: bytes, result: bytes) -> str:
    try:
        before = base.decode("utf-8").splitlines(keepends=True)
        after = result.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError as exc:
        raise ValueError("memory pages must be UTF-8") from exc
    framed: list[str] = []
    for line in difflib.unified_diff(before, after, fromfile="a/page", tofile="b/page"):
        framed.append(line)
        if not line.endswith("\n"):
            framed.append("\n\\ No newline at end of file\n")
    return "".join(framed)


class PageEditDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    choice: Literal["Note only", "Forget memory"]
    target_ids: tuple[str, ...] = ()


class PageEditQuestion(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    operation_index: int
    question: str


class PageEditAnalysis(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    before: tuple[str, ...]
    after: tuple[str, ...]
    changed_before: tuple[str, ...]
    changed_after: tuple[str, ...]
    patch: str


class PageEditPreview(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    path: str
    base_revision: str
    result_revision: str
    patch: str
    operations: tuple[RecordOperation, ...]
    questions: tuple[PageEditQuestion, ...]
    analysis_pending: bool = False


class AppliedPageOperation(BaseModel):
    model_config = ConfigDict(frozen=True)

    op: Literal["ADD", "SUPERSEDE", "MERGE", "RETRACT", "NOOP"]
    text: str | None = None
    kind: Kind | None = None
    scope: MemoryScope | None = None
    target_ids: tuple[str, ...] = ()
    meta_labels: tuple[str, ...] | None = None
    entity_labels: tuple[str, ...] | None = None
    sources: tuple[SourceRef, ...] = ()

    @classmethod
    def from_operation(
        cls,
        operation: RecordOperation,
        sources: tuple[SourceRef, ...],
    ) -> AppliedPageOperation:
        if operation.op == "ASK":
            raise ValueError("ASK must be resolved before an event is applied")
        return cls(**operation.model_dump(), sources=sources)


class PageEditEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_type: Literal["PAGE_EDIT", "SYNTHESIS_MERGE"] = "PAGE_EDIT"
    id: str
    occurred_at: str
    sequence: int
    actor: str
    origin: Literal["desktop", "external", "agent", "synthesis"]
    path: str
    base_revision: str
    result_revision: str
    patch: str
    operations: tuple[AppliedPageOperation, ...]
    reconciliation: Literal["applied", "pending", "needs_review"]
    analysis: PageEditAnalysis | None = None
    reconciles_event_id: str | None = None
    review_operations: tuple[RecordOperation, ...] = ()
    questions: tuple[PageEditQuestion, ...] = ()
    review_event_id: str | None = None
    observation_id: str | None = None
    source_canonical_revision: str | None = None

    @field_validator("occurred_at")
    @classmethod
    def validate_occurred_at(cls, value: str) -> str:
        if re.search(r"\.\d{3}(?:Z|[+-]\d{2}:\d{2})$", value) is None:
            raise ValueError("page edit occurred_at requires milliseconds and an explicit offset")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("page edit occurred_at must be RFC 3339") from exc
        if parsed.tzinfo is None:
            raise ValueError("page edit occurred_at requires an explicit offset")
        return value


def render_page_edit_event(event: PageEditEvent) -> str:
    payload = event.model_dump_json(exclude_none=True)
    return f"{PAGE_EDIT_EVENT_MARKER}\n{payload}\n"


def parse_page_edit_events(content: str | bytes) -> tuple[PageEditEvent, ...]:
    if isinstance(content, bytes):
        content = content.decode("utf-8")
    events: list[PageEditEvent] = []
    lines = content.splitlines()
    for index, line in enumerate(lines):
        if line not in PAGE_EDIT_EVENT_MARKERS:
            continue
        if index + 1 >= len(lines):
            raise ValueError("page edit event marker has no payload")
        try:
            payload = json.loads(lines[index + 1])
        except json.JSONDecodeError as exc:
            raise ValueError("invalid page edit event JSON") from exc
        events.append(PageEditEvent.model_validate(payload))
    return tuple(events)


__all__ = [
    "AppliedPageOperation",
    "PageEditAnalysis",
    "PageEditDecision",
    "PageEditEvent",
    "PageEditPreview",
    "PageEditQuestion",
    "page_revision",
    "parse_page_edit_events",
    "render_page_edit_event",
    "unified_patch",
]
