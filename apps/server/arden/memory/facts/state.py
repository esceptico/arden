"""Pure deterministic fact-state replay and lifecycle invariants."""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import replace
from datetime import datetime
from typing import Any

from arden.memory.facts.event_codec import canonical, chain_version, digest, parse_time, text, time_value, uuid_value
from arden.memory.facts.models import (
    Fact,
    FactConflictError,
    FactEvent,
    FactLedgerCorruptionError,
    FactPlan,
    FactValidationError,
)

TERMINAL = frozenset({"superseded", "expired", "retracted"})
REVIEW_WINDOW_LIMIT = 5
RETENTION_ORIGIN = "memory.retention"
MAINTENANCE_ORIGIN = "memory.maintenance"


def state_from(events: Sequence[FactEvent]) -> dict[str, Fact]:
    validate_event_order(events, error_type=FactLedgerCorruptionError)
    state: dict[str, Fact] = {}
    for event in events:
        if event.op == "create":
            if event.fact_id in state:
                raise FactLedgerCorruptionError("fact created more than once")
            state[event.fact_id] = fact_from_create(event)
            continue
        current = state.get(event.fact_id)
        if current is None:
            raise FactLedgerCorruptionError("event references an unknown fact")
        if current.status in TERMINAL:
            raise FactLedgerCorruptionError("terminal fact has a later event")
        payload = event.record["payload"]
        if event.op == "supersede":
            successor_id = payload["successor_id"]
            successor = state.get(successor_id)
            if successor is None:
                raise FactLedgerCorruptionError("successor must be created first")
            if current.evidence_class == "direct" and successor.evidence_class == "inferred":
                raise FactLedgerCorruptionError("inferred fact superseded direct fact")
            would_cycle(state, current.fact_id, successor_id)
        state[event.fact_id] = apply(current, event)
    validate_successor_graph(state, error_type=FactLedgerCorruptionError)
    return state


def storage_order(events: Sequence[FactEvent]) -> tuple[FactEvent, ...]:
    """Mirror the order produced by monthly append-only event files."""

    return tuple(sorted(events, key=lambda event: event.occurred_at.strftime("%Y-%m.jsonl")))


def fact_from_create(event: FactEvent) -> Fact:
    payload = event.record["payload"]
    return Fact(
        fact_id=event.fact_id,
        text=payload["text"],
        normalized_text=payload["normalized_text"],
        kind=payload["kind"],
        labels=tuple(payload["labels"]),
        subjects=tuple(payload["subjects"]),
        scope=dict(payload["scope"]),
        lifecycle=payload["lifecycle"],
        status="active",
        certainty=payload["certainty"],
        evidence_class=payload["evidence_class"],
        sources=tuple(payload["sources"]),
        created_at=event.occurred_at,
        reviewed_at=None,
        review_at=as_time(payload["review_at"], "review_at"),
        review_basis=payload["review_basis"],
        expires_at=as_time(payload["expires_at"], "expires_at"),
        successor_id=None,
        version=chain_version("", event.record),
    )


def apply(current: Fact, event: FactEvent) -> Fact:
    payload = event.record["payload"]
    changes: dict[str, Any] = {"version": chain_version(current.version, event.record)}
    if event.op == "review":
        changes["reviewed_at"] = event.occurred_at
    if event.op in {"review", "amend"}:
        for key in (
            "kind",
            "labels",
            "subjects",
            "scope",
            "lifecycle",
            "certainty",
            "evidence_class",
            "sources",
            "review_basis",
        ):
            if key in payload:
                value = payload[key]
                if key in {"labels", "subjects", "sources"}:
                    value = tuple(value)
                elif key == "scope":
                    value = dict(value)
                changes[key] = value
        for key in ("review_at", "expires_at"):
            if key in payload:
                changes[key] = as_time(payload[key], key)
    if event.op == "supersede":
        changes.update(status="superseded", successor_id=payload["successor_id"])
    elif event.op == "expire":
        changes["status"] = "expired"
    elif event.op == "retract":
        changes["status"] = "retracted"
    return replace(current, **changes)


