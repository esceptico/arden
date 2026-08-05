"""Async, scope-aware service boundary above the synchronous fact ledger."""

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Any, Protocol

from arden.core.public_refs import is_public_ref, public_ref
from arden.logging import get_logger
from arden.memory.facts.ledger import FactLedger
from arden.memory.facts.models import Fact, FactChangeFeed, FactConflictError, FactEvent, FactPlan, FactValidationError
from arden.memory.facts.plan_store import (
    FactPlanCorruptionError,
    FactPlanRequestConflictError,
    FactPlanStore,
    StoredFactPlan,
)

FactScope = tuple[str, str | None]
FactOrder = tuple[datetime, str]
_SCOPE_KINDS = frozenset({"user", "area", "global", "project", "integration"})
DEFAULT_RESULT_LIMIT = 100
BATCH_FACT_LIMIT = 100
_logger = get_logger(__name__)


def fact_public_ref(fact: Fact) -> str:
    """Return the canonical model-facing handle for one immutable fact."""

    return public_ref(fact.text, fact.fact_id, empty_slug="fact")


class RankedFactSearch(Protocol):
    """Relevance-ordered fact IDs for a query; None when the index cannot serve."""

    def __call__(self, query: str, *, limit: int) -> Awaitable[tuple[str, ...] | None]: ...


class FactSearchUnavailableError(RuntimeError):
    """The ranked fact search index cannot serve queries right now."""


@dataclass(frozen=True, slots=True)
class FactPrincipal:
    """The caller identity and the scopes it may inspect or mutate."""

    owner_id: str
    readable_scopes: frozenset[FactScope]
    writable_scopes: frozenset[FactScope]

    def can_read(self, scope: FactScope) -> bool:
        return scope in self.readable_scopes

    def can_write(self, scope: FactScope) -> bool:
        return scope in self.writable_scopes


@dataclass(frozen=True, slots=True)
class FactPlanPreview:
    plan_id: str
    events: tuple[FactEvent, ...]
    dependencies: dict[str, str]
    duplicate_windows: dict[str, str]
    preview: str


@dataclass(frozen=True, slots=True)
class FactCommitResult:
    plan_id: str
    events: tuple[FactEvent, ...]
    facts: tuple[Fact, ...]


@dataclass(frozen=True, slots=True)
class DueFactReview:
    fact: Fact
    due_at: datetime
    related_facts: tuple[Fact, ...]
    review_window: str
    explicit_expiry_due: bool


@dataclass(frozen=True, slots=True)
class RetentionReviewBatch:
    revision: str | None
    evaluated_at: datetime
    reviews: tuple[DueFactReview, ...]


@dataclass(frozen=True, slots=True)
class FactPage[T]:
    items: tuple[T, ...]
    total: int
    has_more: bool


class FactScopeError(PermissionError):
    """A fact operation falls outside the principal's readable/writeable scopes."""


class FactPostCommitError(RuntimeError):
    """A committed plan could not notify its derived-state consumers.

    The canonical commit is durable. Retry :meth:`FactService.commit` with the
    same plan id to repeat the idempotent notification.
    """

    def __init__(self, result: FactCommitResult) -> None:
        super().__init__(f"fact plan {result.plan_id} committed but post-commit processing failed")
        self.result = result


