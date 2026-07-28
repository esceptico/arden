"""Provisional cross-domain insights from one pinned canonical fact snapshot."""

import asyncio
import unicodedata
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

from arden.constants import BUILTIN_MEMORY_DREAM_ID
from arden.memory.facts.ledger import FactLedger
from arden.memory.facts.models import Fact
from arden.wiki.models import GeneratedPageTarget, WikiPageRecord
from arden.wiki.service import WikiService

ORIGIN = "memory.dream"
ACTOR = "Dream"
MIN_EVIDENCE_FACTS = 6
MAX_EVIDENCE_FACTS = 120
MAX_INSIGHTS = 5
MAX_INSIGHT_CHARS = 600


class FactDreamError(RuntimeError):
    """Dream could not safely publish its provisional projection."""


@dataclass(frozen=True, slots=True)
class DreamEvidence:
    token: str
    fact_id: str
    version: str
    text: str
    subjects: tuple[str, ...]
    source_summary: str


@dataclass(frozen=True, slots=True)
class DreamInsight:
    claim: str
    fact_tokens: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _PublishedInsight:
    claim: str
    fact_ids: tuple[str, ...]


class FactDreamRenderer(Protocol):
    async def render(
        self,
        *,
        month: date,
        evidence: tuple[DreamEvidence, ...],
    ) -> tuple[DreamInsight, ...]: ...


@dataclass(frozen=True, slots=True)
class FactDreamResult:
    fact_revision: str | None
    insight_count: int = 0
    published: bool = False
    empty: bool = False