def validate_event_order(
    events: Sequence[FactEvent], *, error_type: type[FactValidationError] | type[FactLedgerCorruptionError]
) -> None:
    latest: dict[str, datetime] = {}
    for event in events:
        previous = latest.get(event.fact_id)
        if previous is not None and event.occurred_at < previous:
            raise error_type(f"fact event time moved backwards: {event.fact_id}")
        latest[event.fact_id] = event.occurred_at


def validate_successor_graph(
    state: Mapping[str, Fact], *, error_type: type[FactValidationError] | type[FactLedgerCorruptionError]
) -> None:
    for fact in state.values():
        if fact.evidence_class != "direct" or fact.status != "superseded":
            continue
        cursor = fact
        seen = {fact.fact_id}
        while cursor.successor_id is not None:
            if cursor.successor_id in seen or cursor.successor_id not in state:
                raise error_type("invalid supersession graph")
            seen.add(cursor.successor_id)
            cursor = state[cursor.successor_id]
            if cursor.evidence_class == "inferred":
                raise error_type("direct fact cannot resolve to inferred evidence")


def would_cycle(state: Mapping[str, Fact], old_id: str, successor_id: str) -> None:
    seen = {old_id}
    cursor: str | None = successor_id
    while cursor is not None:
        if cursor in seen:
            raise FactValidationError("supersession cycle")
        seen.add(cursor)
        cursor = state[cursor].successor_id


def active(state: Mapping[str, Fact], fact_id: object, dependencies: dict[str, str]) -> Fact:
    fact_id = text(fact_id, "fact_id")
    try:
        fact = state[fact_id]
    except KeyError as exc:
        raise FactValidationError("unknown fact") from exc
    if fact.status != "active":
        raise FactValidationError("fact is terminal")
    dependencies.setdefault(fact_id, fact.version)
    return fact


def review_window_facts(fact: Fact, active_facts: Iterable[Fact]) -> tuple[Fact, ...]:
    evidence_cutoff = fact.reviewed_at or fact.created_at
    subjects = set(fact.subjects)
    return tuple(
        sorted(
            (
                candidate
                for candidate in active_facts
                if candidate.fact_id != fact.fact_id
                and candidate.scope == fact.scope
                and candidate.created_at > evidence_cutoff
                and subjects.intersection(candidate.subjects)
            ),
            key=lambda candidate: (candidate.created_at, candidate.fact_id),
            reverse=True,
        )[:REVIEW_WINDOW_LIMIT]
    )


def review_window(fact: Fact, related: Iterable[Fact]) -> str:
    cutoff = fact.reviewed_at or fact.created_at
    return digest(
        {
            "fact_id": fact.fact_id,
            "version": fact.version,
            "scope": dict(fact.scope),
            "subjects": list(fact.subjects),
            "cutoff": time_value(cutoff),
            "candidates": [{"fact_id": candidate.fact_id, "version": candidate.version} for candidate in related],
        }
    )


def duplicate_digest(state: Mapping[str, Fact], scope_kind: str, scope_key: str | None, normalized_text: str) -> str:
    return digest(
        sorted(
            fact.fact_id
            for fact in state.values()
            if fact.status == "active"
            and fact.scope == {"kind": scope_kind, "key": scope_key}
            and fact.normalized_text == normalized_text
        )
    )


def fact_window_key(fact: Fact) -> str:
    return canonical([fact.scope["kind"], fact.scope["key"], fact.normalized_text])


def changed_windows(
    before: Mapping[str, Fact],
    after: Mapping[str, Fact],
    window_parts: Callable[[str], tuple[str, str | None, str]],
) -> dict[str, str]:
    keys = {fact_window_key(fact) for fact in (*before.values(), *after.values()) if fact.status == "active"}
    return {
        key: duplicate_digest(before, *window_parts(key))
        for key in keys
        if duplicate_digest(before, *window_parts(key)) != duplicate_digest(after, *window_parts(key))
    }