@dataclass
class FactService:
    """Facts API: durable plan workflow state above canonical append-only facts."""

    ledger: FactLedger
    plans: FactPlanStore
    post_commit: Callable[[], Awaitable[None]] | None = None
    # None = no search index configured; queries then use the ledger's
    # exhaustive substring scan, as `include_inactive` archaeology always does.
    ranked_search: RankedFactSearch | None = None
    _ref_lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False, repr=False)
    _ref_revision: str | None = field(default=None, init=False, repr=False)
    _ref_index: Mapping[str, Fact] = field(default_factory=dict, init=False, repr=False)
    _fact_ref_index: Mapping[str, str] = field(default_factory=dict, init=False, repr=False)
    _ref_index_ready: bool = field(default=False, init=False, repr=False)

    async def search(
        self,
        principal: FactPrincipal,
        query: str | None = None,
        *,
        subject: str | None = None,
        include_inactive: bool = False,
        limit: int = DEFAULT_RESULT_LIMIT,
        after: FactOrder | None = None,
    ) -> tuple[Fact, ...]:
        return (
            await self.search_page(
                principal,
                query,
                subject=subject,
                include_inactive=include_inactive,
                limit=limit,
                after=after,
            )
        ).items

    async def visible_facts(self, principal: FactPrincipal) -> tuple[Fact, ...]:
        """Return one current, owner-filtered snapshot for exact reference lookup."""

        snapshot = await asyncio.to_thread(self.ledger.read_snapshot)
        return tuple(
            sorted(
                (fact for fact in snapshot.facts.values() if principal.can_read(_scope(fact))),
                key=lambda fact: (fact.created_at, fact.fact_id),
            )
        )

    async def resolve_ref(self, principal: FactPrincipal, fact_ref: str) -> Fact:
        """Resolve one exact public handle without replaying an unchanged ledger."""

        return (await self.resolve_refs(principal, (fact_ref,)))[0]

    async def resolve_refs(self, principal: FactPrincipal, fact_refs: tuple[str, ...]) -> tuple[Fact, ...]:
        """Resolve exact handles against one revision-keyed canonical index."""

        if not fact_refs:
            return ()
        if any(not is_public_ref(ref) for ref in fact_refs):
            raise KeyError("unknown fact_ref")
        index, _ = await self._ref_indexes()

        facts: list[Fact] = []
        for ref in fact_refs:
            try:
                fact = index[ref]
            except KeyError as exc:
                raise KeyError("unknown fact_ref") from exc
            if not principal.can_read(_scope(fact)):
                raise KeyError("unknown fact_ref")
            facts.append(fact)
        return tuple(facts)

    async def public_refs(self, principal: FactPrincipal, fact_ids: Iterable[str]) -> dict[str, str]:
        """Resolve internal fact identities to public refs through the cached exact index."""

        ids = tuple(dict.fromkeys(fact_ids))
        if any(not isinstance(fact_id, str) or not fact_id for fact_id in ids):
            raise ValueError("fact IDs must be non-empty strings")
        index, refs = await self._ref_indexes()
        result: dict[str, str] = {}
        for fact_id in ids:
            try:
                fact_ref = refs[fact_id]
                fact = index[fact_ref]
            except KeyError as exc:
                raise KeyError("unknown fact") from exc
            if not principal.can_read(_scope(fact)):
                raise KeyError("unknown fact")
            result[fact_id] = fact_ref
        return result

    async def new_public_refs(self, facts: Mapping[str, str]) -> dict[str, str]:
        """Allocate preview-only public refs without rereading the ledger."""

        index, _ = await self._ref_indexes()
        result: dict[str, str] = {}
        for fact_id, text in facts.items():
            if not isinstance(fact_id, str) or not fact_id or not isinstance(text, str) or not text:
                raise ValueError("new facts require non-empty IDs and text")
            fact_ref = public_ref(text, fact_id, empty_slug="fact")
            if fact_ref in index or fact_ref in result.values():
                raise FactValidationError("fact_ref collision prevents exact plan preview")
            result[fact_id] = fact_ref
        return result

    async def _ref_indexes(self) -> tuple[Mapping[str, Fact], Mapping[str, str]]:
        current_revision = await self.current_revision()
        async with self._ref_lock:
            if not self._ref_index_ready or self._ref_revision != current_revision:
                snapshot = await asyncio.to_thread(self.ledger.read_snapshot)
                index: dict[str, Fact] = {}
                by_id: dict[str, str] = {}
                for fact in snapshot.facts.values():
                    ref = fact_public_ref(fact)
                    if ref in index:
                        raise FactValidationError("fact_ref collision prevents exact resolution")
                    index[ref] = fact
                    by_id[fact.fact_id] = ref
                self._ref_index = MappingProxyType(index)
                self._fact_ref_index = MappingProxyType(by_id)
                self._ref_revision = snapshot.revision
                self._ref_index_ready = True
            return self._ref_index, self._fact_ref_index

    async def search_page(
        self,
        principal: FactPrincipal,
        query: str | None = None,
        *,
        subject: str | None = None,
        include_inactive: bool = False,
        limit: int = DEFAULT_RESULT_LIMIT,
        after: FactOrder | None = None,
    ) -> FactPage[Fact]:
        limit = _limit(limit)
        after = _after(after)
        ranked_search = self.ranked_search
        if query is not None and not include_inactive and ranked_search is not None:
            return await self._ranked_page(ranked_search, principal, query, subject=subject, limit=limit)
        facts = await asyncio.to_thread(
            self.ledger.search,
            query,
            subject=subject,
            include_inactive=include_inactive,
        )
        visible = tuple(fact for fact in facts if principal.can_read(_scope(fact)))
        remaining = tuple(fact for fact in visible if after is None or (fact.created_at, fact.fact_id) > after)
        return FactPage(remaining[:limit], len(visible), len(remaining) > limit)

    async def _ranked_page(
        self,
        ranked_search: RankedFactSearch,
        principal: FactPrincipal,
        query: str,
        *,
        subject: str | None,
        limit: int,
    ) -> FactPage[Fact]:
        """Relevance order has no stable cursor: one top-N page, never `has_more`.
        Every hit is revalidated against current ledger state, so the page may
        run short of `limit` when the index leads the ledger or scopes hide hits."""
        ranked = await ranked_search(query, limit=limit)
        if ranked is None:
            raise FactSearchUnavailableError("fact search index is unavailable")
        active = {fact.fact_id: fact for fact in await asyncio.to_thread(self.ledger.search)}
        visible: list[Fact] = []
        for fact_id in ranked:
            if len(visible) == limit:
                break
            fact = active.get(fact_id)
            if fact is None or not principal.can_read(_scope(fact)):
                continue
            if subject is not None and subject not in fact.subjects:
                continue
            visible.append(fact)
        return FactPage(tuple(visible), len(visible), False)

    async def revision(self) -> str | None:
        return await asyncio.to_thread(lambda: self.ledger.revision)

    async def current_revision(self) -> str | None:
        """Return the cheap published head used to validate derived caches."""

        return await asyncio.to_thread(lambda: self.ledger.current_revision)

    async def changes_since(self, watermark: str | None) -> FactChangeFeed:
        """Read one stable canonical delta for a durable backend consumer."""

        return await asyncio.to_thread(self.ledger.changes_since, watermark)

    async def known_scopes(self) -> frozenset[FactScope]:
        """Return scopes present in canonical history without granting authority."""

        return await asyncio.to_thread(self.ledger.known_scopes)

    async def get(self, principal: FactPrincipal, fact_id: str) -> Fact:
        fact = await asyncio.to_thread(self.ledger.get, fact_id)
        self._require_read(principal, _scope(fact))
        return fact

    async def get_many_with_snapshot_principal(
        self,
        fact_ids: tuple[str, ...],
        principal_from_scopes: Callable[[frozenset[FactScope]], FactPrincipal],
    ) -> tuple[Fact, ...]:
        """Read facts and construct their principal from one canonical snapshot."""

        fact_ids = _fact_ids(fact_ids)
        snapshot = await asyncio.to_thread(self.ledger.read_snapshot)
        principal = principal_from_scopes(snapshot.known_scopes)
        return self._read_many(principal, snapshot.facts, fact_ids)

    @staticmethod
    def _read_many(
        principal: FactPrincipal,
        facts_by_id: Mapping[str, Fact],
        fact_ids: tuple[str, ...],
    ) -> tuple[Fact, ...]:
        facts = []
        for fact_id in fact_ids:
            try:
                fact = facts_by_id[fact_id]
            except KeyError as exc:
                raise KeyError(f"unknown fact: {fact_id}") from exc
            FactService._require_read(principal, _scope(fact))
            facts.append(fact)
        return tuple(facts)

    async def history(self, principal: FactPrincipal, fact_id: str) -> tuple[FactEvent, ...]:
        """Return the chain under its current canonical scope.

        A scope amendment is an explicit reclassification of the whole fact,
        not an ACL transfer between separate historical objects. Planning such
        an amendment requires write authority over both old and new scopes.
        """

        await self.get(principal, fact_id)
        return await asyncio.to_thread(self.ledger.history, fact_id)

    async def due_reviews(
        self,
        principal: FactPrincipal,
        *,
        limit: int = DEFAULT_RESULT_LIMIT,
        after: FactOrder | None = None,
    ) -> tuple[DueFactReview, ...]:
        return (await self.due_review_page(principal, limit=limit, after=after)).items

    async def due_review_page(
        self,
        principal: FactPrincipal,
        *,
        limit: int = DEFAULT_RESULT_LIMIT,
        after: FactOrder | None = None,
    ) -> FactPage[DueFactReview]:
        limit = _limit(limit)
        after = _after(after)
        batch = await self.retention_review_batch(principal)
        visible_due = batch.reviews
        remaining = tuple(item for item in visible_due if after is None or (item.due_at, item.fact.fact_id) > after)
        return FactPage(remaining[:limit], len(visible_due), len(remaining) > limit)

    async def retention_review_batch(self, principal: FactPrincipal) -> RetentionReviewBatch:
        revision, evaluated_at, due = await asyncio.to_thread(self.ledger.due_review_snapshot)
        reviews = []
        for item in due:
            if not principal.can_read(_scope(item.fact)):
                continue
            reviews.append(
                DueFactReview(
                    item.fact,
                    item.due_at,
                    item.related_facts,
                    item.review_window,
                    item.fact.expires_at is not None and item.fact.expires_at <= evaluated_at,
                )
            )
        return RetentionReviewBatch(revision, evaluated_at, tuple(reviews))

    async def plan(
        self,
        principal: FactPrincipal,
        changes: list[dict[str, Any]] | tuple[dict[str, Any], ...],
        *,
        request_key: str,
        actor: str,
        origin: str,
        reason: str,
    ) -> FactPlanPreview:
        batch = tuple(changes)
        request_fingerprint = _request_fingerprint(principal, batch, actor=actor, origin=origin, reason=reason)
        existing = await self.plans.get_by_request(owner_id=principal.owner_id, request_key=request_key)
        if existing is not None:
            if existing.request_fingerprint != request_fingerprint:
                raise FactPlanRequestConflictError("request_key was reused for a different fact plan request")
            self._require_write_scopes(principal, existing.scopes)
            plan = await self._stored_plan(existing)
            self._require_read_scopes(principal, await self._plan_evidence_scopes(plan))
            return _preview(plan)

        plan = await asyncio.to_thread(self.ledger.plan, batch, actor=actor, origin=origin, reason=reason)
        scopes = await self._plan_scopes(plan)
        self._require_write_scopes(principal, scopes)
        self._require_read_scopes(principal, await self._plan_evidence_scopes(plan))
        stored = await self.plans.create_or_reuse(
            owner_id=principal.owner_id,
            request_key=request_key,
            request_fingerprint=request_fingerprint,
            scopes=scopes,
            plan=plan,
        )
        self._require_write_scopes(principal, stored.scopes)
        return _preview(await self._stored_plan(stored))

    async def commit(
        self,
        principal: FactPrincipal,
        plan_id: str,
        *,
        expected_revision: str | None = None,
    ) -> FactCommitResult:
        stored = await self.plans.get(plan_id, owner_id=principal.owner_id)
        self._require_write_scopes(principal, stored.scopes)
        plan = await self._stored_plan(stored)
        self._require_read_scopes(principal, await self._plan_evidence_scopes(plan))
        stored, _claimed = await self.plans.claim(plan_id, owner_id=principal.owner_id)
        # A persisted `committing` row may be an interrupted caller. The ledger's
        # immutable plan-id idempotency makes resuming it safe after restart.
        try:
            if expected_revision is None:
                events = await asyncio.to_thread(self.ledger.commit, plan)
            else:
                events = await asyncio.to_thread(self.ledger.commit_at_revision, plan, expected_revision)
        except FactConflictError:
            await self.plans.release(plan_id, owner_id=principal.owner_id)
            raise
        await self.plans.mark_committed(plan_id, owner_id=principal.owner_id)
        fact_ids = tuple(dict.fromkeys(event.fact_id for event in events))
        facts = tuple(await asyncio.gather(*(asyncio.to_thread(self.ledger.get, fact_id) for fact_id in fact_ids)))
        result = FactCommitResult(plan_id, events, facts)
        # Notification is deliberately at-least-once. A replay after a failed
        # callback re-arms the idempotent debounce without rewriting the facts.
        if self.post_commit is not None:
            try:
                await self.post_commit()
            except Exception as exc:
                _logger.warning("post-commit fact notification failed", exc_info=True)
                raise FactPostCommitError(result) from exc
        return result

    async def _stored_plan(self, stored: StoredFactPlan) -> FactPlan:
        plan = stored.plan()
        try:
            await asyncio.to_thread(self.ledger.validate_plan, plan)
        except (FactValidationError, TypeError) as exc:
            raise FactPlanCorruptionError("persisted fact plan failed ledger validation") from exc
        scopes = await self._plan_scopes(plan)
        if scopes != stored.scopes:
            raise FactPlanCorruptionError("persisted fact plan scopes do not match canonical dependencies")
        return plan

    async def _plan_scopes(self, plan: FactPlan) -> tuple[FactScope, ...]:
        created: dict[str, FactScope] = {}
        scopes: set[FactScope] = set()
        for event in plan.events:
            if event.op == "create":
                scope = _payload_scope(event.record["payload"])
                created[event.fact_id] = scope
                scopes.add(scope)
                continue
            scope = created.get(event.fact_id)
            if scope is None:
                scope = await self._dependency_scope(plan, event.fact_id)
            scopes.add(scope)
            if event.op == "amend" and "scope" in event.record["payload"]:
                scopes.add(_payload_scope({"scope": event.record["payload"]["scope"]}))
            if event.op == "supersede":
                successor_id = event.record["payload"]["successor_id"]
                successor_scope = created.get(successor_id)
                if successor_scope is None:
                    successor_scope = await self._dependency_scope(plan, successor_id)
                scopes.add(successor_scope)
        return tuple(sorted(scopes, key=lambda item: (item[0], item[1] or "")))

    async def _dependency_scope(self, plan: FactPlan, fact_id: str) -> FactScope:
        try:
            version = plan.dependencies[fact_id]
            fact = await asyncio.to_thread(self.ledger.get_version, fact_id, version)
        except (KeyError, FactValidationError) as exc:
            raise FactPlanCorruptionError("fact plan dependency is unavailable") from exc
        return _scope(fact)

    async def _plan_evidence_scopes(self, plan: FactPlan) -> tuple[FactScope, ...]:
        scopes = set()
        for event in plan.events:
            for source in event.record["payload"].get("sources", ()):
                if source.get("kind") != "fact":
                    continue
                scopes.add(await self._dependency_scope(plan, source["ref"]))
        return tuple(sorted(scopes, key=lambda item: (item[0], item[1] or "")))

    @staticmethod
    def _require_read(principal: FactPrincipal, scope: FactScope) -> None:
        if not principal.can_read(scope):
            raise FactScopeError("fact is outside the current readable scope")

    @staticmethod
    def _require_write_scopes(principal: FactPrincipal, scopes: tuple[FactScope, ...]) -> None:
        if any(not principal.can_write(scope) for scope in scopes):
            raise FactScopeError("fact plan is outside the current writable scope")

    @staticmethod
    def _require_read_scopes(principal: FactPrincipal, scopes: tuple[FactScope, ...]) -> None:
        if any(not principal.can_read(scope) for scope in scopes):
            raise FactScopeError("fact evidence is outside the current readable scope")


