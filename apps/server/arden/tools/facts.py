"""Structured agent tools for the canonical fact ledger.

The tools deliberately sit above :class:`FactService`: callers never select an
owner, actor, origin, or authority scope.  The server derives those values
from the current session and Area, then attaches its own provenance to every
planned change.
"""

import base64
import binascii
import hashlib
import json
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from arden.agent.types.tools import ToolEffect, ToolOutcome, ToolOutcomeStatus, ToolSourceRef, normalize_source_refs
from arden.constants import BUILTIN_MEMORY_RETENTION_ID
from arden.core.public_refs import is_public_ref, public_ref
from arden.memory.facts.boundary import fact_read_scopes, fact_write_scope, source_time
from arden.memory.facts.models import (
    Fact,
    FactConflictError,
    FactEvent,
    FactLedgerCorruptionError,
    FactValidationError,
)
from arden.memory.facts.plan_store import (
    FactPlanCorruptionError,
    FactPlanOwnershipError,
    FactPlanRequestConflictError,
)
from arden.memory.facts.service import (
    DueFactReview,
    FactPostCommitError,
    FactPrincipal,
    FactScopeError,
    FactSearchUnavailableError,
    FactService,
)
from arden.tools.core import ToolResult, tool
from arden.tools.core.context import ToolExecution
from arden.tools.core.types import ToolAction, ToolPolicy, ToolScope

FACT_SERVICE = "facts"
_MAX_FACT_RESULTS = 100
_MAX_HISTORY_SOURCES = 50
_INTERNAL_SOURCE_EXTRA_FIELDS = frozenset(
    {
        "version",
        "wiki_commit_id",
        "page_version",
        "baseline_commit_id",
        "baseline_page_version",
    }
)
_MAX_CURSOR_CHARS = 2_048
_CURSOR_VERSION = 1


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


FactRef = Annotated[
    str,
    Field(
        min_length=8,
        max_length=80,
        description="Exact fact_ref returned by a fact read or fact_plan_changes.",
    ),
]


class FactSearchInput(_Input):
    query: str | None = Field(
        default=None,
        min_length=1,
        max_length=20_000,
        description=(
            "Natural-language query, relevance-ranked (semantic + lexical). Ranked results are the "
            "top matches in one page; omit the query to list facts chronologically with pagination."
        ),
    )
    subject: str | None = Field(default=None, min_length=1, max_length=500)
    status: Literal["active", "all"] = Field(
        default="active", description="active returns normal recall facts; all includes terminal history."
    )
    limit: int = Field(default=20, ge=1, le=_MAX_FACT_RESULTS)
    cursor: str | None = Field(
        default=None, max_length=_MAX_CURSOR_CHARS, description="Opaque cursor from a prior fact_search page."
    )


class FactGetInput(_Input):
    fact_ref: FactRef


class FactHistoryInput(_Input):
    fact_ref: FactRef
    limit: int = Field(default=50, ge=1, le=_MAX_FACT_RESULTS)
    cursor: str | None = Field(
        default=None, max_length=_MAX_CURSOR_CHARS, description="Opaque cursor from a prior history page."
    )


class FactDueReviewsInput(_Input):
    limit: int = Field(default=20, ge=1, le=_MAX_FACT_RESULTS)
    cursor: str | None = Field(
        default=None, max_length=_MAX_CURSOR_CHARS, description="Opaque cursor from a prior due-review page."
    )


class CreateFactChange(_Input):
    op: Literal["create"]
    text: str = Field(min_length=1, max_length=20_000)
    kind: str = Field(min_length=1, max_length=200)
    labels: list[str] = Field(default_factory=list, max_length=100)
    subjects: list[str] = Field(min_length=1, max_length=100)
    lifecycle: Literal["durable", "temporary"] = "durable"
    certainty: Literal["confirmed", "uncertain"] = "confirmed"
    evidence_class: Literal["direct", "inferred"] = "direct"
    review_at: str | None = Field(default=None, max_length=64)
    review_basis: Literal["explicit", "inferred", "fallback"] | None = None
    expires_at: str | None = Field(default=None, max_length=64)
    supersedes_fact_refs: list[FactRef] = Field(default_factory=list, max_length=100)
    reason: str | None = Field(default=None, min_length=1, max_length=4_000)

    @model_validator(mode="after")
    def _require_supersession_reason(self) -> Self:
        if self.reason is not None and not self.supersedes_fact_refs:
            raise ValueError("create reason requires supersedes_fact_refs")
        if self.supersedes_fact_refs and self.reason is None:
            raise ValueError("create supersedes_fact_refs requires reason")
        return self


class ReviewFactChange(_Input):
    op: Literal["review"]
    fact_ref: FactRef
    reason: str = Field(min_length=1, max_length=4_000)
    review_at: str | None = Field(default=None, max_length=64)
    review_basis: Literal["explicit", "inferred", "fallback"] | None = None
    certainty: Literal["confirmed", "uncertain"] | None = None
    evidence_fact_refs: list[FactRef] = Field(default_factory=list, max_length=100)