def validate_retention_plan_invariants(
    events: Sequence[FactEvent], origin: str, fact_version: Callable[[object, str], str]
) -> None:
    for event in events:
        payload = event.record["payload"]
        has_window = "review_window" in payload
        if origin == RETENTION_ORIGIN:
            if event.op not in {"review", "supersede", "expire"}:
                raise FactValidationError("Retention may only review, supersede, or expire due temporary facts")
            if not has_window:
                raise FactValidationError("Retention changes require an expected review window")
        elif has_window:
            raise FactValidationError("review_window is reserved for Retention")
        if has_window:
            fact_version(payload["review_window"], "review_window")


def validate_maintenance_plan_invariants(events: Sequence[FactEvent], origin: str) -> None:
    for event in events:
        maintenance_merge = event.record["payload"].get("maintenance_merge")
        if origin == MAINTENANCE_ORIGIN:
            if event.op == "amend":
                if maintenance_merge is not None:
                    raise FactValidationError("metadata maintenance must not claim a duplicate merge")
                allowed = {
                    "kind",
                    "labels",
                    "subjects",
                    "lifecycle",
                    "evidence_class",
                    "review_at",
                    "review_basis",
                    "expires_at",
                }
                if unknown := set(event.record["payload"]).difference(allowed):
                    raise FactValidationError(
                        f"Memory Maintenance may amend only classification metadata: {sorted(unknown)}"
                    )
            elif event.op != "supersede" or maintenance_merge is not True:
                raise FactValidationError("Memory Maintenance may only amend metadata or merge duplicates")
        elif maintenance_merge is not None:
            raise FactValidationError("maintenance_merge is reserved for Memory Maintenance")


def as_time(value: object, field: str) -> datetime | None:
    return None if value is None else parse_time(value, field)


def validate_retention_plan_change(
    change: Mapping[str, Any],
    current: Fact,
    state: Mapping[str, Fact],
    point: datetime,
    fact_version: Callable[[object, str], str],
) -> None:
    if current.lifecycle != "temporary":
        raise FactValidationError("Retention may only change temporary facts")
    if "expected_version" not in change:
        raise FactValidationError("Retention changes require expected_version")
    due_at = min(item for item in (current.review_at, current.expires_at) if item is not None)
    if due_at > point:
        raise FactValidationError("Retention may only change facts currently due for review")
    expected = fact_version(change["expected_review_window"], "expected_review_window")
    related = review_window_facts(current, (fact for fact in state.values() if fact.status == "active"))
    if review_window(current, related) != expected:
        raise FactConflictError("newer related evidence changed since due review")


def validate_retention_review_windows(plan: FactPlan, state: Mapping[str, Fact]) -> None:
    for event in plan.events:
        expected = event.record["payload"].get("review_window")
        if expected is None:
            continue
        current = state.get(event.fact_id)
        if current is None or current.status != "active":
            raise FactConflictError(f"fact changed since due review: {event.fact_id}")
        related = review_window_facts(current, (fact for fact in state.values() if fact.status == "active"))
        if review_window(current, related) != expected:
            raise FactConflictError("newer related evidence changed since due review")