def _scope(fact: Fact) -> FactScope:
    return fact.scope["kind"], fact.scope["key"]


def _payload_scope(payload: object) -> FactScope:
    if not isinstance(payload, dict) or not isinstance(payload.get("scope"), dict):
        raise FactValidationError("fact plan create event has no scope")
    scope = payload["scope"]
    try:
        return _validated_scope(scope.get("kind"), scope.get("key"))
    except ValueError as exc:
        raise FactValidationError("fact plan create event has an invalid scope") from exc


def _validated_scope(kind: object, key: object) -> FactScope:
    if not isinstance(kind, str) or not kind or "\0" in kind or kind not in _SCOPE_KINDS:
        raise ValueError("invalid scope kind")
    if kind in {"user", "global"}:
        if key is not None:
            raise ValueError(f"{kind} scope key must be null")
        return kind, None
    if not isinstance(key, str) or not key or "\0" in key:
        raise ValueError(f"{kind} scope key must be a non-empty string")
    return kind, key


def _limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= DEFAULT_RESULT_LIMIT:
        raise ValueError(f"limit must be between 1 and {DEFAULT_RESULT_LIMIT}")
    return value


def _fact_ids(value: tuple[str, ...]) -> tuple[str, ...]:
    if not 1 <= len(value) <= BATCH_FACT_LIMIT:
        raise ValueError(f"fact_ids must contain between 1 and {BATCH_FACT_LIMIT} ids")
    if any(not isinstance(fact_id, str) or not fact_id or "\0" in fact_id for fact_id in value):
        raise ValueError("fact_ids must contain non-empty strings")
    if len(set(value)) != len(value):
        raise ValueError("fact_ids must not contain duplicates")
    return value