class AmendFactChange(_Input):
    op: Literal["amend"]
    fact_ref: FactRef
    kind: str | None = Field(default=None, min_length=1, max_length=200)
    labels: list[str] | None = Field(default=None, max_length=100)
    subjects: list[str] | None = Field(default=None, min_length=1, max_length=100)
    lifecycle: Literal["durable", "temporary"] | None = None
    evidence_class: Literal["direct", "inferred"] | None = None
    evidence_fact_refs: list[FactRef] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def _require_metadata(self) -> Self:
        if all(value is None for value in (self.kind, self.labels, self.subjects, self.lifecycle, self.evidence_class)):
            raise ValueError("amend requires metadata")
        return self


class SupersedeFactChange(_Input):
    op: Literal["supersede"]
    fact_ref: FactRef
    successor_fact_ref: FactRef
    reason: str = Field(min_length=1, max_length=4_000)
    evidence_fact_refs: list[FactRef] = Field(default_factory=list, max_length=100)


class ExpireFactChange(_Input):
    op: Literal["expire"]
    fact_ref: FactRef
    reason: str = Field(min_length=1, max_length=4_000)
    evidence_fact_refs: list[FactRef] = Field(default_factory=list, max_length=100)


class RetractFactChange(_Input):
    op: Literal["retract"]
    fact_ref: FactRef
    reason: str = Field(min_length=1, max_length=4_000)
    evidence_fact_refs: list[FactRef] = Field(default_factory=list, max_length=100)


FactChange = Annotated[
    CreateFactChange | ReviewFactChange | AmendFactChange | SupersedeFactChange | ExpireFactChange | RetractFactChange,
    Field(discriminator="op"),
]


class FactPlanChangesInput(_Input):
    changes: list[FactChange] = Field(min_length=1, max_length=30)
    reason: str = Field(
        min_length=1,
        max_length=4_000,
        description="Why this batch is justified. Stored with every immutable event.",
    )


class FactCommitChangesInput(_Input):
    plan_ref: str = Field(
        min_length=16,
        max_length=16,
        description="Exact plan_ref returned by fact_plan_changes.",
    )


class _UnknownFactRef(KeyError):
    pass


class _UnknownPlanRef(KeyError):
    pass


def _fact_ref(fact: Fact) -> str:
    return public_ref(fact.text, fact.fact_id, empty_slug="fact")


def _unavailable() -> ToolResult:
    return ToolResult.failure(
        code="not_configured",
        message="Canonical facts are not available.",
        preview="Facts unavailable",
        recovery_action="Configure the canonical fact service.",
    )


def _service(execution: ToolExecution) -> FactService | ToolResult:
    service = execution.ctx.services.get(FACT_SERVICE)
    return service if isinstance(service, FactService) else _unavailable()


def _session_id(execution: ToolExecution) -> str:
    return execution.ctx.session_id


def _automation_id(execution: ToolExecution) -> str | None:
    return execution.ctx.run.automation_id


def _is_retention(execution: ToolExecution) -> bool:
    return _automation_id(execution) == BUILTIN_MEMORY_RETENTION_ID


async def _principal(execution: ToolExecution, service: FactService) -> FactPrincipal:
    session_id = _session_id(execution)
    if _is_retention(execution):
        scopes = await service.known_scopes()
        return FactPrincipal(
            owner_id=f"automation:{BUILTIN_MEMORY_RETENTION_ID}",
            readable_scopes=scopes,
            writable_scopes=scopes,
        )
    scopes = fact_read_scopes(execution.ctx.area)
    # Write authority is supplied only by this server-side session/Area policy.
    # Individual changes never carry an authority grant.
    return FactPrincipal(owner_id=f"session:{session_id}", readable_scopes=scopes, writable_scopes=scopes)


def _fact_ref_index(facts: tuple[Fact, ...]) -> dict[str, Fact]:
    index: dict[str, Fact] = {}
    for fact in facts:
        ref = _fact_ref(fact)
        if ref in index:
            raise FactValidationError("A fact_ref collision prevents exact resolution.")
        index[ref] = fact
    return index


def _resolve_fact_ref(index: Mapping[str, Fact], fact_ref: str) -> Fact:
    if not is_public_ref(fact_ref):
        raise _UnknownFactRef(fact_ref)
    try:
        return index[fact_ref]
    except KeyError as exc:
        raise _UnknownFactRef(fact_ref) from exc


async def _resolve_plan_ref(service: FactService, principal: FactPrincipal, plan_ref: str) -> str:
    if not is_public_ref(plan_ref, slug="fact-plan"):
        raise _UnknownPlanRef(plan_ref)
    matches = [
        plan_id
        for plan_id in await service.plans.plan_ids_for_owner(principal.owner_id)
        if public_ref("fact-plan", plan_id, empty_slug="fact-plan") == plan_ref
    ]
    if not matches:
        raise _UnknownPlanRef(plan_ref)
    if len(matches) > 1:
        raise FactValidationError("A plan_ref collision prevents exact resolution.")
    return matches[0]


def _write_scope(execution: ToolExecution, kind: str) -> tuple[str, str | None]:
    return fact_write_scope(kind=kind, area=execution.ctx.area)


