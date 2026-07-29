"""Conservative reconciliation over the canonical fact change feed."""

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from arden.constants import BUILTIN_MEMORY_CONSOLIDATE_ID
from arden.llm.base import CompletionClient
from arden.memory.facts.consumer_store import FactConsumerStore
from arden.memory.facts.maintenance.store import (
    CONSUMER_ID,
    FactMaintenanceError,
    FactMaintenanceIntentStore,
    MaintenanceIntent,
    NormalizedFactIntent,
)
from arden.memory.facts.models import Fact, FactChangeFeed, FactConflictError
from arden.memory.facts.service import FactPrincipal, FactService
from arden.wiki.models import WikiMaintenancePageUpdate, WikiPageRecord, WikiSnapshot
from arden.wiki.service import WikiService

ORIGIN = "memory.maintenance"
_ACTOR = f"automation:{BUILTIN_MEMORY_CONSOLIDATE_ID}"
_MAX_FACT_TEXT = 4_000
_MAX_REASON = 1_000
_REVIEW_SYSTEM_PROMPT = """Review one prepared canonical-fact cluster using only the supplied evidence.
Return no_change when evidence is weak or ambiguous.
For no_change, leave every action field null. You may only:
- amend metadata on the changed fact: kind, labels, subjects, lifecycle, or evidence class;
- merge two genuine duplicate claims from this cluster while preserving one as survivor.
- normalize one exact subject of the changed fact to the title of a supplied canonical wiki page.

Never create or rewrite fact text. Never review, expire, retract, age-archive, or perform
general evidence-based supersession. Never edit wiki prose. An inferred fact cannot replace
a direct fact. For normalize_topic, copy old_topic exactly from the changed fact and select
the canonical page only with its opaque P### token. Never return a page ID, path, or invented
page name. Do not normalize when the relationship is ambiguous or the old topic already has
its own page. Use only the opaque F### and P### tokens shown in the cluster.
"""


class FactMaintenanceCandidateProvider(Protocol):
    """Optional semantic fallback over a rebuildable fact index."""

    async def semantic_candidates(self, fact: Fact, *, limit: int) -> Sequence[str]: ...


class FactMaintenanceDecision(BaseModel):
    """One constrained decision addressed only by run-local opaque tokens."""

    model_config = ConfigDict(extra="forbid", strict=True)

    outcome: Literal["no_change", "amend_metadata", "merge_duplicate", "normalize_topic"]
    reason: str = Field(min_length=1, max_length=_MAX_REASON)
    target_token: str | None = None
    kind: str | None = Field(default=None, min_length=1, max_length=200)
    labels: list[str] | None = Field(default=None, max_length=100)
    subjects: list[str] | None = Field(default=None, min_length=1, max_length=100)
    lifecycle: Literal["durable", "temporary"] | None = None
    evidence_class: Literal["direct", "inferred"] | None = None
    discard_token: str | None = None
    survivor_token: str | None = None
    old_topic: str | None = Field(default=None, min_length=1, max_length=_MAX_FACT_TEXT)
    canonical_page_token: str | None = None

    @model_validator(mode="after")
    def _validate_outcome_shape(self) -> Self:
        metadata = (self.kind, self.labels, self.subjects, self.lifecycle, self.evidence_class)
        normalization = (self.old_topic, self.canonical_page_token)
        if self.outcome == "no_change":
            if (
                self.target_token is not None
                or self.kind is not None
                or bool(self.labels)
                or self.subjects is not None
                or self.lifecycle is not None
                or self.evidence_class is not None
            ):
                raise ValueError("no_change must not include an amendment")
            if self.discard_token is not None or self.survivor_token is not None:
                raise ValueError("no_change must not include a merge")
            if any(value is not None for value in normalization):
                raise ValueError("no_change must not include topic normalization")
        elif self.outcome == "amend_metadata":
            if self.target_token is None or all(value is None for value in metadata):
                raise ValueError("amend_metadata requires a target and metadata")
            if self.discard_token is not None or self.survivor_token is not None:
                raise ValueError("amend_metadata must not include a merge")
            if any(value is not None for value in normalization):
                raise ValueError("amend_metadata must not include topic normalization")
        elif self.outcome == "merge_duplicate":
            if self.discard_token is None or self.survivor_token is None:
                raise ValueError("merge_duplicate requires both fact tokens")
            if self.discard_token == self.survivor_token:
                raise ValueError("merge_duplicate tokens must be different")
            if self.target_token is not None or any(value is not None for value in metadata):
                raise ValueError("merge_duplicate must not include an amendment")
            if any(value is not None for value in normalization):
                raise ValueError("merge_duplicate must not include topic normalization")
        else:
            if self.old_topic is None or self.canonical_page_token is None:
                raise ValueError("normalize_topic requires an old topic and canonical page token")
            if self.target_token is not None or any(value is not None for value in metadata):
                raise ValueError("normalize_topic must not include an amendment")
            if self.discard_token is not None or self.survivor_token is not None:
                raise ValueError("normalize_topic must not include a merge")
        return self