def _after(value: FactOrder | None) -> FactOrder | None:
    if value is None:
        return None
    if not isinstance(value, tuple) or len(value) != 2:
        raise ValueError("after must be a timestamp and fact_id pair")
    point, fact_id = value
    if (
        not isinstance(point, datetime)
        or point.tzinfo is None
        or point.utcoffset() != timedelta(0)
        or not isinstance(fact_id, str)
        or not fact_id
        or "\0" in fact_id
    ):
        raise ValueError("after must be a UTC timestamp and non-empty fact_id")
    return point.astimezone(UTC), fact_id


def _request_fingerprint(
    principal: FactPrincipal,
    changes: tuple[dict[str, Any], ...],
    *,
    actor: str,
    origin: str,
    reason: str,
) -> str:
    value = {
        "owner_id": principal.owner_id,
        "changes": changes,
        "actor": actor,
        "origin": origin,
        "reason": reason,
    }
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise FactValidationError("fact plan request must be JSON-safe") from exc
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _preview(plan: FactPlan) -> FactPlanPreview:
    lines = [_event_preview(event) for event in plan.events]
    return FactPlanPreview(
        plan.plan_id,
        plan.events,
        dict(plan.dependencies),
        dict(plan.duplicate_windows),
        "\n".join(lines),
    )


def _event_preview(event: FactEvent) -> str:
    payload = event.record["payload"]
    fact_id = _quoted(event.fact_id)
    if event.op == "create":
        return f"- create {fact_id}: {_quoted(payload['text'])}"
    if event.op == "review":
        review_at = "none" if payload["review_at"] is None else _quoted(payload["review_at"])
        return f"- review {fact_id}: {_quoted(payload['reason'])} (next review: {review_at})"
    if event.op == "amend":
        return f"- amend {fact_id}: {', '.join(sorted(payload))}"
    if event.op == "supersede":
        return f"- supersede {fact_id} with {_quoted(payload['successor_id'])}: {_quoted(payload['reason'])}"
    return f"- {event.op} {fact_id}: {_quoted(payload['reason'])}"


def _quoted(value: object) -> str:
    return json.dumps(value, ensure_ascii=True)