async def _sources(execution: ToolExecution, scope: tuple[str, str | None]) -> list[dict[str, Any]]:
    session_id = _session_id(execution)
    extra = {"session_id": session_id, "tool_name": execution.tool_name}
    if automation_id := _automation_id(execution):
        extra["origin_automation_id"] = automation_id
    source: dict[str, Any] = {
        "kind": "tool_call",
        "ref": execution.tool_id,
        "scope_kind": scope[0],
        "scope_key": scope[1],
        "role": "evidence",
        "extra": extra,
    }
    sources: list[dict[str, Any]] = [source]
    sessions = execution.ctx.services.get("session")
    if sessions is None:
        return sources
    page = await sessions.list_messages(session_id, limit=20)
    messages = page.get("messages", []) if isinstance(page, dict) else []
    row = next((item for item in reversed(messages) if item.get("role") == "user" and item.get("message_id")), None)
    if row is None:
        return sources
    occurred_at, precision = source_time(row.get("created_at"))
    message_source: dict[str, Any] = {
        "kind": "chat_message",
        "ref": str(row["message_id"]),
        "scope_kind": scope[0],
        "scope_key": scope[1],
        "role": "user",
        "extra": {"session_id": session_id, "seq": row.get("seq")},
    }
    if occurred_at is not None:
        message_source["occurred_at"] = occurred_at
        message_source["time_precision"] = precision
    sources.append(message_source)
    return sources


async def _evidence_sources(
    service: FactService,
    principal: FactPrincipal,
    fact_ids: tuple[str, ...],
    *,
    retained_facts: Mapping[str, Fact] | None = None,
) -> list[dict[str, Any]]:
    if len(set(fact_ids)) != len(fact_ids):
        raise ValueError("evidence_fact_refs must not contain duplicates")
    sources = []
    for fact_id in fact_ids:
        if retained_facts is None:
            fact = await service.get(principal, fact_id)
        else:
            try:
                fact = retained_facts[fact_id]
            except KeyError as exc:
                raise ValueError("Retention evidence must be a returned newer related fact") from exc
        fact_scope = (str(fact.scope["kind"]), fact.scope["key"])
        sources.append(
            {
                "kind": "fact",
                "ref": fact.fact_id,
                "scope_kind": fact_scope[0],
                "scope_key": fact_scope[1],
                "role": "evidence",
                "extra": {"version": fact.version},
            }
        )
    return sources


def _guard_retention_change(
    change: dict[str, Any],
    evidence_fact_ids: tuple[str, ...],
    review: DueFactReview,
    evaluated_at: datetime,
) -> dict[str, Fact]:
    op = change["op"]
    if op not in {"review", "supersede", "expire"}:
        raise ValueError("Retention may only review, supersede, or expire due temporary facts")
    allowed = {fact.fact_id: fact for fact in review.related_facts}
    if review.explicit_expiry_due:
        allowed[review.fact.fact_id] = review.fact
    if not set(evidence_fact_ids).issubset(allowed):
        raise ValueError("Retention evidence must be returned for this due fact")
    if op == "review":
        if review.explicit_expiry_due:
            raise ValueError("A due explicit expiry must be expired or superseded")
        if not evidence_fact_ids:
            if change.get("certainty") != "uncertain":
                raise ValueError("Retention review without newer evidence must mark the fact uncertain")
            review_at = change.get("review_at")
            review_basis = change.get("review_basis")
            try:
                next_review = datetime.fromisoformat(str(review_at))
            except ValueError as exc:
                raise ValueError("Uncertain Retention review requires an explicit future review_at") from exc
            if next_review.tzinfo is None or next_review.utcoffset() != timedelta(0) or next_review <= evaluated_at:
                raise ValueError("Uncertain Retention review requires an explicit future UTC review_at")
            if review_basis not in {"explicit", "inferred", "fallback"}:
                raise ValueError("Uncertain Retention review requires review_basis")
    if op == "expire" and not evidence_fact_ids:
        raise ValueError("Retention expiry requires explicit fact evidence")
    if op == "supersede" and change["successor_id"] not in evidence_fact_ids:
        raise ValueError("Retention supersession must cite its successor as evidence")
    return allowed


