"""Strict append-only facts backed by the crash-safe managed-file repository."""

import re
import uuid
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from types import MappingProxyType
from typing import Any

from arden.memory.facts.event_codec import (
    CERTAINTIES,
    EVIDENCE,
    LIFECYCLES,
    MONTH,
    OPS,
    REVIEW_BASES,
    canonical,
    decode_event,
    fact_version,
    make_event,
    normalized,
    optional_time,
    parse_event_file,
    parse_time,
    scope,
    sources,
    strings,
    text,
    time_value,
    window_key_value,
    window_parts,
)
from arden.memory.facts.models import (
    DueReviewCandidate,
    Fact,
    FactChangeFeed,
    FactConflictError,
    FactEvent,
    FactLedgerCorruptionError,
    FactPlan,
    FactValidationError,
)
from arden.memory.facts.state import (
    MAINTENANCE_ORIGIN,
    RETENTION_ORIGIN,
    active,
    apply,
    changed_windows,
    event_attribution,
    fact_from_create,
    review_window,
    review_window_facts,
    state_from,
    storage_order,
    validate_event_order,
    validate_plan,
    validate_plan_dependencies,
    validate_retention_plan_change,
    validate_retention_review_windows,
    validate_successor_graph,
    would_cycle,
)
from arden.revisions.errors import (
    CorruptRepositoryError,
    IdempotencyConflictError,
    RevisionConflictError,
)
from arden.revisions.models import ChangeSet, CollectionReport, Commit, Create, ResourceState, Update
from arden.revisions.repository import ManagedFileRepository

_MARKER_PATH = ".ledger.json"
_MARKER_RESOURCE_ID = "fact-ledger-schema"
_MARKER_CONTENT = b'{"schema_version":1}\n'
_FALLBACK_REVIEW = timedelta(days=90)


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


def _readonly_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _readonly_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_readonly_json(item) for item in value)
    return value


