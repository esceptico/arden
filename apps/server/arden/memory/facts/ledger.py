"""Strict append-only facts backed by the crash-safe managed-file repository."""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

from arden.revisions import ChangeSet, Create, ManagedFileRepository, ResourceState, Update
from arden.revisions.errors import (
    CorruptRepositoryError,
    IdempotencyConflictError,
    RevisionConflictError,
)

from .models import (
    DueReviewCandidate,
    Fact,
    FactConflictError,
    FactEvent,
    FactLedgerCorruptionError,
    FactPlan,
    FactValidationError,
)

_VERSION = 1
_MONTH = re.compile(r"^\d{4}-\d{2}\.jsonl$")
_MARKER_PATH = ".ledger.json"
_MARKER_RESOURCE_ID = "fact-ledger-schema"
_MARKER_CONTENT = b'{"schema_version":1}\n'
_RFC3339_UTC = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)$")
_OPS = frozenset({"create", "review", "amend", "supersede", "expire", "retract"})
_LIFECYCLES = frozenset({"durable", "temporary"})
_CERTAINTIES = frozenset({"confirmed", "uncertain"})
_EVIDENCE = frozenset({"direct", "inferred"})
_SCOPE_KINDS = frozenset({"user", "area", "global", "project", "integration"})
_REVIEW_BASES = frozenset({"explicit", "inferred", "fallback"})
_TIME_PRECISIONS = frozenset({"millisecond", "second", "minute", "day", "unknown"})
_TERMINAL = frozenset({"superseded", "expired", "retracted"})
_FALLBACK_REVIEW = timedelta(days=90)
_SOURCE_REQUIRED = frozenset({"kind", "ref"})
_SOURCE_OPTIONAL = frozenset(
    {
        "captured_at",
        "scope_kind",
        "scope_key",
        "occurred_at",
        "time_precision",
        "role",
        "excerpt_hash",
        "extra",
    }
)


class _SnapshotChanged(RuntimeError):
    pass


@dataclass(frozen=True)
class _FactSnapshot:
    """One validated repository view, retained through publication."""

    head: str | None
    events: tuple[FactEvent, ...]
    state: Mapping[str, Fact]
    resources: Mapping[str, Any]
    contents: Mapping[str, bytes]


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _chain_version(previous: str, record: Mapping[str, Any]) -> str:
    return hashlib.sha256((previous + "\0" + _canonical(record)).encode()).hexdigest()


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise FactValidationError(f"{field} must be a non-empty string")
    return value


def _reference(value: object) -> str:
    if not isinstance(value, str) or "\0" in value:
        raise FactValidationError("source.ref must be a string without NUL")
    return value


def _uuid(value: object, field: str) -> str:
    text = _text(value, field)
    try:
        parsed = uuid.UUID(text)
    except ValueError as exc:
        raise FactValidationError(f"{field} must be a UUID") from exc
    if str(parsed) != text:
        raise FactValidationError(f"{field} must be a canonical UUID")
    return text


def _parse_time(value: object, field: str) -> datetime:
    if not isinstance(value, str) or _RFC3339_UTC.fullmatch(value) is None:
        raise FactValidationError(f"{field} must be an RFC3339 UTC string")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FactValidationError(f"invalid {field}") from exc
    if result.tzinfo is None or result.utcoffset() != timedelta(0):
        raise FactValidationError(f"{field} must be UTC")
    return result.astimezone(UTC)


def _change_time(value: object) -> datetime:
    """Validate the migration-only top-level timestamp before canonicalizing it."""
    if not isinstance(value, str) or _RFC3339_UTC.fullmatch(value) is None:
        raise FactValidationError("occurred_at must be an RFC3339 UTC string")
    return _parse_time(value, "occurred_at")


def _time(value: datetime) -> str:
    if value.tzinfo is None:
        raise FactValidationError("timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _optional_time(value: object, field: str) -> str | None:
    return None if value is None else _time(_parse_time(value, field))


def _source_precision_error(value: object, precision: object) -> str | None:
    if precision in (None, "unknown"):
        return None if value is None else "source.occurred_at requires known time_precision"
    if value is None:
        return "known source.time_precision requires occurred_at"
    if precision == "day":
        return (
            None
            if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)
            else "day source.time_precision requires a date"
        )
    if not isinstance(value, str):
        return f"{precision} source.time_precision requires an RFC3339 timestamp"
    timestamp = re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:(?P<seconds>\d{2})(?P<fraction>\.\d+)?(?:Z|[+-]\d{2}:\d{2})",
        value,
    )
    if timestamp is None:
        return f"{precision} source.time_precision requires an RFC3339 timestamp"
    fraction = timestamp["fraction"]
    if precision == "minute" and (timestamp["seconds"] != "00" or fraction is not None):
        return "minute source.time_precision requires zero seconds and no fraction"
    if precision == "second" and fraction is not None:
        return "second source.time_precision forbids fractional seconds"
    if precision == "millisecond" and (fraction is None or len(fraction) != 4):
        return "millisecond source.time_precision requires exactly three fractional digits"
    return None


def _normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def _strings(value: object, field: str, *, required: bool = False) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise FactValidationError(f"{field} must be a list")
    result = tuple(_text(item, field) for item in value)
    if required and not result:
        raise FactValidationError(f"{field} must not be empty")
    if len(set(result)) != len(result):
        raise FactValidationError(f"{field} must not contain duplicates")
    return result


def _scope_key(kind: str, value: object, field: str) -> str | None:
    if kind in {"global", "user"}:
        if value is not None:
            raise FactValidationError(f"{field} must be null for {kind} scope")
        return None
    return _text(value, field)