def validate_plan(
    plan: FactPlan,
    decode_event: Callable[[object], FactEvent],
    sources: Callable[[object], tuple[dict[str, Any], ...]],
    fact_version: Callable[[object, str], str],
    window_key_value: Callable[[object], str],
) -> None:
    if not isinstance(plan, FactPlan):
        raise TypeError("plan must be a FactPlan")
    try:
        plan_id = uuid_value(plan.plan_id, "plan_id")
    except FactValidationError:
        raise
    if type(plan.events) is not tuple or not plan.events:
        raise FactValidationError("fact plan events must be a non-empty tuple")
    decoded: list[FactEvent] = []
    for event in plan.events:
        if not isinstance(event, FactEvent):
            raise FactValidationError("fact plan contains an invalid event")
        try:
            stored = decode_event(event.record)
        except FactLedgerCorruptionError as exc:
            raise FactValidationError(str(exc)) from exc
        if stored != event or stored.plan_id != plan_id:
            raise FactValidationError("fact plan event does not match its canonical record")
        decoded.append(stored)
    if len({event.event_id for event in decoded}) != len(decoded):
        raise FactValidationError("fact plan contains duplicate event ids")
    try:
        _actor, origin, _reason = event_attribution(decoded)
    except FactLedgerCorruptionError as exc:
        raise FactValidationError(str(exc)) from exc
    validate_event_order(decoded, error_type=FactValidationError)
    validate_retention_plan_invariants(decoded, origin, fact_version)
    validate_maintenance_plan_invariants(decoded, origin)
    created = {event.fact_id for event in decoded if event.op == "create"}
    required_dependencies: set[str] = set()
    source_versions: dict[str, str] = {}
    seen_created: set[str] = set()
    for event in decoded:
        if event.op == "create":
            seen_created.add(event.fact_id)
        elif event.fact_id not in seen_created:
            required_dependencies.add(event.fact_id)
        if event.op == "supersede":
            successor_id = event.record["payload"]["successor_id"]
            if successor_id not in seen_created:
                required_dependencies.add(successor_id)
        payload = event.record["payload"]
        if "sources" in payload:
            for source in sources(payload["sources"]):
                if source["kind"] != "fact":
                    continue
                source_id = source["ref"]
                if source_id in created:
                    raise FactValidationError("fact source must reference pre-plan canonical evidence")
                version = fact_version(source["extra"]["version"], "source.extra.version")
                existing = source_versions.setdefault(source_id, version)
                if existing != version:
                    raise FactValidationError("fact plan cites conflicting evidence versions")
                required_dependencies.add(source_id)
    dependencies = plan_hashes(plan.dependencies, "dependencies", window_key_value)
    if set(dependencies) != required_dependencies:
        raise FactValidationError("fact plan dependencies do not match its events")
    if any(dependencies[fact_id] != version for fact_id, version in source_versions.items()):
        raise FactValidationError("fact plan dependency does not match cited evidence")
    plan_hashes(plan.duplicate_windows, "duplicate_windows", window_key_value)


def plan_hashes(value: object, field: str, window_key_value: Callable[[object], str]) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise FactValidationError(f"fact plan {field} must be a mapping")
    result: dict[str, str] = {}
    for key, item in value.items():
        try:
            clean_key = window_key_value(key) if field == "duplicate_windows" else text(key, field)
        except FactLedgerCorruptionError as exc:
            raise FactValidationError(str(exc)) from exc
        if not isinstance(item, str) or len(item) != 64 or any(char not in "0123456789abcdef" for char in item):
            raise FactValidationError(f"invalid fact plan {field} digest")
        result[clean_key] = item
    return result


def event_attribution(events: Sequence[FactEvent]) -> tuple[str, str, str]:
    if not events:
        raise FactLedgerCorruptionError("fact events must not be empty")
    attribution = (events[0].actor, events[0].origin, events[0].plan_reason)
    if any((event.actor, event.origin, event.plan_reason) != attribution for event in events):
        raise FactLedgerCorruptionError("fact plan has inconsistent event attribution")
    return attribution


def validate_plan_dependencies(
    plan: FactPlan,
    state: Mapping[str, Fact],
    window_parts: Callable[[str], tuple[str, str | None, str]],
) -> None:
    for fact_id, version in plan.dependencies.items():
        if fact_id not in state or state[fact_id].version != version:
            raise FactConflictError(f"fact changed since plan: {fact_id}")
    for key, digest_value in plan.duplicate_windows.items():
        scope_kind, scope_key, normalized_text = window_parts(key)
        if duplicate_digest(state, scope_kind, scope_key, normalized_text) != digest_value:
            raise FactConflictError("same-scope exact-text duplicate window changed")