async def _prepared_changes(
    execution: ToolExecution,
    service: FactService,
    principal: FactPrincipal,
    changes: list[FactChange],
) -> list[dict[str, Any]]:
    retention = _is_retention(execution)
    review_batch = await service.retention_review_batch(principal) if retention else None
    reviews = {} if review_batch is None else {item.fact.fact_id: item for item in review_batch.reviews}
    fact_index = _fact_ref_index(await service.visible_facts(principal))
    prepared: list[dict[str, Any]] = []
    for item in changes:
        change = item.model_dump(exclude_none=True)
        evidence_fact_ids = tuple(
            _resolve_fact_ref(fact_index, ref).fact_id for ref in change.pop("evidence_fact_refs", ())
        )
        if change["op"] == "create":
            change["supersedes"] = [
                _resolve_fact_ref(fact_index, ref).fact_id for ref in change.pop("supersedes_fact_refs")
            ]
        else:
            change["fact_id"] = _resolve_fact_ref(fact_index, change.pop("fact_ref")).fact_id
        if change["op"] == "supersede":
            change["successor_id"] = _resolve_fact_ref(fact_index, change.pop("successor_fact_ref")).fact_id
        retained_facts = None
        if retention:
            try:
                review = reviews[change["fact_id"]]
            except KeyError as exc:
                raise ValueError("Retention may only change facts currently due for review") from exc
            assert review_batch is not None
            retained_facts = _guard_retention_change(
                change,
                evidence_fact_ids,
                review,
                review_batch.evaluated_at,
            )
            target = review.fact
            change["expected_version"] = target.version
            change["expected_review_window"] = review.review_window
        # Scope and provenance are derived by the server. Models cannot select
        # either for new facts, and every lifecycle event receives current evidence.
        if change["op"] == "create":
            scope = _write_scope(execution, str(change["kind"]))
            change["scope"] = {"kind": scope[0], "key": scope[1]}
        elif not retention:
            target = await service.get(principal, change["fact_id"])
            scope = (str(target.scope["kind"]), target.scope["key"])
        else:
            scope = (str(target.scope["kind"]), target.scope["key"])
        change["sources"] = [
            *await _sources(execution, scope),
            *await _evidence_sources(
                service,
                principal,
                evidence_fact_ids,
                retained_facts=retained_facts,
            ),
        ]
        prepared.append(change)
    return prepared


def _attribution(execution: ToolExecution) -> tuple[str, str]:
    if _is_retention(execution):
        return f"automation:{BUILTIN_MEMORY_RETENTION_ID}", "memory.retention"
    return f"session:{_session_id(execution)}", "tool.fact_changes"


def _quote(value: object) -> str:
    return json.dumps(value, ensure_ascii=True)


def _time(value: datetime | None) -> str | None:
    return None if value is None else value.isoformat()


def _source_summary(sources: tuple[Mapping[str, Any], ...] | tuple[Any, ...]) -> dict[str, Any]:
    kinds = sorted({str(source.get("kind", "unknown")) for source in sources if isinstance(source, Mapping)})
    return {"count": len(sources), "kinds": kinds}


def _sources_data(sources: tuple[Mapping[str, Any], ...] | tuple[Any, ...]) -> dict[str, Any]:
    items = []
    for source in sources[:_MAX_HISTORY_SOURCES]:
        if not isinstance(source, Mapping):
            continue
        item = dict(source)
        if isinstance(extra := item.get("extra"), Mapping):
            public_extra = {key: value for key, value in extra.items() if key not in _INTERNAL_SOURCE_EXTRA_FIELDS}
            if public_extra:
                item["extra"] = public_extra
            else:
                item.pop("extra")
        items.append(item)
    return {
        "items": items,
        "total": len(sources),
        "has_more": len(sources) > _MAX_HISTORY_SOURCES,
    }


def _fact_data(fact: Fact, *, include_sources: bool = False) -> dict[str, Any]:
    value: dict[str, Any] = {
        "fact_ref": _fact_ref(fact),
        "fact_id": fact.fact_id,
        "text": fact.text,
        "kind": fact.kind,
        "labels": list(fact.labels),
        "subjects": list(fact.subjects),
        "scope": dict(fact.scope),
        "lifecycle": fact.lifecycle,
        "status": fact.status,
        "certainty": fact.certainty,
        "evidence_class": fact.evidence_class,
        "created_at": _time(fact.created_at),
        "reviewed_at": _time(fact.reviewed_at),
        "review_at": _time(fact.review_at),
        "review_basis": fact.review_basis,
        "expires_at": _time(fact.expires_at),
        "successor_id": fact.successor_id,
        "source_summary": _source_summary(fact.sources),
    }
    if include_sources:
        value["sources"] = _sources_data(fact.sources)
    return value


def _event_data(event: FactEvent) -> dict[str, Any]:
    payload = dict(event.record["payload"])
    sources = payload.pop("sources", ())
    payload.pop("review_window", None)
    payload.pop("expected_version", None)
    value: dict[str, Any] = {
        "event_id": event.event_id,
        "plan_id": event.plan_id,
        "fact_id": event.fact_id,
        "op": event.op,
        "occurred_at": _time(event.occurred_at),
        "actor": event.actor,
        "origin": event.origin,
        "reason": event.plan_reason,
        "payload": payload,
    }
    if isinstance(sources, list):
        value["sources"] = _sources_data(tuple(sources))
    return value


def _fact_line(fact: Fact) -> str:
    review = _time(fact.review_at) or "none"
    return (
        f"- {_quote(_fact_ref(fact))} [{fact.status}/{fact.lifecycle}] {_quote(fact.text)}\n"
        f"  subjects: {', '.join(_quote(subject) for subject in fact.subjects)}; review_at: {_quote(review)}; "
        f"sources: {len(fact.sources)}"
    )