@dataclass(frozen=True, slots=True)
class FactMaintenancePreparedCluster:
    """Bounded, pinned evidence handed to one reviewer call."""

    target_token: str
    markdown: str
    fact_tokens: Mapping[str, Fact]
    shared_subject_tokens: tuple[str, ...]
    exact_text_tokens: tuple[str, ...]
    semantic_tokens: tuple[str, ...]
    wiki_page_tokens: Mapping[str, str]


async def review_fact_maintenance(
    client: CompletionClient,
    model: str,
    cluster: FactMaintenancePreparedCluster,
    *,
    reasoning_effort: str | None = None,
) -> FactMaintenanceDecision:
    response = await client.completion(
        model=model,
        messages=[
            {"role": "system", "content": _REVIEW_SYSTEM_PROMPT},
            {"role": "user", "content": cluster.markdown},
        ],
        response_format=FactMaintenanceDecision,
        reasoning_effort=reasoning_effort,
    )
    if not response.choices or response.choices[0].message.content is None:
        raise ValueError("fact maintenance reviewer returned no decision")
    content = response.choices[0].message.content
    if isinstance(content, FactMaintenanceDecision):
        return content
    return FactMaintenanceDecision.model_validate_json(content)


FactMaintenanceReviewer = Callable[[FactMaintenancePreparedCluster], Awaitable[FactMaintenanceDecision]]


@dataclass(frozen=True, slots=True)
class FactMaintenanceResult:
    through_revision: str | None
    reviewed_clusters: int = 0
    amended_facts: int = 0
    merged_facts: int = 0
    advanced: bool = False
    empty: bool = False


@dataclass(frozen=True, slots=True)
class _ReviewedDecision:
    target: Fact
    cluster: FactMaintenancePreparedCluster
    decision: FactMaintenanceDecision