def _scope(value: object) -> dict[str, str | None]:
    if not isinstance(value, Mapping) or set(value) != {"kind", "key"}:
        raise FactValidationError("scope must contain exactly kind and key")
    kind = _text(value["kind"], "scope.kind")
    if kind not in _SCOPE_KINDS:
        raise FactValidationError("invalid scope kind")
    return {"kind": kind, "key": _scope_key(kind, value["key"], "scope.key")}


def _json_value(value: object, field: str) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise FactValidationError(f"{field} must be JSON-safe")
        return value
    if isinstance(value, (list, tuple)):
        return [_json_value(item, field) for item in value]
    if isinstance(value, Mapping):
        result = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise FactValidationError(f"{field} keys must be strings")
            result[key] = _json_value(item, field)
        return result
    raise FactValidationError(f"{field} must be JSON-safe")


def _source(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise FactValidationError("each source must be an object")
    if not set(value) >= _SOURCE_REQUIRED or set(value) - _SOURCE_REQUIRED - _SOURCE_OPTIONAL:
        raise FactValidationError("invalid SourceRef fields")
    result: dict[str, Any] = {
        "kind": _text(value["kind"], "source.kind"),
        "ref": _reference(value["ref"]),
    }
    for field in _SOURCE_OPTIONAL - {"extra"}:
        if field not in value:
            continue
        item = value[field]
        if item is None:
            result[field] = None
        else:
            result[field] = _text(item, f"source.{field}")
    if "extra" in value:
        if not isinstance(value["extra"], Mapping):
            raise FactValidationError("source.extra must be an object")
        result["extra"] = _json_value(value["extra"], "source.extra")
    precision = result.get("time_precision")
    if "time_precision" in result and precision not in _TIME_PRECISIONS:
        raise FactValidationError("invalid source.time_precision")
    scope_kind = result.get("scope_kind")
    scope_key = result.get("scope_key")
    if scope_kind is None:
        if scope_key is not None:
            raise FactValidationError("source scope_kind and scope_key must appear together")
    else:
        if scope_kind not in _SCOPE_KINDS:
            raise FactValidationError("invalid source.scope_kind")
        result["scope_key"] = _scope_key(scope_kind, scope_key, "source.scope_key")
    if result.get("captured_at") is not None:
        _parse_time(result["captured_at"], "source.captured_at")
    occurred_at = result.get("occurred_at")
    if error := _source_precision_error(occurred_at, precision):
        raise FactValidationError(error)
    if occurred_at is not None:
        if precision == "day":
            try:
                date.fromisoformat(occurred_at)
            except ValueError as exc:
                raise FactValidationError("invalid source.occurred_at") from exc
        else:
            _parse_time(occurred_at, "source.occurred_at")
    return result


def _sources(value: object) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise FactValidationError("sources must be a non-empty list")
    return tuple(_source(item) for item in value)


class FactLedger:
    """Process-independent plans over immutable monthly fact events."""

    def __init__(self, root: Path, *, clock: Callable[[], datetime] | None = None) -> None:
        root = Path(root)
        self._repository = ManagedFileRepository(root / "records", history_root=root / ".fact-history")
        self._clock = clock or (lambda: datetime.now(UTC))

    @property
    def root(self) -> Path:
        return self._repository.root.parent

    @property
    def records_root(self) -> Path:
        return self._repository.root

    @property
    def history_root(self) -> Path:
        return self._repository.history_root

    def get(self, fact_id: str) -> Fact:
        try:
            return self._snapshot().state[fact_id]
        except KeyError as exc:
            raise KeyError(f"unknown fact: {fact_id}") from exc

    def search(
        self,
        query: str | None = None,
        *,
        scope_kind: str | None = None,
        scope_key: str | None = None,
        subject: str | None = None,
        include_inactive: bool = False,
    ) -> tuple[Fact, ...]:
        needle = None if query is None else _normalized(_text(query, "query"))
        facts = self._snapshot().state.values()
        return tuple(
            fact
            for fact in sorted(facts, key=lambda item: (item.created_at, item.fact_id))
            if (include_inactive or fact.status == "active")
            and (needle is None or needle in fact.normalized_text)
            and (scope_kind is None or fact.scope["kind"] == scope_kind)
            and (scope_key is None or fact.scope["key"] == scope_key)
            and (subject is None or subject in fact.subjects)
        )

    def history(self, fact_id: str) -> tuple[FactEvent, ...]:
        events = tuple(event for event in self._snapshot().events if event.fact_id == fact_id)
        if not events:
            raise KeyError(f"unknown fact: {fact_id}")
        return events

    def due_reviews(self, *, now: datetime | None = None) -> tuple[DueReviewCandidate, ...]:
        point = self._now() if now is None else self._utc(now)
        result = []
        for fact in self._snapshot().state.values():
            candidates = [item for item in (fact.review_at, fact.expires_at) if item is not None]
            if fact.status != "active" or not candidates:
                continue
            due_at = min(candidates)
            if due_at <= point:
                result.append(DueReviewCandidate(fact, due_at))
        return tuple(sorted(result, key=lambda item: (item.due_at, item.fact.fact_id)))

    def active_subject_count(
        self,
        subject: str,
        *,
        scope_kind: str | None = None,
        scope_key: str | None = None,
    ) -> int:
        return sum(
            fact.status == "active"
            and subject in fact.subjects
            and (scope_kind is None or fact.scope["kind"] == scope_kind)
            and (scope_key is None or fact.scope["key"] == scope_key)
            for fact in self._snapshot().state.values()
        )

    def plan(
        self,
        changes: Iterable[Mapping[str, Any]],
        *,
        actor: str,
        origin: str,
        reason: str,
    ) -> FactPlan:
        actor = _text(actor, "actor")
        origin = _text(origin, "origin")
        reason = _text(reason, "reason")
        snapshot = self._snapshot()
        base_events, base = snapshot.events, snapshot.state
        batch = tuple(changes)
        if not batch:
            raise FactValidationError("changes must not be empty")
        plan_id = str(uuid.uuid4())
        dependencies: dict[str, str] = {}
        events: list[FactEvent] = []
        working = dict(base)
        plan_point = self._now()
        for change in batch:
            if not isinstance(change, Mapping):
                raise FactValidationError("each change must be an object")
            op = change.get("op")
            if op not in _OPS:
                raise FactValidationError("unknown fact operation")
            point = plan_point if "occurred_at" not in change else _change_time(change["occurred_at"])
            if op == "create":
                event, fact = self._create_event(change, plan_id, point, working, actor, origin, reason)
                events.append(event)
                working[fact.fact_id] = fact
                supersedes = change.get("supersedes", ())
                if not isinstance(supersedes, (list, tuple)):
                    raise FactValidationError("supersedes must be a list")
                if not supersedes and "reason" in change:
                    raise FactValidationError("create reason requires supersedes")
                for old_id in supersedes:
                    if old_id == fact.fact_id:
                        raise FactValidationError("supersession cycle")
                    old = self._active(working, old_id, dependencies)
                    if old.evidence_class == "direct" and fact.evidence_class == "inferred":
                        raise FactValidationError("inferred evidence may not supersede direct evidence")
                    supersession_reason = _text(change.get("reason"), "reason")
                    event = self._event(
                        plan_id,
                        old.fact_id,
                        "supersede",
                        point,
                        {
                            "successor_id": fact.fact_id,
                            "reason": supersession_reason,
                            "sources": list(_sources(fact.sources)),
                        },
                        actor,
                        origin,
                        reason,
                    )
                    events.append(event)
                    working[old.fact_id] = self._apply(old, event)
                continue
            fact_id = _text(change.get("fact_id"), "fact_id")
            current = self._active(working, fact_id, dependencies)
            if op == "amend" and "text" in change:
                raise FactValidationError("text amendments require create with supersedes")
            payload = self._change_payload(op, change, current, working, dependencies, point)
            event = self._event(plan_id, fact_id, str(op), point, payload, actor, origin, reason)
            events.append(event)
            working[fact_id] = self._apply(current, event)
        windows = self._changed_windows(base, working)
        plan = FactPlan(
            plan_id,
            tuple(events),
            {fact_id: version for fact_id, version in dependencies.items() if fact_id in base},
            dict(windows),
        )
        self._validate_event_order((*base_events, *plan.events), error_type=FactValidationError)
        try:
            self._state_from(self._storage_order((*base_events, *plan.events)))
        except FactLedgerCorruptionError as exc:
            raise FactValidationError(str(exc)) from exc
        self._validate_successor_graph(working, error_type=FactValidationError)
        return plan

    def commit(self, plan: FactPlan) -> tuple[FactEvent, ...]:
        self._validate_plan(plan)
        actor, origin, reason = self._event_attribution(plan.events)
        for _attempt in range(16):
            snapshot = self._snapshot()
            events, state = snapshot.events, snapshot.state
            applied = tuple(event for event in events if event.plan_id == plan.plan_id)
            if applied:
                return self._match_events(applied, plan.events)
            self._validate_plan_dependencies(plan, state)
            for event in plan.events:
                if event.op == "create" and event.fact_id in state:
                    raise FactConflictError(f"fact was created since plan: {event.fact_id}")
            try:
                resulting = self._state_from(self._storage_order((*events, *plan.events)))
            except FactLedgerCorruptionError as exc:
                raise FactValidationError(str(exc)) from exc
            expected_windows = self._changed_windows(state, resulting)
            if set(expected_windows) != set(plan.duplicate_windows):
                raise FactValidationError("fact plan duplicate windows do not match its events")
            try:
                operations = self._publication_operations(plan, snapshot)
                self._repository.commit(
                    ChangeSet(
                        tuple(operations),
                        actor,
                        origin,
                        reason,
                        f"fact-commit:{plan.plan_id}",
                        snapshot.head,
                    )
                )
            except (IdempotencyConflictError, RevisionConflictError, _SnapshotChanged):
                continue
            except CorruptRepositoryError as exc:
                raise FactLedgerCorruptionError("fact repository drifted or is corrupt") from exc
            return plan.events
        raise FactConflictError("fact publication remained contended")

    @staticmethod
    def _match_events(
        applied: Sequence[FactEvent],
        expected: Sequence[FactEvent],
    ) -> tuple[FactEvent, ...]:
        by_id = {event.event_id: event for event in applied}
        if len(by_id) != len(applied) or set(by_id) != {event.event_id for event in expected}:
            raise FactLedgerCorruptionError("plan id has partial or altered canonical events")
        if any(_canonical(by_id[event.event_id].record) != _canonical(event.record) for event in expected):
            raise FactLedgerCorruptionError("plan id has partial or altered canonical events")
        return tuple(by_id[event.event_id] for event in expected)

    def _validate_plan(self, plan: FactPlan) -> None:
        if not isinstance(plan, FactPlan):
            raise TypeError("plan must be a FactPlan")
        try:
            plan_id = _uuid(plan.plan_id, "plan_id")
        except FactValidationError:
            raise
        if type(plan.events) is not tuple or not plan.events:
            raise FactValidationError("fact plan events must be a non-empty tuple")
        decoded: list[FactEvent] = []
        for event in plan.events:
            if not isinstance(event, FactEvent):
                raise FactValidationError("fact plan contains an invalid event")
            try:
                stored = self._decode_event(event.record)
            except FactLedgerCorruptionError as exc:
                raise FactValidationError(str(exc)) from exc
            if stored != event or stored.plan_id != plan_id:
                raise FactValidationError("fact plan event does not match its canonical record")
            decoded.append(stored)
        if len({event.event_id for event in decoded}) != len(decoded):
            raise FactValidationError("fact plan contains duplicate event ids")
        try:
            self._event_attribution(decoded)
        except FactLedgerCorruptionError as exc:
            raise FactValidationError(str(exc)) from exc
        self._validate_event_order(decoded, error_type=FactValidationError)

        created: set[str] = set()
        required_dependencies: set[str] = set()
        for event in decoded:
            if event.op == "create":
                created.add(event.fact_id)
            elif event.fact_id not in created:
                required_dependencies.add(event.fact_id)
            if event.op == "supersede":
                successor_id = event.record["payload"]["successor_id"]
                if successor_id not in created:
                    required_dependencies.add(successor_id)
        dependencies = self._plan_hashes(plan.dependencies, "dependencies")
        if set(dependencies) != required_dependencies:
            raise FactValidationError("fact plan dependencies do not match its events")
        self._plan_hashes(plan.duplicate_windows, "duplicate_windows")

    @staticmethod
    def _plan_hashes(value: object, field: str) -> dict[str, str]:
        if not isinstance(value, Mapping):
            raise FactValidationError(f"fact plan {field} must be a mapping")
        result: dict[str, str] = {}
        for key, item in value.items():
            try:
                clean_key = FactLedger._window_key_value(key) if field == "duplicate_windows" else _text(key, field)
            except FactLedgerCorruptionError as exc:
                raise FactValidationError(str(exc)) from exc
            if not isinstance(item, str) or not re.fullmatch(r"[0-9a-f]{64}", item):
                raise FactValidationError(f"invalid fact plan {field} digest")
            result[clean_key] = item
        return result

    def _create_event(
        self,
        change: Mapping[str, Any],
        plan_id: str,
        point: datetime,
        state: Mapping[str, Fact],
        actor: str,
        origin: str,
        plan_reason: str,
    ) -> tuple[FactEvent, Fact]:
        allowed = {
            "op",
            "fact_id",
            "text",
            "kind",
            "labels",
            "subjects",
            "scope",
            "lifecycle",
            "certainty",
            "evidence_class",
            "sources",
            "review_at",
            "review_basis",
            "expires_at",
            "supersedes",
            "reason",
            "occurred_at",
        }
        self._unknown(change, allowed)
        supplied_id = change.get("fact_id")
        fact_id = str(uuid.uuid4()) if supplied_id is None else _text(supplied_id, "fact_id")
        if fact_id in state:
            raise FactValidationError("fact_id already exists")
        text = _text(change.get("text"), "text")
        kind = _text(change.get("kind"), "kind")
        labels = _strings(change.get("labels", ()), "labels")
        subjects = _strings(change.get("subjects"), "subjects", required=True)
        scope = _scope(change.get("scope"))
        lifecycle = change.get("lifecycle", "durable")
        certainty = change.get("certainty", "confirmed")
        evidence = change.get("evidence_class", "direct")
        if lifecycle not in _LIFECYCLES:
            raise FactValidationError("invalid lifecycle")
        if certainty not in _CERTAINTIES:
            raise FactValidationError("invalid certainty")
        if evidence not in _EVIDENCE:
            raise FactValidationError("invalid evidence_class")
        expires_at = _optional_time(change.get("expires_at"), "expires_at")
        if lifecycle == "durable" and expires_at is not None:
            raise FactValidationError("durable facts must not have expires_at")
        review_at, review_basis = self._review_fields(
            change.get("review_at"),
            change.get("review_basis"),
            fallback=lifecycle == "temporary" and expires_at is None,
            fallback_from=point,
        )
        payload: dict[str, Any] = {
            "text": text,
            "normalized_text": _normalized(text),
            "kind": kind,
            "labels": list(labels),
            "subjects": list(subjects),
            "scope": scope,
            "lifecycle": lifecycle,
            "status": "active",
            "certainty": certainty,
            "evidence_class": evidence,
            "sources": list(_sources(change.get("sources"))),
            "review_at": review_at,
            "review_basis": review_basis,
            "expires_at": expires_at,
        }
        event = self._event(plan_id, fact_id, "create", point, payload, actor, origin, plan_reason)
        return event, self._fact_from_create(event)

    def _change_payload(
        self,
        op: str,
        change: Mapping[str, Any],
        current: Fact,
        state: Mapping[str, Fact],
        dependencies: dict[str, str],
        point: datetime,
    ) -> dict[str, Any]:
        common = {"op", "fact_id", "occurred_at"}
        if op == "review":
            self._unknown(change, common | {"reason", "review_at", "review_basis", "sources", "certainty"})
            reason = _text(change.get("reason"), "reason")
            review_at, review_basis = self._review_fields(
                change.get("review_at"),
                change.get("review_basis"),
                fallback=current.lifecycle == "temporary" and current.expires_at is None,
                fallback_from=point,
            )
            payload: dict[str, Any] = {
                "reason": reason,
                "review_at": review_at,
                "review_basis": review_basis,
            }
            if "sources" in change:
                payload["sources"] = list(_sources(change["sources"]))
            if "certainty" in change:
                if change["certainty"] not in _CERTAINTIES:
                    raise FactValidationError("invalid certainty")
                payload["certainty"] = change["certainty"]
            return payload
        if op == "amend":
            allowed = {
                "kind",
                "labels",
                "subjects",
                "scope",
                "lifecycle",
                "evidence_class",
                "sources",
            }
            self._unknown(change, common | allowed)
            payload = self._metadata_payload(change)
            if "scope" in change:
                payload["scope"] = _scope(change["scope"])
            if "evidence_class" in change:
                evidence_class = change["evidence_class"]
                if evidence_class not in _EVIDENCE:
                    raise FactValidationError("invalid evidence_class")
                payload["evidence_class"] = evidence_class
            if "lifecycle" in change:
                lifecycle = change["lifecycle"]
                if lifecycle not in _LIFECYCLES:
                    raise FactValidationError("invalid lifecycle")
                payload["lifecycle"] = lifecycle
                if lifecycle == "durable":
                    payload.update(review_at=None, review_basis=None, expires_at=None)
                elif current.lifecycle == "durable":
                    payload.update(
                        review_at=_time(point + _FALLBACK_REVIEW),
                        review_basis="fallback",
                        expires_at=None,
                    )
                else:
                    payload.update(
                        review_at=None if current.review_at is None else _time(current.review_at),
                        review_basis=current.review_basis,
                        expires_at=None if current.expires_at is None else _time(current.expires_at),
                    )
            if not payload:
                raise FactValidationError("amend must change metadata")
            return payload
        if op == "supersede":
            self._unknown(change, common | {"successor_id", "reason", "sources"})
            successor_id = _text(change.get("successor_id"), "successor_id")
            if successor_id not in state:
                raise FactValidationError("successor must exist")
            successor = state[successor_id]
            dependencies.setdefault(successor_id, successor.version)
            if current.evidence_class == "direct" and successor.evidence_class == "inferred":
                raise FactValidationError("inferred evidence may not supersede direct evidence")
            self._would_cycle(state, current.fact_id, successor_id)
            payload: dict[str, Any] = {
                "successor_id": successor_id,
                "reason": _text(change.get("reason"), "reason"),
            }
        else:
            self._unknown(change, common | {"reason", "sources"})
            payload = {"reason": _text(change.get("reason"), "reason")}
        if "sources" in change:
            payload["sources"] = list(_sources(change["sources"]))
        return payload

    def _metadata_payload(
        self,
        change: Mapping[str, Any],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if "kind" in change:
            payload["kind"] = _text(change["kind"], "kind")
        if "labels" in change:
            payload["labels"] = list(_strings(change["labels"], "labels"))
        if "subjects" in change:
            payload["subjects"] = list(_strings(change["subjects"], "subjects", required=True))
        if "sources" in change:
            payload["sources"] = list(_sources(change["sources"]))
        return payload

    @staticmethod
    def _review_fields(
        review_at: object,
        review_basis: object,
        *,
        fallback: bool,
        fallback_from: datetime,
    ) -> tuple[str | None, str | None]:
        if review_at is None:
            if review_basis is not None:
                raise FactValidationError("review_basis requires review_at")
            if fallback:
                return _time(fallback_from + _FALLBACK_REVIEW), "fallback"
            return None, None
        if review_basis not in _REVIEW_BASES:
            raise FactValidationError("review_at requires a valid review_basis")
        return _time(_parse_time(review_at, "review_at")), str(review_basis)

    def _publication_operations(
        self,
        plan: FactPlan,
        snapshot: _FactSnapshot,
    ) -> list[Create | Update]:
        resources = snapshot.resources
        by_month: dict[str, list[FactEvent]] = {}
        for event in plan.events:
            by_month.setdefault(event.occurred_at.strftime("%Y-%m.jsonl"), []).append(event)
        operations: list[Create | Update] = []
        if snapshot.head is None:
            operations.append(Create(_MARKER_RESOURCE_ID, _MARKER_PATH, _MARKER_CONTENT))
        for month, events in sorted(by_month.items()):
            suffix = "".join(_canonical(event.record) + "\n" for event in events).encode()
            resource = resources.get(month)
            if resource is None:
                operations.append(Create(month, month, suffix))
            else:
                prior = snapshot.contents[month]
                self._parse_event_file(month, prior)
                operations.append(Update(resource.resource_id, resource.version_id, prior + suffix))
        return operations

    def _snapshot(self) -> _FactSnapshot:
        last_corruption: FactLedgerCorruptionError | None = None
        for _attempt in range(16):
            start = self._head()
            try:
                result = self._snapshot_once(start)
            except _SnapshotChanged:
                continue
            except FactLedgerCorruptionError as exc:
                if self._head() != start:
                    continue
                last_corruption = exc
                continue
            if self._head() == start:
                return result
        if last_corruption is not None:
            raise last_corruption
        raise FactConflictError("could not obtain a stable fact snapshot")

    def _snapshot_once(self, head: str | None) -> _FactSnapshot:
        resources = self._resources(at=head)
        if head is None:
            if resources:
                raise FactLedgerCorruptionError("empty fact repository has managed resources")
            return _FactSnapshot(None, (), {}, {}, {})
        marker = resources.get(_MARKER_PATH)
        if marker is None:
            raise FactLedgerCorruptionError("fact repository has no schema marker")
        events: list[FactEvent] = []
        contents: dict[str, bytes] = {}
        for path, resource in sorted(resources.items()):
            content = self._read_materialized(resource.resource_id, path, at=head)
            contents[path] = content
            if path == _MARKER_PATH:
                if resource.resource_id != _MARKER_RESOURCE_ID or content != _MARKER_CONTENT:
                    raise FactLedgerCorruptionError("invalid fact schema marker")
                try:
                    history = self._repository.history(resource_id=resource.resource_id, start=head)
                except (CorruptRepositoryError, KeyError) as exc:
                    raise FactLedgerCorruptionError("fact schema marker history is corrupt") from exc
                if len(history) != 1:
                    raise FactLedgerCorruptionError("fact schema marker was changed")
            elif _MONTH.fullmatch(path):
                if resource.resource_id != path:
                    raise FactLedgerCorruptionError("monthly fact resource identity mismatch")
                events.extend(self._parse_event_file(path, content))
                self._assert_append_only(resource.resource_id, head)
            else:
                raise FactLedgerCorruptionError(f"unexpected fact resource: {path}")
        ids = [event.event_id for event in events]
        if len(ids) != len(set(ids)):
            raise FactLedgerCorruptionError("duplicate event id")
        try:
            state = self._state_from(events)
        except FactValidationError as exc:
            raise FactLedgerCorruptionError(str(exc)) from exc
        return _FactSnapshot(head, tuple(events), state, resources, contents)

    def _assert_append_only(self, resource_id: str, head: str | None) -> None:
        try:
            commits = tuple(
                reversed(
                    self._repository.history(
                        resource_id=resource_id,
                        start=head,
                    )
                )
            )
            versions = [self._repository.read(resource_id, at=commit.commit_id) for commit in commits]
        except (CorruptRepositoryError, KeyError) as exc:
            raise FactLedgerCorruptionError("fact record history is corrupt") from exc
        for before, after in pairwise(versions):
            if not after.startswith(before):
                raise FactLedgerCorruptionError("monthly fact record lost an append-only prefix")

    def _resources(self, *, at: str | None = None) -> dict[str, Any]:
        try:
            resources = self._repository.list_resources(at=at, include_archived=True)
        except CorruptRepositoryError as exc:
            raise FactLedgerCorruptionError("fact repository is corrupt") from exc
        result = {}
        for resource in resources:
            if resource.state is not ResourceState.ACTIVE:
                raise FactLedgerCorruptionError("active resource has an invalid state")
            if resource.path in result:
                raise FactLedgerCorruptionError("duplicate fact resource path")
            result[resource.path] = resource
        return result

    def _head(self) -> str | None:
        try:
            return self._repository.head
        except CorruptRepositoryError as exc:
            raise FactLedgerCorruptionError("fact repository is corrupt") from exc

    def _read_materialized(
        self,
        resource_id: str,
        path: str,
        *,
        at: str | None = None,
    ) -> bytes:
        recorded = self._repository.read(resource_id, at=at)
        if at is not None and self._head() != at:
            raise _SnapshotChanged
        try:
            materialized = (self.records_root / path).read_bytes()
        except OSError as exc:
            raise FactLedgerCorruptionError("fact resource was removed externally") from exc
        if recorded != materialized:
            raise FactLedgerCorruptionError("fact resource drifted externally")
        if at is not None and self._head() != at:
            raise _SnapshotChanged
        return recorded

    def _parse_event_file(self, path: str, content: bytes) -> tuple[FactEvent, ...]:
        text = self._utf8_jsonl(content, path)
        events = []
        for line in text.splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                raise FactLedgerCorruptionError(f"malformed JSON in {path}") from exc
            if not isinstance(record, dict) or _canonical(record) != line:
                raise FactLedgerCorruptionError(f"non-canonical JSON in {path}")
            event = self._decode_event(record)
            if event.occurred_at.strftime("%Y-%m.jsonl") != path:
                raise FactLedgerCorruptionError("event is in the wrong monthly file")
            events.append(event)
        return tuple(events)

    def _decode_event(self, record: object) -> FactEvent:
        expected = {
            "schema_version",
            "event_id",
            "plan_id",
            "fact_id",
            "op",
            "occurred_at",
            "actor",
            "origin",
            "plan_reason",
            "payload",
        }
        if not isinstance(record, Mapping) or set(record) != expected:
            raise FactLedgerCorruptionError("unknown event fields")
        try:
            if type(record["schema_version"]) is not int or record["schema_version"] != _VERSION:
                raise FactValidationError("unknown event schema version")
            event_id = _uuid(record["event_id"], "event_id")
            plan_id = _uuid(record["plan_id"], "plan_id")
            fact_id = _text(record["fact_id"], "fact_id")
            op = record["op"]
            if op not in _OPS or not isinstance(record["payload"], Mapping):
                raise FactValidationError("invalid event operation")
            occurred_at = _parse_time(record["occurred_at"], "occurred_at")
            actor = _text(record["actor"], "actor")
            origin = _text(record["origin"], "origin")
            plan_reason = _text(record["plan_reason"], "plan_reason")
            self._validate_payload(str(op), record["payload"])
        except FactValidationError as exc:
            raise FactLedgerCorruptionError(str(exc)) from exc
        return FactEvent(event_id, plan_id, fact_id, str(op), occurred_at, actor, origin, plan_reason, dict(record))

    def _validate_payload(self, op: str, payload: Mapping[str, Any]) -> None:
        if op == "create":
            expected = {
                "text",
                "normalized_text",
                "kind",
                "labels",
                "subjects",
                "scope",
                "lifecycle",
                "status",
                "certainty",
                "evidence_class",
                "sources",
                "review_at",
                "review_basis",
                "expires_at",
            }
            if set(payload) != expected:
                raise FactValidationError("invalid create payload")
            text = _text(payload["text"], "text")
            if payload["normalized_text"] != _normalized(text) or payload["status"] != "active":
                raise FactValidationError("invalid initial fact state")
            _text(payload["kind"], "kind")
            _strings(payload["labels"], "labels")
            _strings(payload["subjects"], "subjects", required=True)
            _scope(payload["scope"])
            _sources(payload["sources"])
            if payload["lifecycle"] not in _LIFECYCLES:
                raise FactValidationError("invalid lifecycle")
            if payload["certainty"] not in _CERTAINTIES:
                raise FactValidationError("invalid certainty")
            if payload["evidence_class"] not in _EVIDENCE:
                raise FactValidationError("invalid evidence_class")
            self._validate_review_pair(payload["review_at"], payload["review_basis"])
            if payload["expires_at"] is not None:
                _parse_time(payload["expires_at"], "expires_at")
            if payload["lifecycle"] == "durable" and payload["expires_at"] is not None:
                raise FactValidationError("durable facts must not have expires_at")
            if payload["lifecycle"] == "temporary" and payload["expires_at"] is None and payload["review_at"] is None:
                raise FactValidationError("temporary fact needs fallback review")
            if payload["expires_at"] is not None and payload["review_basis"] == "fallback":
                raise FactValidationError("explicit expiry must not use fallback review")
            return
        allowed = {
            "review": {"reason", "review_at", "review_basis", "sources", "certainty"},
            "amend": {
                "kind",
                "labels",
                "subjects",
                "scope",
                "lifecycle",
                "evidence_class",
                "sources",
                "review_at",
                "review_basis",
                "expires_at",
            },
            "supersede": {"successor_id", "reason", "sources"},
            "expire": {"reason", "sources"},
            "retract": {"reason", "sources"},
        }[op]
        required = {
            "review": {"reason", "review_at", "review_basis"},
            "amend": set(),
            "supersede": {"successor_id", "reason"},
            "expire": {"reason"},
            "retract": {"reason"},
        }[op]
        if not required <= set(payload) or set(payload) - allowed:
            raise FactValidationError("invalid event payload")
        if op == "amend" and not payload:
            raise FactValidationError("empty amendment")
        if "reason" in payload:
            _text(payload["reason"], "reason")
        if "successor_id" in payload:
            _text(payload["successor_id"], "successor_id")
        if "kind" in payload:
            _text(payload["kind"], "kind")
        if "labels" in payload:
            _strings(payload["labels"], "labels")
        if "subjects" in payload:
            _strings(payload["subjects"], "subjects", required=True)
        if "scope" in payload:
            _scope(payload["scope"])
        if op == "amend":
            if "lifecycle" in payload:
                if payload["lifecycle"] not in _LIFECYCLES:
                    raise FactValidationError("invalid lifecycle")
                if not {"review_at", "review_basis", "expires_at"} <= set(payload):
                    raise FactValidationError("lifecycle amendment requires complete timing fields")
                self._validate_review_pair(payload["review_at"], payload["review_basis"])
                if payload["expires_at"] is not None:
                    _parse_time(payload["expires_at"], "expires_at")
                if payload["lifecycle"] == "durable" and (
                    payload["review_at"] is not None or payload["expires_at"] is not None
                ):
                    raise FactValidationError("durable facts must not have review or expiry dates")
                if (
                    payload["lifecycle"] == "temporary"
                    and payload["review_at"] is None
                    and payload["expires_at"] is None
                ):
                    raise FactValidationError("temporary facts require review or expiry")
            elif {"review_at", "review_basis", "expires_at"} & set(payload):
                raise FactValidationError("fact timing fields require lifecycle")
        if "evidence_class" in payload and payload["evidence_class"] not in _EVIDENCE:
            raise FactValidationError("invalid evidence_class")
        if "sources" in payload:
            _sources(payload["sources"])
        if "certainty" in payload and payload["certainty"] not in _CERTAINTIES:
            raise FactValidationError("invalid certainty")
        if "review_at" in payload or "review_basis" in payload:
            if not {"review_at", "review_basis"} <= set(payload):
                raise FactValidationError("review fields must appear together")
            self._validate_review_pair(payload["review_at"], payload["review_basis"])

    @staticmethod
    def _validate_review_pair(review_at: object, review_basis: object) -> None:
        if review_at is None:
            if review_basis is not None:
                raise FactValidationError("review_basis requires review_at")
            return
        _parse_time(review_at, "review_at")
        if review_basis not in _REVIEW_BASES:
            raise FactValidationError("invalid review_basis")

    def _state_from(self, events: Sequence[FactEvent]) -> dict[str, Fact]:
        self._validate_event_order(events, error_type=FactLedgerCorruptionError)
        state: dict[str, Fact] = {}
        for event in events:
            if event.op == "create":
                if event.fact_id in state:
                    raise FactLedgerCorruptionError("fact created more than once")
                state[event.fact_id] = self._fact_from_create(event)
                continue
            current = state.get(event.fact_id)
            if current is None:
                raise FactLedgerCorruptionError("event references an unknown fact")
            if current.status in _TERMINAL:
                raise FactLedgerCorruptionError("terminal fact has a later event")
            payload = event.record["payload"]
            if event.op == "supersede":
                successor_id = payload["successor_id"]
                successor = state.get(successor_id)
                if successor is None:
                    raise FactLedgerCorruptionError("successor must be created first")
                if current.evidence_class == "direct" and successor.evidence_class == "inferred":
                    raise FactLedgerCorruptionError("inferred fact superseded direct fact")
                self._would_cycle(state, current.fact_id, successor_id)
            state[event.fact_id] = self._apply(current, event)
        self._validate_successor_graph(state, error_type=FactLedgerCorruptionError)
        return state

    @staticmethod
    def _validate_event_order(
        events: Sequence[FactEvent],
        *,
        error_type: type[FactValidationError] | type[FactLedgerCorruptionError],
    ) -> None:
        latest: dict[str, datetime] = {}
        for event in events:
            previous = latest.get(event.fact_id)
            if previous is not None and event.occurred_at < previous:
                raise error_type(f"fact event time moved backwards: {event.fact_id}")
            latest[event.fact_id] = event.occurred_at

    @staticmethod
    def _storage_order(events: Sequence[FactEvent]) -> tuple[FactEvent, ...]:
        """Mirror the order produced by monthly append-only event files."""
        return tuple(sorted(events, key=lambda event: event.occurred_at.strftime("%Y-%m.jsonl")))

    def _fact_from_create(self, event: FactEvent) -> Fact:
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
            review_at=self._as_time(payload["review_at"], "review_at"),
            review_basis=payload["review_basis"],
            expires_at=self._as_time(payload["expires_at"], "expires_at"),
            successor_id=None,
            version=_chain_version("", event.record),
        )

    def _apply(self, current: Fact, event: FactEvent) -> Fact:
        payload = event.record["payload"]
        changes: dict[str, Any] = {"version": _chain_version(current.version, event.record)}
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
                    changes[key] = self._as_time(payload[key], key)
        if event.op == "supersede":
            changes.update(status="superseded", successor_id=payload["successor_id"])
        elif event.op == "expire":
            changes["status"] = "expired"
        elif event.op == "retract":
            changes["status"] = "retracted"
        return self._replace(current, **changes)

    def _validate_plan_dependencies(self, plan: FactPlan, state: Mapping[str, Fact]) -> None:
        for fact_id, version in plan.dependencies.items():
            if fact_id not in state or state[fact_id].version != version:
                raise FactConflictError(f"fact changed since plan: {fact_id}")
        for key, digest in plan.duplicate_windows.items():
            scope_kind, scope_key, text = self._window_parts(key)
            if self._duplicate_digest(state, scope_kind, scope_key, text) != digest:
                raise FactConflictError("same-scope exact-text duplicate window changed")

    @staticmethod
    def _duplicate_digest(
        state: Mapping[str, Fact],
        scope_kind: str,
        scope_key: str | None,
        normalized_text: str,
    ) -> str:
        return _digest(
            sorted(
                fact.fact_id
                for fact in state.values()
                if fact.status == "active"
                and fact.scope == {"kind": scope_kind, "key": scope_key}
                and fact.normalized_text == normalized_text
            )
        )

    def _changed_windows(
        self,
        before: Mapping[str, Fact],
        after: Mapping[str, Fact],
    ) -> dict[str, str]:
        keys = {self._fact_window_key(fact) for fact in (*before.values(), *after.values()) if fact.status == "active"}
        return {
            key: self._duplicate_digest(before, *self._window_parts(key))
            for key in keys
            if self._duplicate_digest(before, *self._window_parts(key))
            != self._duplicate_digest(after, *self._window_parts(key))
        }

    @staticmethod
    def _fact_window_key(fact: Fact) -> str:
        return _canonical([fact.scope["kind"], fact.scope["key"], fact.normalized_text])

    @staticmethod
    def _validate_successor_graph(
        state: Mapping[str, Fact],
        *,
        error_type: type[FactValidationError] | type[FactLedgerCorruptionError],
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

    @staticmethod
    def _active(
        state: Mapping[str, Fact],
        fact_id: object,
        dependencies: dict[str, str],
    ) -> Fact:
        fact_id = _text(fact_id, "fact_id")
        try:
            fact = state[fact_id]
        except KeyError as exc:
            raise FactValidationError("unknown fact") from exc
        if fact.status != "active":
            raise FactValidationError("fact is terminal")
        dependencies.setdefault(fact_id, fact.version)
        return fact

    @staticmethod
    def _would_cycle(state: Mapping[str, Fact], old_id: str, successor_id: str) -> None:
        seen = {old_id}
        cursor: str | None = successor_id
        while cursor is not None:
            if cursor in seen:
                raise FactValidationError("supersession cycle")
            seen.add(cursor)
            cursor = state[cursor].successor_id

    @staticmethod
    def _replace(fact: Fact, **changes: Any) -> Fact:
        values = {field: getattr(fact, field) for field in Fact.__dataclass_fields__}
        values.update(changes)
        return Fact(**values)

    def _event(
        self,
        plan_id: str,
        fact_id: str,
        op: str,
        point: datetime,
        payload: Mapping[str, Any],
        actor: str,
        origin: str,
        plan_reason: str,
    ) -> FactEvent:
        record = {
            "schema_version": _VERSION,
            "event_id": str(uuid.uuid4()),
            "plan_id": plan_id,
            "fact_id": fact_id,
            "op": op,
            "occurred_at": _time(point),
            "actor": actor,
            "origin": origin,
            "plan_reason": plan_reason,
            "payload": dict(payload),
        }
        return FactEvent(record["event_id"], plan_id, fact_id, op, point, actor, origin, plan_reason, record)

    @staticmethod
    def _event_attribution(events: Sequence[FactEvent]) -> tuple[str, str, str]:
        if not events:
            raise FactLedgerCorruptionError("fact events must not be empty")
        attribution = (events[0].actor, events[0].origin, events[0].plan_reason)
        if any((event.actor, event.origin, event.plan_reason) != attribution for event in events):
            raise FactLedgerCorruptionError("fact plan has inconsistent event attribution")
        return attribution

    @staticmethod
    def _unknown(value: Mapping[str, Any], allowed: set[str]) -> None:
        if set(value) - allowed:
            raise FactValidationError("unknown change fields")

    @staticmethod
    def _utf8_jsonl(content: bytes, path: str) -> str:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FactLedgerCorruptionError(f"{path} is not UTF-8") from exc
        if not text or not text.endswith("\n"):
            raise FactLedgerCorruptionError(f"{path} is not canonical JSONL")
        return text

    @staticmethod
    def _window_key_value(value: object) -> str:
        if not isinstance(value, str):
            raise FactLedgerCorruptionError("invalid duplicate-window key")
        try:
            parts = json.loads(value)
            if not isinstance(parts, list) or len(parts) != 3 or _canonical(parts) != value:
                raise FactValidationError("invalid duplicate-window key")
            _scope({"kind": parts[0], "key": parts[1]})
            _text(parts[2], "duplicate-window component")
        except FactValidationError as exc:
            raise FactLedgerCorruptionError(str(exc)) from exc
        except json.JSONDecodeError as exc:
            raise FactLedgerCorruptionError("invalid duplicate-window key") from exc
        return value

    @staticmethod
    def _window_parts(value: str) -> tuple[str, str | None, str]:
        FactLedger._window_key_value(value)
        kind, key, text = json.loads(value)
        return kind, key, text

    @staticmethod
    def _as_time(value: object, field: str) -> datetime | None:
        return None if value is None else _parse_time(value, field)

    def _now(self) -> datetime:
        return self._utc(self._clock())

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise FactValidationError("clock must return an aware datetime")
        return value.astimezone(UTC)