async def _plan_preview_content(
    service: FactService,
    principal: FactPrincipal,
    events: tuple[FactEvent, ...],
) -> str:
    refs = {fact.fact_id: _fact_ref(fact) for fact in await service.visible_facts(principal)}
    for event in events:
        if event.op != "create":
            continue
        text = event.record["payload"]["text"]
        ref = public_ref(text, event.fact_id, empty_slug="fact")
        if ref in refs.values():
            raise FactValidationError("A fact_ref collision prevents an exact plan preview.")
        refs[event.fact_id] = ref

    lines = []
    for event in events:
        payload = event.record["payload"]
        fact_ref = _quote(refs[event.fact_id])
        if event.op == "create":
            lines.append(f"- create {fact_ref}: {_quote(payload['text'])}")
        elif event.op == "review":
            review_at = "none" if payload["review_at"] is None else _quote(payload["review_at"])
            lines.append(f"- review {fact_ref}: {_quote(payload['reason'])} (next review: {review_at})")
        elif event.op == "amend":
            lines.append(f"- amend {fact_ref}: {', '.join(sorted(payload))}")
        elif event.op == "supersede":
            lines.append(
                f"- supersede {fact_ref} with {_quote(refs[payload['successor_id']])}: {_quote(payload['reason'])}"
            )
        else:
            lines.append(f"- {event.op} {fact_ref}: {_quote(payload['reason'])}")
    return "\n".join(lines)


class _InvalidCursor(ValueError):
    pass


class _StaleCursor(ValueError):
    pass


def _digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def _cursor_binding(kind: str, filters: object) -> str:
    return _digest({"kind": kind, "filters": filters})


def _encode_cursor(kind: str, binding: str, snapshot: str, anchor: tuple[str, ...]) -> str:
    value = {
        "version": _CURSOR_VERSION,
        "kind": kind,
        "binding": binding,
        "snapshot": snapshot,
        "anchor": list(anchor),
    }
    content = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(content).decode().rstrip("=")


def _decode_cursor(
    cursor: str,
    *,
    kind: str,
    binding: str,
    snapshot: str,
    anchor_size: int,
) -> tuple[str, ...]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        content = base64.b64decode(padded, altchars=b"-_", validate=True)
        value = json.loads(content)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise _InvalidCursor from exc
    if (
        not isinstance(value, dict)
        or set(value) != {"version", "kind", "binding", "snapshot", "anchor"}
        or type(value["version"]) is not int
        or value["version"] != _CURSOR_VERSION
        or value["kind"] != kind
        or value["binding"] != binding
        or not isinstance(value["snapshot"], str)
        or not isinstance(value["anchor"], list)
        or len(value["anchor"]) != anchor_size
        or any(not isinstance(item, str) for item in value["anchor"])
    ):
        raise _InvalidCursor
    anchor = tuple(value["anchor"])
    if _encode_cursor(kind, binding, value["snapshot"], anchor) != cursor:
        raise _InvalidCursor
    if value["snapshot"] != snapshot:
        raise _StaleCursor
    return anchor


def _cursor_anchor(
    cursor: str | None,
    *,
    kind: str,
    binding: str,
    snapshot: str,
    anchor_size: int,
) -> tuple[str, ...] | ToolResult | None:
    if cursor is None:
        return None
    try:
        return _decode_cursor(
            cursor,
            kind=kind,
            binding=binding,
            snapshot=snapshot,
            anchor_size=anchor_size,
        )
    except _StaleCursor:
        return _stale_page()
    except _InvalidCursor:
        return _invalid_cursor()


def _stale_page() -> ToolResult:
    return ToolResult.failure(
        code="write_conflict",
        message="Facts changed after this page was read.",
        preview="Fact page changed",
        recovery_action="Call the listing tool again without a cursor.",
    )


def _invalid_cursor() -> ToolResult:
    return ToolResult.failure(
        code="invalid_ref",
        message="Invalid fact pagination cursor.",
        preview="Invalid cursor",
        recovery_action="Call the fact listing tool again without a cursor.",
    )


def _after_time(anchor: tuple[str, ...]) -> tuple[datetime, str]:
    try:
        point = datetime.fromisoformat(anchor[0])
    except ValueError as exc:
        raise _InvalidCursor from exc
    if point.tzinfo is None or point.utcoffset() != timedelta(0):
        raise _InvalidCursor
    return point, anchor[1]


def _page(
    items: list[Any],
    *,
    limit: int,
    cursor: str | None,
    kind: str,
    binding: str,
    snapshot: str,
    key,
    anchor_size: int,
    exact_anchor: bool = False,
) -> tuple[tuple[Any, ...], dict[str, Any]] | ToolResult:
    anchor = _cursor_anchor(
        cursor,
        kind=kind,
        binding=binding,
        snapshot=snapshot,
        anchor_size=anchor_size,
    )
    if isinstance(anchor, ToolResult):
        return anchor
    remaining = items
    if anchor is not None:
        if exact_anchor:
            start = next((index + 1 for index, item in enumerate(items) if key(item) == anchor), None)
            if start is None:
                return ToolResult.failure(
                    code="invalid_ref",
                    message="Fact pagination cursor no longer names its immutable anchor.",
                    preview="Invalid cursor",
                    recovery_action="Call the fact listing tool again without a cursor.",
                )
            remaining = items[start:]
        else:
            remaining = [item for item in items if key(item) > anchor]
    selected = tuple(remaining[:limit])
    has_more = len(remaining) > limit
    return selected, {
        "total": len(items),
        "has_more": has_more,
        "next_cursor": (_encode_cursor(kind, binding, snapshot, key(selected[-1])) if has_more and selected else None),
    }