class FactDream:
    """Publish a monthly, provisional insight page without creating new facts."""

    def __init__(
        self,
        ledger: FactLedger,
        wiki: WikiService,
        renderer: FactDreamRenderer,
        *,
        timezone_name: str = "UTC",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._ledger = ledger
        self._wiki = wiki
        self._renderer = renderer
        self._timezone = ZoneInfo(timezone_name)
        self._clock = clock or (lambda: datetime.now(UTC))

    async def run(self) -> FactDreamResult:
        revision = await asyncio.to_thread(lambda: self._ledger.revision)
        if revision is None:
            return FactDreamResult(None, empty=True)

        facts = await asyncio.to_thread(self._ledger.facts_at, revision)
        snapshot = await asyncio.to_thread(self._wiki.snapshot)
        month = self._month()
        page_id = f"insights-{month:%Y-%m}"
        existing = next((record for record in snapshot.pages if record.page.page_id == page_id), None)
        selected = _select_evidence(facts)

        insights: tuple[_PublishedInsight, ...] = ()
        citations: tuple[Fact, ...] = ()
        if _has_cross_domain_evidence(selected):
            evidence = tuple(
                DreamEvidence(
                    token=f"F{index:03d}",
                    fact_id=fact.fact_id,
                    version=fact.version,
                    text=fact.text,
                    subjects=fact.subjects,
                    source_summary=_source_summary(fact),
                )
                for index, fact in enumerate(selected, 1)
            )
            try:
                rendered = await self._renderer.render(month=month, evidence=evidence)
            except Exception as exc:
                raise FactDreamError("Dream renderer failed") from exc
            insights, citations = _validate_insights(rendered, evidence, facts)

        if not insights and existing is None:
            return FactDreamResult(revision, empty=True)

        target = _target(month, existing, insights, citations)
        commit = await asyncio.to_thread(
            self._validate_and_publish,
            revision,
            facts,
            snapshot.head,
            target,
        )
        return FactDreamResult(
            revision,
            insight_count=len(insights),
            published=commit is not None,
            empty=commit is None and not insights,
        )

    def _month(self) -> date:
        now = self._clock()
        if now.tzinfo is None:
            raise FactDreamError("Dream clock must return a timezone-aware datetime")
        local = now.astimezone(self._timezone)
        return date(local.year, local.month, 1)

    def _validate_and_publish(
        self,
        revision: str,
        pinned: Mapping[str, Fact],
        wiki_head: str | None,
        target: GeneratedPageTarget,
    ):
        with self._ledger.locked_facts() as current:
            if {fact_id: fact.version for fact_id, fact in current.items()} != {
                fact_id: fact.version for fact_id, fact in pinned.items()
            }:
                raise FactDreamError("canonical facts changed before Dream publication")
            return self._wiki.publish_generated(
                (target,),
                source_revision=revision,
                base_head=wiki_head,
                actor=ACTOR,
                origin=ORIGIN,
                reason=f"publish {target.path} Dream insights",
            )


def _eligible(fact: Fact) -> bool:
    return (
        fact.status == "active"
        and fact.certainty == "confirmed"
        and fact.evidence_class == "direct"
        and bool(fact.subjects)
    )


def _select_evidence(facts: Mapping[str, Fact]) -> tuple[Fact, ...]:
    eligible = sorted(
        (fact for fact in facts.values() if _eligible(fact)),
        key=lambda fact: (fact.created_at, fact.fact_id),
    )
    return tuple(eligible[-MAX_EVIDENCE_FACTS:])


def _normal(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _domains(facts: tuple[Fact, ...]) -> set[str]:
    return {_normal(subject) for fact in facts for subject in fact.subjects if _normal(subject)}


def _has_cross_domain_evidence(facts: tuple[Fact, ...]) -> bool:
    return len(facts) >= MIN_EVIDENCE_FACTS and len(_domains(facts)) >= 2


def _validate_insights(
    value: object,
    evidence: tuple[DreamEvidence, ...],
    facts: Mapping[str, Fact],
) -> tuple[tuple[_PublishedInsight, ...], tuple[Fact, ...]]:
    if not isinstance(value, tuple) or not all(isinstance(item, DreamInsight) for item in value):
        raise FactDreamError("Dream renderer must return a tuple of DreamInsight values")
    if len(value) > MAX_INSIGHTS:
        raise FactDreamError(f"Dream renderer returned more than {MAX_INSIGHTS} insights")

    tokens = {item.token: item for item in evidence}
    claims: set[str] = set()
    cited: dict[str, Fact] = {}
    published: list[_PublishedInsight] = []
    for insight in value:
        claim = insight.claim
        if (
            not isinstance(claim, str)
            or not claim
            or claim != claim.strip()
            or "\n" in claim
            or "\0" in claim
            or len(claim) > MAX_INSIGHT_CHARS
            or "fact:" in claim.casefold()
        ):
            raise FactDreamError("Dream insight claim is invalid")
        normalized_claim = _normal(claim)
        if normalized_claim in claims:
            raise FactDreamError("Dream renderer returned duplicate insights")
        claims.add(normalized_claim)

        fact_tokens = insight.fact_tokens
        if (
            not isinstance(fact_tokens, tuple)
            or len(fact_tokens) < 2
            or not all(isinstance(token, str) and token for token in fact_tokens)
            or len(set(fact_tokens)) != len(fact_tokens)
        ):
            raise FactDreamError("each Dream insight needs at least two distinct fact tokens")
        unknown = set(fact_tokens) - set(tokens)
        if unknown:
            raise FactDreamError(f"Dream renderer cited unknown token: {min(unknown)}")
        insight_facts = tuple(facts[tokens[token].fact_id] for token in fact_tokens)
        if len({fact.fact_id for fact in insight_facts}) < 2 or len(_domains(insight_facts)) < 2:
            raise FactDreamError("each Dream insight must cite two facts from distinct subjects")
        for fact in insight_facts:
            cited.setdefault(fact.fact_id, fact)
        published.append(_PublishedInsight(claim, tuple(fact.fact_id for fact in insight_facts)))

    return tuple(published), tuple(cited.values())


def _source_summary(fact: Fact) -> str:
    sources = sorted(
        {
            f"{source.get('kind', 'source')}:{source.get('ref', '')}"
            for source in fact.sources
            if source.get("kind") or source.get("ref")
        }
    )
    return ", ".join(sources[:3]) or "canonical fact"


def _target(
    month: date,
    existing: WikiPageRecord | None,
    insights: tuple[_PublishedInsight, ...],
    citations: tuple[Fact, ...],
) -> GeneratedPageTarget:
    page_id = f"insights-{month:%Y-%m}"
    title = f"Insights {month:%Y-%m}"
    path = f"insights/{month:%Y-%m}.md"
    aliases: tuple[str, ...] = ()
    metadata: dict[str, object] = {}
    if existing is not None:
        if existing.page.lifecycle != "active":
            raise FactDreamError("Dream insight page is not active")
        owner = existing.page.metadata.get("producer_automation_id")
        if owner != BUILTIN_MEMORY_DREAM_ID:
            raise FactDreamError("existing Dream insight page is not owned by Dream")
        title = existing.page.title
        path = existing.resource.path
        aliases = existing.page.aliases
        metadata.update(
            {
                key: value
                for key, value in existing.page.metadata.items()
                if key not in {"generated_from_revision", "fact_citations"}
            }
        )
    metadata.update(
        {
            "producer_automation_id": BUILTIN_MEMORY_DREAM_ID,
            "provisional": True,
            "fact_citations": [{"fact_id": fact.fact_id, "version": fact.version} for fact in citations],
        }
    )
    generated = _render_markdown(insights)
    return GeneratedPageTarget(page_id, path, title, aliases, generated, metadata)


def _render_markdown(insights: tuple[_PublishedInsight, ...]) -> bytes:
    if not insights:
        return b""
    lines = [
        "> [!warning] Provisional",
        "> Dream-generated connections are suggestions, not canonical facts.",
        "",
    ]
    for insight in insights:
        evidence = ", ".join(f"fact:{_markdown_escape(fact_id)}" for fact_id in insight.fact_ids)
        lines.append(f"- {insight.claim} _(evidence: {evidence})_")
    return ("\n".join(lines).rstrip() + "\n").encode()


def _markdown_escape(value: str) -> str:
    escaped = value.replace("\\", "\\\\")
    for character in "`*_{}[]<>()#+-.!|":
        escaped = escaped.replace(character, f"\\{character}")
    return escaped