def _readonly_fact(fact: Fact) -> Fact:
    return replace(
        fact,
        scope=MappingProxyType(dict(fact.scope)),
        sources=tuple(_readonly_json(source) for source in fact.sources),
    )


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

    @property
    def revision(self) -> str | None:
        """Current validated canonical ledger head."""

        return self._snapshot().head

    def collect_history(self) -> CollectionReport:
        """Collect unreachable fact-history objects using the repository grace period."""

        return self._repository.collect()

    def get(self, fact_id: str) -> Fact:
        try:
            return self._snapshot().state[fact_id]
        except KeyError as exc:
            raise KeyError(f"unknown fact: {fact_id}") from exc

    def facts_at(self, revision: str | None) -> Mapping[str, Fact]:
        """Return the immutable fact state at one reachable revision.

        ``None`` is the empty, pre-ledger revision; it is not an alias for the
        current head.  Non-empty revisions are replayed only from retained
        canonical history, so a caller cannot accidentally combine an older
        feed with the live fact state.
        """

        self._snapshot()
        if revision is None:
            return MappingProxyType({})
        if not isinstance(revision, str) or not revision:
            raise FactValidationError("revision must be a non-empty revision or null")
        feed = self._change_feed_at(None, revision)
        state = state_from(storage_order(feed.events))
        return MappingProxyType({fact_id: _readonly_fact(fact) for fact_id, fact in state.items()})

    @contextmanager
    def locked_facts(self) -> Iterator[Mapping[str, Fact]]:
        """Hold the fact repository lock around one current read-only snapshot."""

        with self._repository._storage.locked():
            snapshot = self._snapshot()
            yield MappingProxyType({fact_id: _readonly_fact(fact) for fact_id, fact in snapshot.state.items()})

    def get_version(self, fact_id: str, version: str) -> Fact:
        """Return one exact historical fact version from its immutable chain."""

        fact_id = text(fact_id, "fact_id")
        if not isinstance(version, str) or re.fullmatch(r"[0-9a-f]{64}", version) is None:
            raise FactValidationError("version must be a SHA-256 digest")
        current: Fact | None = None
        for event in self._snapshot().events:
            if event.fact_id != fact_id:
                continue
            if event.op == "create":
                current = fact_from_create(event)
            else:
                if current is None:
                    raise FactLedgerCorruptionError("historical event precedes fact creation")
                current = apply(current, event)
            if current.version == version:
                return current
        raise KeyError(f"unknown fact version: {fact_id}@{version}")

    def search(
        self,
        query: str | None = None,
        *,
        scope_kind: str | None = None,
        scope_key: str | None = None,
        subject: str | None = None,
        include_inactive: bool = False,
    ) -> tuple[Fact, ...]:
        needle = None if query is None else normalized(text(query, "query"))
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

    def changes_since(self, watermark: str | None) -> FactChangeFeed:
        """Return one stable append-only delta after a reachable fact revision."""

        if watermark is not None and (not isinstance(watermark, str) or not watermark):
            raise FactValidationError("watermark must be a non-empty revision or null")
        snapshot = self._snapshot()
        if snapshot.head is None:
            if watermark is not None:
                raise FactValidationError("watermark is not a reachable fact revision")
            return FactChangeFeed(None, None, (), (), ())
        return self._change_feed_at(watermark, snapshot.head)

    def changes_between(self, watermark: str | None, through_revision: str) -> FactChangeFeed:
        """Rebuild one exact retained delta without extending it to the live head."""

        if not isinstance(through_revision, str) or not through_revision:
            raise FactValidationError("through_revision must be a non-empty revision")
        self._snapshot()
        return self._change_feed_at(watermark, through_revision)

    def validate_change_feed(self, feed: FactChangeFeed) -> None:
        """Prove that a retained feed exactly matches reachable canonical history."""

        if not isinstance(feed, FactChangeFeed):
            raise TypeError("feed must be a FactChangeFeed")
        self._snapshot()
        if feed.through_revision is None:
            canonical = FactChangeFeed(None, None, (), (), ())
        else:
            canonical = self._change_feed_at(feed.from_revision, feed.through_revision)
        if feed != canonical:
            raise FactValidationError("fact change feed does not match canonical history")

    def _change_feed_at(self, watermark: str | None, through: str) -> FactChangeFeed:
        if watermark is not None and (not isinstance(watermark, str) or not watermark):
            raise FactValidationError("watermark must be a non-empty revision or null")
        try:
            commits = tuple(reversed(self._repository.history(start=through)))
        except KeyError as exc:
            raise FactValidationError("through revision is not reachable from canonical facts") from exc
        except CorruptRepositoryError as exc:
            raise FactLedgerCorruptionError("fact revision history is corrupt") from exc
        started = watermark is None
        all_events: list[FactEvent] = []
        events: list[FactEvent] = []
        for commit in commits:
            added = self._events_added_by_commit(commit)
            all_events.extend(added)
            if not started:
                if commit.commit_id == watermark:
                    started = True
                continue
            events.extend(added)
        if not started:
            raise FactValidationError("watermark is not a reachable fact revision")
        all_event_ids = [event.event_id for event in all_events]
        if len(all_event_ids) != len(set(all_event_ids)):
            raise FactLedgerCorruptionError("duplicate event id in fact revision history")
        event_ids = {event.event_id for event in events}
        state = state_from(storage_order(all_events))
        prior_events = tuple(event for event in all_events if event.event_id not in event_ids)
        prior = state_from(storage_order(prior_events))
        fact_ids = tuple(dict.fromkeys(event.fact_id for event in events))
        working = dict(prior)
        routes: set[tuple[str, str | None, str]] = set()
        for event in storage_order(events):
            before = working.get(event.fact_id)
            if before is not None:
                routes.update((str(before.scope["kind"]), before.scope["key"], subject) for subject in before.subjects)
            if event.op == "create":
                after = fact_from_create(event)
            else:
                if before is None:
                    raise FactLedgerCorruptionError("delta event references an unknown fact")
                after = apply(before, event)
            working[event.fact_id] = after
            routes.update((str(after.scope["kind"]), after.scope["key"], subject) for subject in after.subjects)
        if any(working[fact_id].version != state[fact_id].version for fact_id in fact_ids):
            raise FactLedgerCorruptionError("fact delta does not reproduce canonical state")
        affected_routes = tuple(sorted(routes, key=lambda item: (item[0], item[1] or "", item[2])))
        return FactChangeFeed(watermark, through, tuple(events), fact_ids, affected_routes)

    def known_scopes(self) -> frozenset[tuple[str, str | None]]:
        """Return every scope recorded in canonical fact history."""

        scopes = set()
        for event in self._snapshot().events:
            payload = event.record["payload"]
            if event.op != "create" and "scope" not in payload:
                continue
            scope_value = scope(payload["scope"])
            scopes.add((scope_value["kind"], scope_value["key"]))
        return frozenset(scopes)

    def validate_initialized(self) -> str:
        """Fully validate an initialized ledger and return its current head."""

        head = self.revision
        if head is None:
            raise FactLedgerCorruptionError("fact ledger is not initialized")
        return head

    def due_reviews(self, *, now: datetime | None = None) -> tuple[DueReviewCandidate, ...]:
        return self.due_review_snapshot(now=now)[2]

    def due_review_snapshot(
        self,
        *,
        now: datetime | None = None,
    ) -> tuple[str | None, datetime, tuple[DueReviewCandidate, ...]]:
        """Return due reviews and their evidence from one validated state."""

        snapshot = self._snapshot()
        point = self._now() if now is None else self._utc(now)
        active = tuple(
            sorted(
                (fact for fact in snapshot.state.values() if fact.status == "active"),
                key=lambda fact: (fact.created_at, fact.fact_id),
            )
        )
        result = []
        for fact in active:
            candidates = [item for item in (fact.review_at, fact.expires_at) if item is not None]
            if not candidates:
                continue
            due_at = min(candidates)
            if due_at <= point:
                related = review_window_facts(fact, active)
                result.append(DueReviewCandidate(fact, due_at, related, review_window(fact, related)))
        due = tuple(sorted(result, key=lambda item: (item.due_at, item.fact.fact_id)))
        return snapshot.head, point, due

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
        actor = text(actor, "actor")
        origin = text(origin, "origin")
        reason = text(reason, "reason")
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
            if op not in OPS:
                raise FactValidationError("unknown fact operation")
            is_retention = origin == RETENTION_ORIGIN
            is_maintenance = origin == MAINTENANCE_ORIGIN
            has_review_window = "expected_review_window" in change
            if is_retention and op not in {"review", "supersede", "expire"}:
                raise FactValidationError("Retention may only review, supersede, or expire due temporary facts")
            if is_retention and not has_review_window:
                raise FactValidationError("Retention changes require an expected review window")
            if has_review_window and not is_retention:
                raise FactValidationError("expected_review_window is reserved for Retention")
            maintenance_merge = change.get("maintenance_merge")
            if is_maintenance:
                if op == "amend":
                    if maintenance_merge is not None:
                        raise FactValidationError("metadata maintenance must not claim a duplicate merge")
                    allowed = {
                        "op",
                        "fact_id",
                        "expected_version",
                        "kind",
                        "labels",
                        "subjects",
                        "lifecycle",
                        "evidence_class",
                    }
                    if unknown := set(change).difference(allowed):
                        raise FactValidationError(
                            f"Memory Maintenance may amend only classification metadata: {sorted(unknown)}"
                        )
                elif op != "supersede" or maintenance_merge is not True:
                    raise FactValidationError("Memory Maintenance may only amend metadata or merge duplicates")
            elif maintenance_merge is not None:
                raise FactValidationError("maintenance_merge is reserved for Memory Maintenance")
            point = plan_point
            if op == "create":
                event, fact = self._create_event(change, plan_id, point, working, actor, origin, reason)
                self._pin_fact_sources(event.record["payload"], base, dependencies)
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
                    old = active(working, old_id, dependencies)
                    if old.evidence_class == "direct" and fact.evidence_class == "inferred":
                        raise FactValidationError("inferred evidence may not supersede direct evidence")
                    supersession_reason = text(change.get("reason"), "reason")
                    event = make_event(
                        plan_id,
                        old.fact_id,
                        "supersede",
                        point,
                        {
                            "successor_id": fact.fact_id,
                            "reason": supersession_reason,
                            "sources": list(sources(fact.sources)),
                        },
                        actor,
                        origin,
                        reason,
                    )
                    self._pin_fact_sources(event.record["payload"], base, dependencies)
                    events.append(event)
                    working[old.fact_id] = apply(old, event)
                continue
            fact_id = text(change.get("fact_id"), "fact_id")
            current = active(working, fact_id, dependencies)
            if "expected_version" in change:
                expected = fact_version(change["expected_version"], "expected_version")
                if current.version != expected:
                    raise FactConflictError(f"fact changed since due review: {fact_id}")
            if is_retention:
                validate_retention_plan_change(change, current, base, plan_point, fact_version)
            if op == "amend" and "text" in change:
                raise FactValidationError("text amendments require create with supersedes")
            payload = self._change_payload(op, change, current, working, dependencies, point)
            event = make_event(plan_id, fact_id, str(op), point, payload, actor, origin, reason)
            self._pin_fact_sources(event.record["payload"], base, dependencies)
            events.append(event)
            working[fact_id] = apply(current, event)
        windows = changed_windows(base, working, window_parts)
        plan = FactPlan(
            plan_id,
            tuple(events),
            {fact_id: version for fact_id, version in dependencies.items() if fact_id in base},
            dict(windows),
        )
        validate_event_order((*base_events, *plan.events), error_type=FactValidationError)
        try:
            state_from(storage_order((*base_events, *plan.events)))
        except FactLedgerCorruptionError as exc:
            raise FactValidationError(str(exc)) from exc
        validate_successor_graph(working, error_type=FactValidationError)
        return plan

    def commit(
        self,
        plan: FactPlan,
        *,
        expected_revision: str | None = None,
    ) -> tuple[FactEvent, ...]:
        validate_plan(plan, decode_event, sources, fact_version, window_key_value)
        if expected_revision is not None and (
            not isinstance(expected_revision, str) or re.fullmatch(r"[0-9a-f]{64}", expected_revision) is None
        ):
            raise FactValidationError("expected_revision must be a lowercase SHA-256 revision")
        actor, origin, reason = event_attribution(plan.events)
        for _attempt in range(16):
            snapshot = self._snapshot()
            events, state = snapshot.events, snapshot.state
            applied = tuple(event for event in events if event.plan_id == plan.plan_id)
            if applied:
                return self._match_events(applied, plan.events)
            if expected_revision is not None and snapshot.head != expected_revision:
                raise FactConflictError("fact ledger changed before guarded publication")
            validate_plan_dependencies(plan, state, window_parts)
            validate_retention_review_windows(plan, state)
            for event in plan.events:
                if event.op == "create" and event.fact_id in state:
                    raise FactConflictError(f"fact was created since plan: {event.fact_id}")
            try:
                resulting = state_from(storage_order((*events, *plan.events)))
            except FactLedgerCorruptionError as exc:
                raise FactValidationError(str(exc)) from exc
            expected_windows = changed_windows(state, resulting, window_parts)
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

    def commit_at_revision(self, plan: FactPlan, expected_revision: str) -> tuple[FactEvent, ...]:
        """Commit once only if the complete fact ledger is still at one revision.

        An already-applied plan remains idempotently replayable after later
        commits. The global guard is used by operations whose selection depends
        on the absence of additional matching facts, not only touched versions.
        """

        return self.commit(plan, expected_revision=expected_revision)

    def plan_applied(self, plan_id: str) -> bool:
        """Return whether any canonical event belongs to an exact plan ID."""

        try:
            plan_id = str(uuid.UUID(text(plan_id, "plan_id")))
        except (FactValidationError, ValueError) as exc:
            raise FactValidationError("plan_id must be a UUID") from exc
        return any(event.plan_id == plan_id for event in self._snapshot().events)

    def validate_plan(self, plan: FactPlan) -> None:
        """Validate a retained plan without reading or mutating canonical facts."""

        validate_plan(plan, decode_event, sources, fact_version, window_key_value)

    @staticmethod
    def _match_events(
        applied: Sequence[FactEvent],
        expected: Sequence[FactEvent],
    ) -> tuple[FactEvent, ...]:
        by_id = {event.event_id: event for event in applied}
        if len(by_id) != len(applied) or set(by_id) != {event.event_id for event in expected}:
            raise FactLedgerCorruptionError("plan id has partial or altered canonical events")
        if any(canonical(by_id[event.event_id].record) != canonical(event.record) for event in expected):
            raise FactLedgerCorruptionError("plan id has partial or altered canonical events")
        return tuple(by_id[event.event_id] for event in expected)

    @classmethod
    def _pin_fact_sources(
        cls,
        payload: Mapping[str, Any],
        base: Mapping[str, Fact],
        dependencies: dict[str, str],
    ) -> None:
        if "sources" not in payload:
            return
        for source in sources(payload["sources"]):
            if source["kind"] != "fact":
                continue
            fact_id = source["ref"]
            version = fact_version(source["extra"]["version"], "source.extra.version")
            current = base.get(fact_id)
            if current is None:
                raise FactValidationError(f"fact source is not canonical: {fact_id}")
            if current.version != version:
                raise FactConflictError(f"fact evidence changed since it was read: {fact_id}")
            existing = dependencies.setdefault(fact_id, version)
            if existing != version:
                raise FactConflictError(f"fact evidence version conflicts with this plan: {fact_id}")

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
        }
        self._unknown(change, allowed)
        supplied_id = change.get("fact_id")
        fact_id = str(uuid.uuid4()) if supplied_id is None else text(supplied_id, "fact_id")
        if fact_id in state:
            raise FactValidationError("fact_id already exists")
        fact_text = text(change.get("text"), "text")
        kind = text(change.get("kind"), "kind")
        labels = strings(change.get("labels", ()), "labels")
        subjects = strings(change.get("subjects"), "subjects", required=True)
        scope_value = scope(change.get("scope"))
        lifecycle = change.get("lifecycle", "durable")
        certainty = change.get("certainty", "confirmed")
        evidence = change.get("evidence_class", "direct")
        if lifecycle not in LIFECYCLES:
            raise FactValidationError("invalid lifecycle")
        if certainty not in CERTAINTIES:
            raise FactValidationError("invalid certainty")
        if evidence not in EVIDENCE:
            raise FactValidationError("invalid evidence_class")
        expires_at = optional_time(change.get("expires_at"), "expires_at")
        if lifecycle == "durable" and expires_at is not None:
            raise FactValidationError("durable facts must not have expires_at")
        review_at, review_basis = self._review_fields(
            change.get("review_at"),
            change.get("review_basis"),
            fallback=lifecycle == "temporary" and expires_at is None,
            fallback_from=point,
        )
        payload: dict[str, Any] = {
            "text": fact_text,
            "normalized_text": normalized(fact_text),
            "kind": kind,
            "labels": list(labels),
            "subjects": list(subjects),
            "scope": scope_value,
            "lifecycle": lifecycle,
            "status": "active",
            "certainty": certainty,
            "evidence_class": evidence,
            "sources": list(sources(change.get("sources"))),
            "review_at": review_at,
            "review_basis": review_basis,
            "expires_at": expires_at,
        }
        event = make_event(plan_id, fact_id, "create", point, payload, actor, origin, plan_reason)
        return event, fact_from_create(event)

    def _change_payload(
        self,
        op: str,
        change: Mapping[str, Any],
        current: Fact,
        state: Mapping[str, Fact],
        dependencies: dict[str, str],
        point: datetime,
    ) -> dict[str, Any]:
        common = {"op", "fact_id", "expected_version", "expected_review_window"}
        if op == "review":
            self._unknown(change, common | {"reason", "review_at", "review_basis", "sources", "certainty"})
            reason = text(change.get("reason"), "reason")
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
                payload["sources"] = list(sources(change["sources"]))
            if "certainty" in change:
                if change["certainty"] not in CERTAINTIES:
                    raise FactValidationError("invalid certainty")
                payload["certainty"] = change["certainty"]
            self._copy_expected_review_window(change, payload)
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
                payload["scope"] = scope(change["scope"])
            if "evidence_class" in change:
                evidence_class = change["evidence_class"]
                if evidence_class not in EVIDENCE:
                    raise FactValidationError("invalid evidence_class")
                payload["evidence_class"] = evidence_class
            if "lifecycle" in change:
                lifecycle = change["lifecycle"]
                if lifecycle not in LIFECYCLES:
                    raise FactValidationError("invalid lifecycle")
                payload["lifecycle"] = lifecycle
                if lifecycle == "durable":
                    payload.update(review_at=None, review_basis=None, expires_at=None)
                elif current.lifecycle == "durable":
                    payload.update(
                        review_at=time_value(point + _FALLBACK_REVIEW),
                        review_basis="fallback",
                        expires_at=None,
                    )
                else:
                    payload.update(
                        review_at=None if current.review_at is None else time_value(current.review_at),
                        review_basis=current.review_basis,
                        expires_at=None if current.expires_at is None else time_value(current.expires_at),
                    )
            if not payload:
                raise FactValidationError("amend must change metadata")
            return payload
        if op == "supersede":
            self._unknown(change, common | {"successor_id", "reason", "sources", "maintenance_merge"})
            successor_id = text(change.get("successor_id"), "successor_id")
            if successor_id not in state:
                raise FactValidationError("successor must exist")
            successor = state[successor_id]
            dependencies.setdefault(successor_id, successor.version)
            if current.evidence_class == "direct" and successor.evidence_class == "inferred":
                raise FactValidationError("inferred evidence may not supersede direct evidence")
            if change.get("maintenance_merge") is True and current.scope != successor.scope:
                raise FactValidationError("duplicate merge cannot cross fact scopes")
            would_cycle(state, current.fact_id, successor_id)
            payload: dict[str, Any] = {
                "successor_id": successor_id,
                "reason": text(change.get("reason"), "reason"),
            }
            if change.get("maintenance_merge") is True:
                payload["maintenance_merge"] = True
        else:
            self._unknown(change, common | {"reason", "sources"})
            payload = {"reason": text(change.get("reason"), "reason")}
        if "sources" in change:
            payload["sources"] = list(sources(change["sources"]))
        self._copy_expected_review_window(change, payload)
        return payload

    def _copy_expected_review_window(self, change: Mapping[str, Any], payload: dict[str, Any]) -> None:
        if "expected_review_window" in change:
            payload["review_window"] = fact_version(change["expected_review_window"], "expected_review_window")

    def _metadata_payload(
        self,
        change: Mapping[str, Any],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if "kind" in change:
            payload["kind"] = text(change["kind"], "kind")
        if "labels" in change:
            payload["labels"] = list(strings(change["labels"], "labels"))
        if "subjects" in change:
            payload["subjects"] = list(strings(change["subjects"], "subjects", required=True))
        if "sources" in change:
            payload["sources"] = list(sources(change["sources"]))
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
                return time_value(fallback_from + _FALLBACK_REVIEW), "fallback"
            return None, None
        if review_basis not in REVIEW_BASES:
            raise FactValidationError("review_at requires a valid review_basis")
        return time_value(parse_time(review_at, "review_at")), str(review_basis)

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
            suffix = "".join(canonical(event.record) + "\n" for event in events).encode()
            resource = resources.get(month)
            if resource is None:
                operations.append(Create(month, month, suffix))
            else:
                prior = snapshot.contents[month]
                parse_event_file(month, prior)
                operations.append(Update(resource.resource_id, resource.version_id, prior + suffix))
        return operations

    def _events_added_by_commit(self, commit: Commit) -> tuple[FactEvent, ...]:
        """Decode each monthly append in commit order, never timestamp order."""

        result: list[FactEvent] = []
        for change in sorted(commit.changes, key=lambda item: "" if item.after is None else item.after.path):
            after = change.after
            if after is None:
                raise FactLedgerCorruptionError("fact revision removed a managed resource")
            path = after.path
            if path == _MARKER_PATH:
                continue
            if MONTH.fullmatch(path) is None:
                raise FactLedgerCorruptionError("fact revision changed an unexpected resource")
            try:
                current = self._repository.read(after.resource_id, at=commit.commit_id)
                previous = (
                    b""
                    if change.before is None
                    else self._repository.read(change.before.resource_id, at=commit.parent_id)
                )
            except (CorruptRepositoryError, KeyError) as exc:
                raise FactLedgerCorruptionError("fact revision contents are corrupt") from exc
            if not current.startswith(previous):
                raise FactLedgerCorruptionError("monthly fact record lost an append-only prefix")
            result.extend(parse_event_file(path, current[len(previous) :]))
        return tuple(result)

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
            elif MONTH.fullmatch(path):
                if resource.resource_id != path:
                    raise FactLedgerCorruptionError("monthly fact resource identity mismatch")
                events.extend(parse_event_file(path, content))
                self._assert_append_only(resource.resource_id, head)
            else:
                raise FactLedgerCorruptionError(f"unexpected fact resource: {path}")
        ids = [event.event_id for event in events]
        if len(ids) != len(set(ids)):
            raise FactLedgerCorruptionError("duplicate event id")
        try:
            state = state_from(events)
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

    @staticmethod
    def _unknown(value: Mapping[str, Any], allowed: set[str]) -> None:
        if set(value) - allowed:
            raise FactValidationError("unknown change fields")

    def _now(self) -> datetime:
        return self._utc(self._clock())

    @staticmethod
    def _utc(value: datetime) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise FactValidationError("clock must return an aware datetime")
        return value.astimezone(UTC)