def _visibility(principal) -> dict[str, Any]:
    return {
        "owner_id": principal.owner_id,
        "readable_scopes": [
            [kind, key] for kind, key in sorted(principal.readable_scopes, key=lambda item: (item[0], item[1] or ""))
        ],
    }


def _fact_refs(facts: tuple[Fact, ...] | list[Fact]) -> tuple[ToolSourceRef, ...]:
    return normalize_source_refs(
        ToolSourceRef(provider="memory", kind="fact", ref=fact.fact_id, title=fact.text[:256]) for fact in facts
    )


def _failure(exc: Exception) -> ToolResult:
    if isinstance(exc, FactSearchUnavailableError):
        return ToolResult.failure(
            code="search_unavailable",
            message="The fact search index is not ready to serve queries.",
            preview="Fact search unavailable",
            retryable=True,
            recovery_action="Retry shortly, or call fact_search without a query to list facts.",
        )
    if isinstance(exc, FactPostCommitError):
        plan_ref = public_ref("fact-plan", exc.result.plan_id, empty_slug="fact-plan")
        return ToolResult.failure(
            code="post_commit_failed",
            message=(
                f"Fact plan {plan_ref} committed, but derived memory state did not refresh. "
                "Retry fact_commit_changes with the same plan_ref."
            ),
            preview="Facts committed; refresh failed",
            retryable=True,
            recovery_action="Retry fact_commit_changes with the same plan_ref.",
            data={
                "plan_ref": plan_ref,
                "plan_id": exc.result.plan_id,
                "committed": True,
                "events": [_event_data(event) for event in exc.result.events],
                "facts": [_fact_data(fact, include_sources=True) for fact in exc.result.facts],
            },
        )
    if isinstance(exc, FactScopeError | FactPlanOwnershipError):
        return ToolResult.failure(
            code="permission_denied",
            message="That fact operation is outside the current session's authority.",
            preview="Outside scope",
            status=ToolOutcomeStatus.DENIED,
            recovery_action="Use a session with the fact's Area or user scope.",
        )
    if isinstance(exc, _UnknownFactRef):
        return ToolResult.failure(
            code="not_found",
            message="No visible fact matches that exact fact_ref.",
            preview="Fact not found",
            recovery_action="Search facts and use an exact returned fact_ref.",
        )
    if isinstance(exc, _UnknownPlanRef):
        return ToolResult.failure(
            code="not_found",
            message="No owned fact plan matches that exact plan_ref.",
            preview="Fact plan not found",
            recovery_action="Prepare a new plan and use its exact returned plan_ref.",
        )
    if isinstance(exc, KeyError):
        return ToolResult.failure(
            code="not_found",
            message="The requested fact resource was not found.",
            preview="Fact resource not found",
            recovery_action="Search facts or prepare a new fact plan.",
        )
    if isinstance(exc, FactConflictError):
        return ToolResult.failure(
            code="write_conflict",
            message="Facts changed since this plan was prepared; nothing was committed.",
            preview="Fact plan stale",
            recovery_action="Search current facts, prepare a new plan, then commit it.",
        )
    if isinstance(exc, FactPlanRequestConflictError):
        return ToolResult.failure(
            code="request_conflict",
            message="This tool call's plan key was already used for different changes.",
            preview="Plan request changed",
            recovery_action="Start a new fact_plan_changes call.",
        )
    if isinstance(exc, FactPlanCorruptionError | FactLedgerCorruptionError):
        return ToolResult.failure(
            code="storage_corrupt",
            message="The saved fact plan failed integrity checks; nothing was committed.",
            preview="Plan unavailable",
            recovery_action="Prepare a fresh fact plan after the storage issue is resolved.",
        )
    if isinstance(exc, FactValidationError | ValueError):
        return ToolResult.failure(
            code="invalid_input",
            message=f"Invalid fact change: {exc}",
            preview="Invalid fact change",
            recovery_action="Correct the structured change and plan it again.",
        )
    raise exc