class FactMaintenance:
    """Review changed facts and publish one version-pinned maintenance plan."""

    def __init__(
        self,
        service: FactService,
        consumers: FactConsumerStore,
        reviewer: FactMaintenanceReviewer,
        *,
        wiki: WikiService,
        candidate_provider: FactMaintenanceCandidateProvider | None = None,
        candidate_limit: int = 12,
    ) -> None:
        if not 1 <= candidate_limit <= 100:
            raise ValueError("candidate_limit must be between 1 and 100")
        self._service = service
        self._consumers = consumers
        self._reviewer = reviewer
        self._wiki = wiki
        self._candidate_provider = candidate_provider
        self._candidate_limit = candidate_limit
        self._intents = FactMaintenanceIntentStore(service.plans.conn)

    async def run(self) -> FactMaintenanceResult:
        await self._intents.init_schema()
        watermark = await self._consumers.get(CONSUMER_ID)
        from_revision = None if watermark is None else watermark.revision
        intent = await self._intents.get()
        if intent is not None:
            if intent.from_revision != from_revision:
                # The watermark already moved past this completed intent.
                await self._intents.delete(intent.plan_id)
            else:
                feed = await asyncio.to_thread(
                    self._service.ledger.changes_between,
                    intent.from_revision,
                    intent.through_revision,
                )
                return await self._resume_intent(intent, feed)

        feed = await self._service.changes_since(from_revision)
        if not feed.events:
            return FactMaintenanceResult(feed.through_revision, empty=True)
        if feed.through_revision is None:
            raise FactMaintenanceError("nonempty fact feed has no through revision")

        facts = await asyncio.to_thread(self._service.ledger.facts_at, feed.through_revision)
        targets = self._pending_targets(feed, facts)
        if not targets:
            await self._consumers.advance(CONSUMER_ID, feed=feed, ledger=self._service.ledger)
            return FactMaintenanceResult(feed.through_revision, advanced=True)
        wiki_snapshot = await asyncio.to_thread(self._wiki.snapshot)
        wiki_pages = self._wiki_page_tokens(wiki_snapshot)
        reviewed: list[_ReviewedDecision] = []
        for target in targets:
            cluster = await self._prepare_cluster(target, facts, wiki_pages)
            decision = await self._reviewer(cluster)
            reviewed.append(_ReviewedDecision(target, cluster, decision))

        changes, amended, merged, reasons = self._prepare_changes(reviewed)
        topic_changes, topic_bindings, topic_reasons = self._prepare_topic_normalizations(
            reviewed,
            facts,
            wiki_snapshot,
            wiki_pages,
        )
        changes = self._merge_topic_changes(changes, topic_changes)
        amended = sum(change["op"] == "amend" for change in changes)
        if changes:
            principal = await self._principal()
            reason = self._plan_reason(feed, amended, merged, (*reasons, *topic_reasons))
            fingerprint = hashlib.sha256(
                json.dumps(changes, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            preview = await self._service.plan(
                principal,
                changes,
                request_key=f"{CONSUMER_ID}:{feed.from_revision or 'start'}:{feed.through_revision}:{fingerprint}",
                actor=_ACTOR,
                origin=ORIGIN,
                reason=reason,
            )
            if topic_changes:
                normalized = []
                for change in topic_changes:
                    fact_id = change["fact_id"]
                    normalized.append(
                        NormalizedFactIntent(
                            fact_id=fact_id,
                            before_subjects=facts[fact_id].subjects,
                            after_subjects=tuple(change["subjects"]),
                        )
                    )
                intent = MaintenanceIntent(
                    from_revision=feed.from_revision,
                    through_revision=feed.through_revision,
                    plan_id=preview.plan_id,
                    bindings=topic_bindings,
                    normalized_facts=tuple(normalized),
                )
                await self._intents.save(intent)
                return await self._resume_intent(intent, feed, reviewed_clusters=len(reviewed))
            await self._service.commit(principal, preview.plan_id)

        await self._consumers.advance(CONSUMER_ID, feed=feed, ledger=self._service.ledger)
        return FactMaintenanceResult(
            feed.through_revision,
            reviewed_clusters=len(reviewed),
            amended_facts=amended,
            merged_facts=merged,
            advanced=True,
        )

    async def _resume_intent(
        self,
        intent: MaintenanceIntent,
        feed: FactChangeFeed,
        *,
        reviewed_clusters: int = 0,
    ) -> FactMaintenanceResult:
        """Finish one durable alias-first intent without asking the reviewer again."""

        applied = await asyncio.to_thread(self._service.ledger.plan_applied, intent.plan_id)
        mapping_valid = await self._ensure_topic_aliases(intent)
        if not mapping_valid and not applied:
            # The selected page no longer owns the proposed name. Discard the
            # uncommitted plan and re-review against current wiki truth.
            await self._intents.delete(intent.plan_id)
            return await self.run()
        if mapping_valid and not applied:
            principal = await self._principal()
            try:
                await self._service.commit(
                    principal,
                    intent.plan_id,
                    expected_revision=intent.through_revision,
                )
            except FactConflictError:
                if not await asyncio.to_thread(self._service.ledger.plan_applied, intent.plan_id):
                    current = await self._service.revision()
                    if current != intent.through_revision:
                        await self._intents.delete(intent.plan_id)
                raise

        await self._converge_committed_normalization(intent)
        await self._consumers.advance(CONSUMER_ID, feed=feed, ledger=self._service.ledger)
        await self._intents.delete(intent.plan_id)
        stored = await self._service.plans.get(intent.plan_id, owner_id=_ACTOR)
        plan = stored.plan()
        return FactMaintenanceResult(
            feed.through_revision,
            reviewed_clusters=reviewed_clusters,
            amended_facts=sum(event.op == "amend" for event in plan.events),
            merged_facts=sum(event.op == "supersede" for event in plan.events),
            advanced=True,
        )

    async def _ensure_topic_aliases(self, intent: MaintenanceIntent) -> bool:
        """Publish missing aliases from current wiki truth, or defer on collision."""

        snapshot = await asyncio.to_thread(self._wiki.snapshot)
        active = {record.page.page_id: record for record in snapshot.pages if record.page.lifecycle == "active"}
        aliases_by_page: dict[str, list[str]] = {}
        pending_owners: dict[str, str] = {}
        for old_topic, page_id, canonical_title in intent.bindings:
            selected = active.get(page_id)
            if selected is None or selected.page.title != canonical_title:
                return False
            owner = self._wiki.resolve_topic_name(old_topic, snapshot=snapshot)
            if owner is not None:
                if owner.page.page_id != page_id:
                    return False
                continue
            normalized = old_topic.strip().casefold()
            pending = pending_owners.get(normalized)
            if pending is not None and pending != page_id:
                return False
            pending_owners[normalized] = page_id
            aliases = aliases_by_page.setdefault(page_id, [])
            if normalized not in {value.strip().casefold() for value in (*selected.page.aliases, *aliases)}:
                aliases.append(old_topic)

        updates = tuple(
            WikiMaintenancePageUpdate(
                page_id,
                active[page_id].resource.version_id,
                active[page_id].page.title,
                (*active[page_id].page.aliases, *aliases_by_page[page_id]),
                active[page_id].page.body,
            )
            for page_id in sorted(aliases_by_page)
        )
        if updates:
            if snapshot.head is None:
                raise FactMaintenanceError("active wiki pages require a repository head")
            await asyncio.to_thread(
                self._wiki.apply_maintenance_updates,
                updates,
                base_head=snapshot.head,
                actor=_ACTOR,
                origin=ORIGIN,
                reason=self._topic_alias_reason(updates),
                idempotency_key=self._topic_alias_key(intent.plan_id, updates),
            )
        return await asyncio.to_thread(self._topic_bindings_valid, intent.bindings)

    def _topic_bindings_valid(self, bindings: Sequence[tuple[str, str, str]]) -> bool:
        snapshot = self._wiki.snapshot()
        active = {record.page.page_id: record for record in snapshot.pages if record.page.lifecycle == "active"}
        for old_topic, page_id, canonical_title in bindings:
            selected = active.get(page_id)
            owner = self._wiki.resolve_topic_name(old_topic, snapshot=snapshot)
            if (
                selected is None
                or selected.page.title != canonical_title
                or owner is None
                or owner.page.page_id != page_id
            ):
                return False
        return True

    async def _converge_committed_normalization(self, intent: MaintenanceIntent) -> None:
        """Align facts to one binding state observed before and after correction."""

        for _attempt in range(8):
            binding_valid = await asyncio.to_thread(self._topic_bindings_valid, intent.bindings)
            await self._align_normalized_facts(intent, use_canonical=binding_valid)
            if not await self._normalized_facts_match(intent, use_canonical=binding_valid):
                continue
            if await asyncio.to_thread(self._topic_bindings_valid, intent.bindings) == binding_valid:
                return
        raise FactMaintenanceError("topic normalization remained contended during final revalidation")

    async def _align_normalized_facts(
        self,
        intent: MaintenanceIntent,
        *,
        use_canonical: bool,
    ) -> None:
        """Append one version-pinned fact plan toward the currently observed wiki truth."""

        current_revision = await self._service.revision()
        if current_revision is None:
            raise FactMaintenanceError("committed normalization requires a fact revision")
        facts = await asyncio.to_thread(self._service.ledger.facts_at, current_revision)
        changes: list[dict[str, object]] = []
        for item in intent.normalized_facts:
            fact = facts.get(item.fact_id)
            if fact is None:
                raise FactMaintenanceError("normalized fact disappeared before final alignment")
            desired = item.after_subjects if use_canonical else item.before_subjects
            prior = item.before_subjects if use_canonical else item.after_subjects
            if fact.status != "active" or fact.subjects == desired:
                continue
            if fact.subjects != prior:
                raise FactMaintenanceError("normalized fact changed before safe final alignment")
            changes.append(
                {
                    "op": "amend",
                    "fact_id": fact.fact_id,
                    "expected_version": fact.version,
                    "subjects": list(desired),
                }
            )
        if changes:
            principal = await self._principal()
            fingerprint = hashlib.sha256(
                json.dumps(changes, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            direction = "canonical" if use_canonical else "original"
            preview = await self._service.plan(
                principal,
                changes,
                request_key=f"{CONSUMER_ID}:align:{intent.plan_id}:{direction}:{fingerprint}",
                actor=_ACTOR,
                origin=ORIGIN,
                reason=f"align {direction} subjects for topic normalization plan {intent.plan_id}",
            )
            await self._service.commit(
                principal,
                preview.plan_id,
                expected_revision=current_revision,
            )

    async def _normalized_facts_match(
        self,
        intent: MaintenanceIntent,
        *,
        use_canonical: bool,
    ) -> bool:
        revision = await self._service.revision()
        if revision is None:
            return False
        facts = await asyncio.to_thread(self._service.ledger.facts_at, revision)
        for item in intent.normalized_facts:
            fact = facts.get(item.fact_id)
            desired = item.after_subjects if use_canonical else item.before_subjects
            if fact is None or (fact.status == "active" and fact.subjects != desired):
                return False
        return True

    @staticmethod
    def _pending_targets(feed: FactChangeFeed, facts: Mapping[str, Fact]) -> tuple[Fact, ...]:
        last_origin = {event.fact_id: event.origin for event in feed.events}
        return tuple(
            fact
            for fact_id in feed.affected_fact_ids
            if (fact := facts.get(fact_id)) is not None
            and fact.status == "active"
            and last_origin.get(fact_id) != ORIGIN
        )

    async def _prepare_cluster(
        self,
        target: Fact,
        facts: Mapping[str, Fact],
        wiki_pages: Mapping[str, WikiPageRecord],
    ) -> FactMaintenancePreparedCluster:
        pool = tuple(
            fact
            for fact in facts.values()
            if fact.fact_id != target.fact_id and fact.status == "active" and fact.scope == target.scope
        )
        target_subjects = set(target.subjects)
        shared = tuple(
            sorted(
                (fact for fact in pool if target_subjects.intersection(fact.subjects)),
                key=lambda fact: (-len(target_subjects.intersection(fact.subjects)), fact.created_at, fact.fact_id),
            )[: self._candidate_limit]
        )
        seen = {fact.fact_id for fact in shared}
        exact = tuple(
            sorted(
                (fact for fact in pool if fact.fact_id not in seen and fact.normalized_text == target.normalized_text),
                key=lambda fact: (fact.created_at, fact.fact_id),
            )[: self._candidate_limit]
        )
        seen.update(fact.fact_id for fact in exact)

        semantic: tuple[Fact, ...] = ()
        if self._candidate_provider is not None:
            candidate_ids = await self._candidate_provider.semantic_candidates(target, limit=self._candidate_limit)
            selected: list[Fact] = []
            for fact_id in candidate_ids:
                if fact_id in seen or fact_id == target.fact_id:
                    continue
                candidate = facts.get(fact_id)
                if candidate is None or candidate.status != "active" or candidate.scope != target.scope:
                    continue
                selected.append(candidate)
                seen.add(fact_id)
                if len(selected) == self._candidate_limit:
                    break
            semantic = tuple(selected)

        ordered = (target, *shared, *exact, *semantic)
        tokens = {f"F{index:03d}": fact for index, fact in enumerate(ordered)}
        token_for = {fact.fact_id: token for token, fact in tokens.items()}
        page_titles = MappingProxyType({token: record.page.title for token, record in wiki_pages.items()})
        return FactMaintenancePreparedCluster(
            target_token="F000",
            markdown=self._cluster_markdown(tokens, shared, exact, semantic, wiki_pages),
            fact_tokens=MappingProxyType(tokens),
            shared_subject_tokens=tuple(token_for[fact.fact_id] for fact in shared),
            exact_text_tokens=tuple(token_for[fact.fact_id] for fact in exact),
            semantic_tokens=tuple(token_for[fact.fact_id] for fact in semantic),
            wiki_page_tokens=page_titles,
        )

    @classmethod
    def _cluster_markdown(
        cls,
        tokens: Mapping[str, Fact],
        shared: Sequence[Fact],
        exact: Sequence[Fact],
        semantic: Sequence[Fact],
        wiki_pages: Mapping[str, WikiPageRecord],
    ) -> str:
        token_for = {fact.fact_id: token for token, fact in tokens.items()}

        def section(title: str, values: Sequence[Fact]) -> str:
            lines = [f"## {title}"]
            lines.extend(cls._fact_line(token_for[fact.fact_id], fact) for fact in values)
            if not values:
                lines.append("- none")
            return "\n".join(lines)

        return "\n\n".join(
            (
                "# Canonical fact maintenance cluster",
                section("Changed fact", (tokens["F000"],)),
                section("Shared-subject candidates", shared),
                section("Exact-text candidates", exact),
                section("Semantic candidates", semantic),
                cls._wiki_page_section(wiki_pages),
            )
        )

    @staticmethod
    def _wiki_page_section(wiki_pages: Mapping[str, WikiPageRecord]) -> str:
        lines = ["## Canonical wiki-page options"]
        lines.extend(
            f"- {token}: title={json.dumps(record.page.title, ensure_ascii=False)}; "
            f"aliases={json.dumps(list(record.page.aliases), ensure_ascii=False)}"
            for token, record in wiki_pages.items()
        )
        if len(lines) == 1:
            lines.append("- none")
        return "\n".join(lines)

    @staticmethod
    def _fact_line(token: str, fact: Fact) -> str:
        text = fact.text if len(fact.text) <= _MAX_FACT_TEXT else fact.text[:_MAX_FACT_TEXT] + "…"
        source_kinds = sorted({source.get("kind", "unknown") for source in fact.sources})
        return (
            f"- {token}: text={json.dumps(text, ensure_ascii=False)}; "
            f"kind={json.dumps(fact.kind)}; subjects={json.dumps(list(fact.subjects), ensure_ascii=False)}; "
            f"labels={json.dumps(list(fact.labels), ensure_ascii=False)}; lifecycle={fact.lifecycle}; "
            f"evidence={fact.evidence_class}; created={fact.created_at.isoformat()}; "
            f"sources={json.dumps(source_kinds)}"
        )

    def _prepare_changes(
        self,
        reviewed: Sequence[_ReviewedDecision],
    ) -> tuple[list[dict[str, Any]], int, int, tuple[str, ...]]:
        amendments: dict[str, tuple[Fact, dict[str, Any]]] = {}
        merges: dict[str, tuple[Fact, Fact, str]] = {}
        reasons: list[str] = []
        for item in reviewed:
            decision = item.decision
            if decision.outcome == "no_change":
                continue
            if decision.outcome == "amend_metadata":
                reasons.append(decision.reason)
                if decision.target_token != item.cluster.target_token:
                    raise FactMaintenanceError("metadata amendment must target the changed fact")
                target = self._token_fact(item.cluster, decision.target_token)
                metadata = self._changed_metadata(target, decision)
                if metadata:
                    amendments[target.fact_id] = (target, metadata)
                continue
            if decision.outcome == "normalize_topic":
                continue

            reasons.append(decision.reason)
            discard = self._token_fact(item.cluster, decision.discard_token)
            survivor = self._token_fact(item.cluster, decision.survivor_token)
            if item.target.fact_id not in {discard.fact_id, survivor.fact_id}:
                raise FactMaintenanceError("duplicate merge must include the changed fact")
            if discard.scope != survivor.scope:
                raise FactMaintenanceError("duplicate merge cannot cross fact scopes")
            if discard.evidence_class == "direct" and survivor.evidence_class == "inferred":
                raise FactMaintenanceError("inferred evidence cannot replace direct evidence")
            existing = merges.get(discard.fact_id)
            if existing is not None and existing[1].fact_id != survivor.fact_id:
                raise FactMaintenanceError("one fact cannot have multiple duplicate survivors")
            merges[discard.fact_id] = (discard, survivor, decision.reason)

        discarded = set(merges)
        survivors = {survivor.fact_id for _discard, survivor, _reason in merges.values()}
        if discarded.intersection(survivors):
            raise FactMaintenanceError("duplicate merge chains are not allowed in one maintenance plan")
        if set(amendments).intersection(discarded | survivors):
            raise FactMaintenanceError("one maintenance plan cannot both amend and merge a fact")

        changes: list[dict[str, Any]] = []
        for fact_id in sorted(amendments):
            target, metadata = amendments[fact_id]
            changes.append(
                {
                    "op": "amend",
                    "fact_id": target.fact_id,
                    "expected_version": target.version,
                    **metadata,
                }
            )
        for fact_id in sorted(merges):
            discard, survivor, reason = merges[fact_id]
            changes.append(
                {
                    "op": "supersede",
                    "fact_id": discard.fact_id,
                    "successor_id": survivor.fact_id,
                    "expected_version": discard.version,
                    "reason": reason,
                    "sources": self._merged_sources(discard, survivor),
                    "maintenance_merge": True,
                }
            )
        return changes, len(amendments), len(merges), tuple(dict.fromkeys(reasons))

    @staticmethod
    def _wiki_page_tokens(snapshot: WikiSnapshot) -> Mapping[str, WikiPageRecord]:
        pages = sorted(
            (
                record
                for record in snapshot.pages
                if record.page.lifecycle == "active" and record.page.page_id != "health"
            ),
            key=lambda record: (record.page.title.casefold(), record.page.title, record.page.page_id),
        )
        return MappingProxyType({f"P{index:03d}": record for index, record in enumerate(pages)})

    def _prepare_topic_normalizations(
        self,
        reviewed: Sequence[_ReviewedDecision],
        facts: Mapping[str, Fact],
        wiki_snapshot: WikiSnapshot,
        wiki_pages: Mapping[str, WikiPageRecord],
    ) -> tuple[
        list[dict[str, Any]],
        tuple[tuple[str, str, str], ...],
        tuple[str, ...],
    ]:
        selections: dict[str, WikiPageRecord] = {}
        reasons: dict[str, str] = {}
        for item in reviewed:
            decision = item.decision
            if decision.outcome != "normalize_topic":
                continue
            assert decision.old_topic is not None
            assert decision.canonical_page_token is not None
            if decision.old_topic not in item.target.subjects:
                raise FactMaintenanceError("topic normalization must use an exact subject of the changed fact")
            try:
                page = wiki_pages[decision.canonical_page_token]
            except KeyError as exc:
                raise FactMaintenanceError("topic normalization used an unknown wiki page token") from exc
            prior = selections.get(decision.old_topic)
            if prior is not None and prior.page.page_id != page.page.page_id:
                raise FactMaintenanceError("one old topic cannot select multiple canonical pages")
            selections[decision.old_topic] = page
            reasons[decision.old_topic] = (
                f"normalize exact topic {json.dumps(decision.old_topic, ensure_ascii=False)} "
                f"to canonical wiki title {json.dumps(page.page.title, ensure_ascii=False)}"
            )

        accepted: dict[str, WikiPageRecord] = {}
        pending_alias_owners: dict[str, str] = {}
        for old_topic, page in sorted(selections.items()):
            canonical_title = page.page.title
            if old_topic == canonical_title:
                continue
            owner = self._wiki.resolve_topic_name(old_topic, snapshot=wiki_snapshot)
            if owner is not None and owner.page.page_id != page.page.page_id:
                # A distinct page already owns this topic. Page reconciliation
                # belongs to Wiki Maintenance; fact subjects remain unchanged.
                continue
            accepted[old_topic] = page
            if owner is not None:
                continue
            normalized = old_topic.strip().casefold()
            pending_owner = pending_alias_owners.get(normalized)
            if pending_owner is not None and pending_owner != page.page.page_id:
                raise FactMaintenanceError("pending topic aliases would make the wiki ambiguous")
            pending_alias_owners[normalized] = page.page.page_id

        subjects_by_fact: dict[str, tuple[str, ...]] = {}
        for fact in facts.values():
            if fact.status != "active":
                continue
            replaced = tuple(
                accepted[subject].page.title if subject in accepted else subject for subject in fact.subjects
            )
            deduplicated = tuple(dict.fromkeys(replaced))
            if deduplicated != fact.subjects:
                subjects_by_fact[fact.fact_id] = deduplicated

        changes = [
            {
                "op": "amend",
                "fact_id": fact_id,
                "expected_version": facts[fact_id].version,
                "subjects": list(subjects_by_fact[fact_id]),
            }
            for fact_id in sorted(subjects_by_fact)
        ]
        used_topics = {
            subject
            for fact in facts.values()
            if fact.fact_id in subjects_by_fact
            for subject in fact.subjects
            if subject in accepted
        }
        bindings = tuple(
            (topic, accepted[topic].page.page_id, accepted[topic].page.title) for topic in sorted(used_topics)
        )
        return changes, bindings, tuple(reasons[topic] for topic in sorted(used_topics))

    @staticmethod
    def _merge_topic_changes(
        changes: Sequence[dict[str, Any]],
        topic_changes: Sequence[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        amendments: dict[str, dict[str, Any]] = {}
        merges: list[dict[str, Any]] = []
        merged_facts: set[str] = set()
        for change in changes:
            copy = dict(change)
            if copy["op"] == "amend":
                amendments[copy["fact_id"]] = copy
            else:
                merges.append(copy)
                merged_facts.add(copy["fact_id"])
                merged_facts.add(copy["successor_id"])
        for topic_change in topic_changes:
            fact_id = topic_change["fact_id"]
            if fact_id in merged_facts:
                raise FactMaintenanceError("one maintenance plan cannot both normalize and merge a fact")
            existing = amendments.get(fact_id)
            if existing is None:
                amendments[fact_id] = dict(topic_change)
                continue
            subjects = topic_change["subjects"]
            if "subjects" in existing and existing["subjects"] != subjects:
                raise FactMaintenanceError("one maintenance plan proposed conflicting subject amendments")
            existing["subjects"] = subjects
        return [
            *(amendments[fact_id] for fact_id in sorted(amendments)),
            *sorted(merges, key=lambda change: change["fact_id"]),
        ]

    @staticmethod
    def _topic_alias_reason(updates: Sequence[WikiMaintenancePageUpdate]) -> str:
        return f"Memory Maintenance canonical topic aliases across {len(updates)} page(s)"

    @staticmethod
    def _topic_alias_key(plan_id: str, updates: Sequence[WikiMaintenancePageUpdate]) -> str:
        payload = tuple((update.page_id, update.expected_version, update.title, update.aliases) for update in updates)
        fingerprint = hashlib.sha256(repr(payload).encode()).hexdigest()
        return f"{CONSUMER_ID}:topic-alias:{plan_id}:{fingerprint}"

    @staticmethod
    def _token_fact(cluster: FactMaintenancePreparedCluster, token: str | None) -> Fact:
        if token is None:
            raise FactMaintenanceError("maintenance decision omitted a fact token")
        try:
            return cluster.fact_tokens[token]
        except KeyError as exc:
            raise FactMaintenanceError("maintenance decision used an unknown fact token") from exc

    @staticmethod
    def _changed_metadata(target: Fact, decision: FactMaintenanceDecision) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if decision.kind is not None and decision.kind != target.kind:
            result["kind"] = decision.kind
        if decision.labels is not None and tuple(decision.labels) != target.labels:
            result["labels"] = decision.labels
        if decision.subjects is not None and tuple(decision.subjects) != target.subjects:
            result["subjects"] = decision.subjects
        if decision.lifecycle is not None and decision.lifecycle != target.lifecycle:
            result["lifecycle"] = decision.lifecycle
        if decision.evidence_class is not None and decision.evidence_class != target.evidence_class:
            result["evidence_class"] = decision.evidence_class
        return result

    @staticmethod
    def _merged_sources(discard: Fact, survivor: Fact) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        seen: set[str] = set()
        for source in (*discard.sources, *survivor.sources):
            value = dict(source)
            key = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            if key in seen:
                continue
            seen.add(key)
            result.append(value)
        return result

    async def _principal(self) -> FactPrincipal:
        scopes = await self._service.known_scopes()
        return FactPrincipal(owner_id=_ACTOR, readable_scopes=scopes, writable_scopes=scopes)

    @staticmethod
    def _plan_reason(
        feed: FactChangeFeed,
        amended: int,
        merged: int,
        reasons: Sequence[str],
    ) -> str:
        prefix = (
            f"memory maintenance {feed.from_revision or 'start'}..{feed.through_revision}: "
            f"{amended} metadata amendment(s), {merged} duplicate merge(s)"
        )
        detail = " | ".join(reasons)
        if not detail:
            return prefix
        return f"{prefix}; {detail}"[:4_000]
