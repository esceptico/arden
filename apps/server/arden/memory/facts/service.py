"""Async, scope-aware service boundary above the synchronous fact ledger."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from arden.logging import get_logger

from .ledger import FactLedger
from .models import Fact, FactChangeFeed, FactConflictError, FactEvent, FactPlan, FactValidationError
from .plan_store import (
    FactPlanCorruptionError,
    FactPlanRequestConflictError,
    FactPlanStore,
    StoredFactPlan,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

FactScope = tuple[str, str | None]
FactOrder = tuple[datetime, str]
_SCOPE_KINDS = frozenset({"user", "area", "global", "project", "integration"})
DEFAULT_RESULT_LIMIT = 100
_logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class FactPrincipal:
    """The caller identity and the scopes it may inspect or mutate."""

    owner_id: str
    readable_scopes: frozenset[FactScope]
    writable_scopes: frozenset[FactScope]

    def __post_init__(self) -> None:
        if not isinstance(self.owner_id, str) or not self.owner_id.strip() or "\0" in self.owner_id:
            raise ValueError("owner_id must be a non-empty string")
        for scope in (*self.readable_scopes, *self.writable_scopes):
            if not isinstance(scope, tuple) or len(scope) != 2:
                raise ValueError("scopes must be (kind, key) pairs")
            _validated_scope(*scope)

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


@dataclass
class FactService:
    """Facts API: durable plan workflow state above canonical append-only facts."""

    ledger: FactLedger
    plans: FactPlanStore
    post_commit: Callable[[], Awaitable[None]] | None = None

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
        facts = await asyncio.to_thread(
            self.ledger.search,
            query,
            subject=subject,
            include_inactive=include_inactive,
        )
        visible = tuple(fact for fact in facts if principal.can_read(_scope(fact)))
        remaining = tuple(fact for fact in visible if after is None or (fact.created_at, fact.fact_id) > after)
        return FactPage(remaining[:limit], len(visible), len(remaining) > limit)

    async def revision(self) -> str | None:
        return await asyncio.to_thread(lambda: self.ledger.revision)

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

    async def commit(self, principal: FactPrincipal, plan_id: str) -> FactCommitResult:
        stored = await self.plans.get(plan_id, owner_id=principal.owner_id)
        self._require_write_scopes(principal, stored.scopes)
        plan = await self._stored_plan(stored)
        self._require_read_scopes(principal, await self._plan_evidence_scopes(plan))
        stored, _claimed = await self.plans.claim(plan_id, owner_id=principal.owner_id)
        # A persisted `committing` row may be an interrupted caller. The ledger's
        # immutable plan-id idempotency makes resuming it safe after restart.
        try:
            events = await asyncio.to_thread(self.ledger.commit, plan)
        except FactConflictError:
            await self.plans.release(plan_id, owner_id=principal.owner_id)
            raise
        await self.plans.mark_committed(plan_id, owner_id=principal.owner_id)
        # Notification is deliberately at-least-once. A replay after an
        # uncertain callback must be able to re-arm the idempotent debounce.
        if self.post_commit is not None:
            try:
                await self.post_commit()
            except Exception:
                _logger.warning("post-commit fact notification failed", exc_info=True)
        fact_ids = tuple(dict.fromkeys(event.fact_id for event in events))
        facts = tuple(await asyncio.gather(*(asyncio.to_thread(self.ledger.get, fact_id) for fact_id in fact_ids)))
        return FactCommitResult(plan_id, events, facts)

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