async def fact_search(execution: ToolExecution, args: FactSearchInput) -> ToolResult:
    service = _service(execution)
    if isinstance(service, ToolResult):
        return service
    try:
        principal = await _principal(execution, service)
        binding = _cursor_binding(
            "search",
            {
                **_visibility(principal),
                "query": args.query,
                "subject": args.subject,
                "status": args.status,
            },
        )
        revision = await service.revision()
        snapshot = revision or "empty"
        anchor = _cursor_anchor(
            args.cursor,
            kind="search",
            binding=binding,
            snapshot=snapshot,
            anchor_size=2,
        )
        if isinstance(anchor, ToolResult):
            return anchor
        try:
            after = None if anchor is None else _after_time(anchor)
        except _InvalidCursor:
            return _invalid_cursor()
        page = await service.search_page(
            principal,
            args.query,
            subject=args.subject,
            include_inactive=args.status == "all",
            limit=args.limit,
            after=after,
        )
        if await service.revision() != revision:
            return _stale_page()
        selected = page.items
        next_cursor = (
            _encode_cursor(
                "search",
                binding,
                snapshot,
                (_time(selected[-1].created_at) or "", selected[-1].fact_id),
            )
            if page.has_more and selected
            else None
        )
        content = "\n".join(_fact_line(fact) for fact in selected) or "No matching facts."
        return ToolResult(
            content=content,
            preview=f"{page.total} fact(s)",
            data={
                "facts": [_fact_data(fact) for fact in selected],
                "total": page.total,
                "has_more": page.has_more,
                "next_cursor": next_cursor,
            },
            source_refs=_fact_refs(selected),
        )
    except Exception as exc:
        return _failure(exc)


async def fact_get(execution: ToolExecution, args: FactGetInput) -> ToolResult:
    service = _service(execution)
    if isinstance(service, ToolResult):
        return service
    try:
        principal = await _principal(execution, service)
        fact = _resolve_fact_ref(_fact_ref_index(await service.visible_facts(principal)), args.fact_ref)
        return ToolResult(
            content=_fact_line(fact),
            preview=f"Fact {_fact_ref(fact)}",
            data={"fact": _fact_data(fact, include_sources=True)},
            source_refs=_fact_refs([fact]),
        )
    except Exception as exc:
        return _failure(exc)


async def fact_history(execution: ToolExecution, args: FactHistoryInput) -> ToolResult:
    service = _service(execution)
    if isinstance(service, ToolResult):
        return service
    try:
        principal = await _principal(execution, service)
        fact = _resolve_fact_ref(_fact_ref_index(await service.visible_facts(principal)), args.fact_ref)
        events = await service.history(principal, fact.fact_id)
        paged = _page(
            list(events),
            limit=args.limit,
            cursor=args.cursor,
            kind="history",
            binding=_cursor_binding("history", {**_visibility(principal), "fact_ref": args.fact_ref}),
            snapshot=_digest([event.event_id for event in events]),
            key=lambda event: (event.event_id,),
            anchor_size=1,
            exact_anchor=True,
        )
        if isinstance(paged, ToolResult):
            return paged
        selected, page = paged
        lines = [f"- {_time(event.occurred_at)} {event.op}: {_quote(args.fact_ref)}" for event in selected]
        return ToolResult(
            content="\n".join(lines) or "No fact history.",
            preview=f"{page['total']} event(s)",
            data={
                "fact_ref": args.fact_ref,
                "fact_id": fact.fact_id,
                "events": [_event_data(event) for event in selected],
                **page,
            },
        )
    except Exception as exc:
        return _failure(exc)


async def fact_due_reviews(execution: ToolExecution, args: FactDueReviewsInput) -> ToolResult:
    service = _service(execution)
    if isinstance(service, ToolResult):
        return service
    try:
        principal = await _principal(execution, service)
        binding = _cursor_binding("due", _visibility(principal))
        revision = await service.revision()
        snapshot = revision or "empty"
        anchor = _cursor_anchor(
            args.cursor,
            kind="due",
            binding=binding,
            snapshot=snapshot,
            anchor_size=2,
        )
        if isinstance(anchor, ToolResult):
            return anchor
        try:
            after = None if anchor is None else _after_time(anchor)
        except _InvalidCursor:
            return _invalid_cursor()
        page = await service.due_review_page(
            principal,
            limit=args.limit,
            after=after,
        )
        if await service.revision() != revision:
            return _stale_page()
        selected = page.items
        next_cursor = (
            _encode_cursor(
                "due",
                binding,
                snapshot,
                (_time(selected[-1].due_at) or "", selected[-1].fact.fact_id),
            )
            if page.has_more and selected
            else None
        )
        items = [
            {
                "fact": _fact_data(item.fact),
                "due_at": _time(item.due_at),
                "explicit_expiry_due": item.explicit_expiry_due,
                "related_facts": [_fact_data(fact) for fact in item.related_facts],
            }
            for item in selected
        ]
        lines = []
        for item in selected:
            related_refs = ", ".join(_quote(_fact_ref(fact)) for fact in item.related_facts) or "none"
            lines.append(
                f"- {_quote(_fact_ref(item.fact))} due {_quote(_time(item.due_at))}; "
                f"newer related evidence: {related_refs}"
            )
        return ToolResult(
            content="\n".join(lines) or "No facts are due for review.",
            preview=f"{page.total} due review(s)",
            data={
                "reviews": items,
                "total": page.total,
                "has_more": page.has_more,
                "next_cursor": next_cursor,
            },
            source_refs=_fact_refs([fact for item in selected for fact in (item.fact, *item.related_facts)]),
        )
    except Exception as exc:
        return _failure(exc)


async def fact_plan_changes(execution: ToolExecution, args: FactPlanChangesInput) -> ToolResult:
    service = _service(execution)
    if isinstance(service, ToolResult):
        return service
    try:
        principal = await _principal(execution, service)
        actor, origin = _attribution(execution)
        preview = await service.plan(
            principal,
            await _prepared_changes(execution, service, principal, args.changes),
            request_key=f"tool:{_session_id(execution)}:{execution.tool_id}",
            actor=actor,
            origin=origin,
            reason=args.reason,
        )
        plan_ref = public_ref("fact-plan", preview.plan_id, empty_slug="fact-plan")
        return ToolResult(
            content=f"Fact change plan ref: {plan_ref}\n\n{await _plan_preview_content(service, principal, preview.events)}",
            preview=f"{len(preview.events)} planned event(s)",
            data={
                "plan_ref": plan_ref,
                "plan_id": preview.plan_id,
                "events": [_event_data(event) for event in preview.events],
            },
        )
    except Exception as exc:
        return _failure(exc)


async def fact_commit_changes(execution: ToolExecution, args: FactCommitChangesInput) -> ToolResult:
    service = _service(execution)
    if isinstance(service, ToolResult):
        return service
    try:
        principal = await _principal(execution, service)
        plan_id = await _resolve_plan_ref(service, principal, args.plan_ref)
        committed = await service.commit(principal, plan_id)
        return ToolResult(
            content=f"Committed {len(committed.events)} fact event(s).",
            preview="Fact plan committed",
            data={
                "plan_ref": args.plan_ref,
                "plan_id": committed.plan_id,
                "events": [_event_data(event) for event in committed.events],
                "facts": [_fact_data(fact, include_sources=True) for fact in committed.facts],
            },
            source_refs=_fact_refs(committed.facts),
            outcome=ToolOutcome(
                status=ToolOutcomeStatus.SUCCEEDED,
                effect=ToolEffect(operation="commit", target=args.plan_ref),
            ),
        )
    except Exception as exc:
        return _failure(exc)


fact_search_tool = tool(
    display_name="Search Facts",
    display_description="Search canonical facts.",
    description=(
        "Search visible canonical facts. Queries are relevance-ranked (semantic + lexical) over active "
        "facts; status='all' switches to an exact-substring scan that includes terminal history. "
        "Returns stable fact_ref values for exact follow-up calls."
    ),
    input_model=FactSearchInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL, permissions=frozenset({FACT_SERVICE})),
    execute=fact_search,
)

fact_get_tool = tool(
    display_name="Get Fact",
    display_description="Read one canonical fact.",
    description="Read one visible fact by the exact fact_ref returned by fact_search or fact_plan_changes.",
    input_model=FactGetInput,
    policy=ToolPolicy(action=ToolAction.READ, scope=ToolScope.INTERNAL, permissions=frozenset({FACT_SERVICE})),
    execute=fact_get,
)

fact_history_tool = tool(
    display_name="Get Fact History",
    display_description="Read a fact's immutable event history.",
    description=(
        "Read saved events and provenance for an exact fact_ref returned by a fact read. "
        "This does not perform a live source lookup."
    ),
    input_model=FactHistoryInput,
    policy=ToolPolicy(
        action=ToolAction.READ, scope=ToolScope.INTERNAL, permissions=frozenset({FACT_SERVICE}), deferred=True
    ),
    execute=fact_history,
)

fact_due_reviews_tool = tool(
    display_name="Get Due Fact Reviews",
    display_description="List facts due for evidence-based review.",
    description="List visible due facts with newer shared-subject evidence. It recommends no lifecycle action.",
    input_model=FactDueReviewsInput,
    policy=ToolPolicy(
        action=ToolAction.READ, scope=ToolScope.INTERNAL, permissions=frozenset({FACT_SERVICE}), deferred=True
    ),
    execute=fact_due_reviews,
)

fact_plan_changes_tool = tool(
    display_name="Plan Fact Changes",
    display_description="Validate fact changes without publishing them.",
    description=(
        "Prepare creates, reviews, metadata amendments, supersessions, expiries, or retractions. "
        "Existing facts must use exact returned fact_ref values. Returns a durable preview and exact plan_ref; "
        "it does not publish facts."
    ),
    input_model=FactPlanChangesInput,
    policy=ToolPolicy(
        action=ToolAction.DRAFT,
        scope=ToolScope.INTERNAL,
        permissions=frozenset({FACT_SERVICE}),
        deferred=True,
        destructive=False,
        open_world=False,
        idempotent=False,
    ),
    execute=fact_plan_changes,
)

fact_commit_changes_tool = tool(
    display_name="Commit Fact Changes",
    display_description="Publish one previously planned fact change set.",
    description="Commit an exact plan_ref only if its affected facts remain unchanged since planning.",
    input_model=FactCommitChangesInput,
    policy=ToolPolicy(
        action=ToolAction.WRITE,
        scope=ToolScope.INTERNAL,
        permissions=frozenset({FACT_SERVICE}),
        deferred=True,
        destructive=True,
        open_world=False,
        idempotent=True,
    ),
    execute=fact_commit_changes,
)
