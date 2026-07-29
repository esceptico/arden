"""Canonical fact-ledger projection into Synthesis-owned wiki regions."""

import asyncio
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from hashlib import sha256
from typing import Protocol
from zoneinfo import ZoneInfo

from arden.memory.facts.consumer_store import FactConsumerStore
from arden.memory.facts.ledger import FactLedger
from arden.memory.facts.models import Fact, FactChangeFeed
from arden.revisions.models import ResourceState
from arden.wiki.constants import AUTOMATIONS_PATH_PREFIX, README_FILENAME
from arden.wiki.models import GeneratedPageTarget, WikiPageRecord
from arden.wiki.pages import extract_generated_region, extract_user_body, parse_page
from arden.wiki.service import WikiService

CONSUMER_ID = "memory.synthesis"
NO_ADDITIONAL_FACTS = "<!-- synthesis: supplied facts already exist in authored content -->"
_CITATION_GROUP = re.compile(r"\(fact:(F\d{3,})(?:\s*,\s*fact:(F\d{3,}))*\)")
_TOKEN = re.compile(r"F\d{3,}")


class FactSynthesisError(RuntimeError):
    """Synthesis could not safely publish a projection."""


@dataclass(frozen=True, slots=True)
class SynthesisFact:
    token: str
    text: str
    source_summary: str


class FactSynthesisRenderer(Protocol):
    """Renders cited Markdown from opaque fact tokens only."""

    async def render(self, *, title: str, facts: tuple[SynthesisFact, ...], existing_body: str) -> str: ...


@dataclass(frozen=True, slots=True)
class FactSynthesisResult:
    through_revision: str | None
    published_pages: int = 0
    advanced: bool = False
    skipped_archived: int = 0
    skipped_under_threshold: int = 0
    empty: bool = False
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class _Route:
    kind: str
    key: str
    title: str
    page_id: str
    path: str
    metadata: Mapping[str, object]
    aliases: tuple[str, ...] = ()
    keys: tuple[tuple[str, str], ...] = ()


class FactSynthesis:
    """Small, feed-pinned fact-to-wiki projection service."""

    def __init__(
        self,
        ledger: FactLedger,
        consumers: FactConsumerStore,
        wiki: WikiService,
        renderer: FactSynthesisRenderer,
        *,
        timezone_name: str = "UTC",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._ledger = ledger
        self._consumers = consumers
        self._wiki = wiki
        self._renderer = renderer
        self._timezone = ZoneInfo(timezone_name)
        self._clock = clock or _utc_now

    async def run(self) -> FactSynthesisResult:
        watermark = await self._consumers.get(CONSUMER_ID)
        feed = await asyncio.to_thread(self._ledger.changes_since, None if watermark is None else watermark.revision)
        if feed.through_revision is None:
            return FactSynthesisResult(feed.through_revision, empty=True)

        today = self._today()
        reason = _reason(feed) if feed.events else _daily_reason(feed.through_revision, today)
        if feed.events and await asyncio.to_thread(self._has_trusted_publication, feed, reason):
            await self._consumers.advance(CONSUMER_ID, feed=feed, ledger=self._ledger)
            return FactSynthesisResult(feed.through_revision, advanced=True, replayed=True)

        facts = await asyncio.to_thread(self._ledger.facts_at, feed.through_revision)
        prior_facts = await asyncio.to_thread(self._ledger.facts_at, feed.from_revision)
        targets, skipped_archived, skipped_under_threshold, base_head = await asyncio.to_thread(
            self._targets,
            feed,
            facts,
            prior_facts,
            today,
        )
        if not targets:
            if feed.events:
                await self._consumers.advance(CONSUMER_ID, feed=feed, ledger=self._ledger)
            return FactSynthesisResult(
                feed.through_revision,
                advanced=bool(feed.events),
                skipped_archived=skipped_archived,
                skipped_under_threshold=skipped_under_threshold,
                empty=not feed.events,
            )
        rendered = await self._render_targets(targets, facts, base_head)
        commit = await asyncio.to_thread(
            self._validate_and_publish,
            feed,
            rendered,
            facts,
            base_head,
            reason,
        )
        if feed.events:
            await self._consumers.advance(CONSUMER_ID, feed=feed, ledger=self._ledger)
        return FactSynthesisResult(
            feed.through_revision,
            published_pages=(
                0
                if commit is None
                else sum(
                    change.after is not None and change.after.path.rsplit("/", 1)[-1] != README_FILENAME
                    for change in commit.changes
                )
            ),
            advanced=bool(feed.events),
            skipped_archived=skipped_archived,
            skipped_under_threshold=skipped_under_threshold,
            empty=not feed.events and commit is None,
        )

    def _today(self) -> date:
        now = self._clock()
        if now.tzinfo is None:
            raise FactSynthesisError("synthesis clock must return a timezone-aware datetime")
        return now.astimezone(self._timezone).date()

    def _targets(
        self,
        feed: FactChangeFeed,
        facts: Mapping[str, Fact],
        prior_facts: Mapping[str, Fact] | None = None,
        today: date | None = None,
    ) -> tuple[tuple[_Route, ...], int, int, str | None]:
        snapshot = self._wiki.snapshot()
        pages = snapshot.pages
        active = tuple(record for record in pages if record.page.lifecycle == "active")
        archived, archived_projects = self._archived_authority(snapshot.head)
        eligible = tuple(fact for fact in facts.values() if _eligible(fact))
        grouped: dict[tuple[str, str], list[Fact]] = {}
        for fact in eligible:
            route = _route_for(fact)
            grouped.setdefault(route, []).append(fact)
        fallback = _citation_fallbacks(active, eligible, prior_facts or {})

        routes: dict[tuple[str, str], _Route] = {}
        skipped_archived = 0
        skipped_under_threshold = 0
        all_keys = {_route_from_tuple(item) for item in feed.affected_routes}
        if feed.events:
            all_keys.add(("active-work", ""))  # lifecycle is outside affected_routes
        if today is not None and (
            feed.events or not any(record.resource.path == f"daily/{today.isoformat()}.md" for record in active)
        ):
            all_keys.add(("daily", today.isoformat()))
        for key in sorted(all_keys):
            assigned = grouped.get(("active-work", ""), ()) if key[0] == "daily" else grouped.get(key, ())
            route, why = self._resolve_route(
                key,
                assigned,
                active,
                pages,
                archived,
                archived_projects,
                fallback.get(key),
            )
            if route is not None:
                routes[key] = route if key[0] == "daily" else _at_key(route, key)
            elif why == "archived":
                skipped_archived += 1
            elif why == "threshold":
                skipped_under_threshold += 1

        # A citation is an enduring claim on its page even if its name changed.
        affected_ids = set(feed.affected_fact_ids)
        cited_pages = {record.page.page_id for record in active if affected_ids & _citation_ids(record)}
        page_routes: dict[str, _Route] = {}
        for route in routes.values():
            prior = page_routes.get(route.page_id)
            page_routes[route.page_id] = route if prior is None else _merge_route(prior, route)
        for page_id in cited_pages:
            if page_id in page_routes:
                continue
            record = next(record for record in active if record.page.page_id == page_id)
            page_routes[page_id] = _without_keys(_existing_route(record))

        # A page can be created after its matching facts were already
        # synthesized. Reconcile those declared topics on every run so the
        # six-hour backstop covers a missed creation notification too.
        for key in grouped:
            if key[0] != "topic":
                continue
            record = _match_topic(active, key[1])
            if record is None or not _needs_reconciliation(record, facts):
                continue
            route = _at_key(_existing_route(record), key)
            existing = page_routes.get(route.page_id)
            page_routes[route.page_id] = route if existing is None else _merge_route(existing, route)

        # Retain only routes affected by this feed, while each selected route later
        # receives every currently eligible fact assigned to it.
        return (
            tuple(sorted(page_routes.values(), key=lambda item: item.page_id)),
            skipped_archived,
            skipped_under_threshold,
            snapshot.head,
        )

    def _resolve_route(
        self,
        key: tuple[str, str],
        facts: Sequence[Fact],
        active: Sequence[WikiPageRecord],
        pages: Sequence[WikiPageRecord],
        archived: set[str],
        archived_projects: set[str],
        fallback: WikiPageRecord | None,
    ) -> tuple[_Route | None, str | None]:
        kind, value = key
        if kind == "active-work":
            record = next((item for item in active if item.page.page_id == "active-work"), None)
            if record is None and not facts:
                return None, None
            if record:
                return _at_key(_existing_route(record), key), None
            candidate = _fixed("active-work", "active-work.md", "Active Work")
            if _archived_match(archived, candidate):
                return None, "archived"
            return candidate, None
        if kind == "daily":
            path = f"daily/{value}.md"
            record = next((item for item in active if item.resource.path == path), None)
            if record is None and not facts:
                return None, None
            if record is not None:
                metadata = {
                    name: item
                    for name, item in record.page.metadata.items()
                    if name not in {"generated_from_revision", "fact_citations"}
                }
                metadata.update({"date": value, "timezone": self._timezone.key})
                return (
                    _Route(
                        "daily",
                        value,
                        record.page.title,
                        record.page.page_id,
                        path,
                        metadata,
                        record.page.aliases,
                        (("active-work", ""),),
                    ),
                    None,
                )
            candidate = _Route(
                "daily",
                value,
                value,
                f"daily-{value}",
                path,
                {"date": value, "timezone": self._timezone.key},
                keys=(("active-work", ""),),
            )
            if _archived_match(archived, candidate):
                return None, "archived"
            return candidate, None
        if kind == "me":
            record = next((item for item in active if item.page.page_id == "me"), None)
            if record:
                return _at_key(_existing_route(record), key), None
            if not facts:
                return None, None
            candidate = _fixed("me", "me.md", "Me")
            if _archived_match(archived, candidate):
                return None, "archived"
            return candidate, None
        if kind == "project":
            scope_key = _display_scope_key(facts) if facts else None
            scope = {"kind": "project", "key": scope_key or value}
            records = []
            for item in active:
                stored = item.page.metadata.get("scope")
                if stored == scope:
                    records.append(item)
            if len(records) > 1:
                raise FactSynthesisError(f"multiple project pages have scope {value!r}")
            record = records[0] if records else None
            if record:
                return _at_key(_existing_route(record), key), None
            if value in archived_projects:
                return None, "archived"
            if not facts:
                return None, None
            assert scope_key is not None
            title = _display_subject(facts)
            identity = _identity("project", value)
            candidate = _Route(
                "project",
                value,
                title,
                f"project-{identity}",
                f"projects/{_slug(title)}-{identity[:8]}.md",
                {"scope": scope},
            )
            if _archived_match(archived, candidate):
                return None, "archived"
            return candidate, None
        if kind != "topic":
            raise FactSynthesisError(f"unknown synthesis route: {kind}")
        record = _match_topic(pages, value)
        if record:
            return _at_key(_existing_route(record), key), None
        if fallback:
            return _at_key(_existing_route(fallback), key), None
        if not facts:
            return None, None
        title = _display_subject(facts)
        identity = _identity("topic", value)
        candidate = _Route(
            "topic",
            value,
            title,
            f"topic-{identity}",
            f"topics/{_slug(title)}-{identity[:8]}.md",
            {},
        )
        if _archived_match(archived, candidate):
            return None, "archived"
        if len(facts) < 2:
            return None, "threshold"
        return candidate, None

    async def _render_targets(
        self,
        routes: tuple[_Route, ...],
        facts: Mapping[str, Fact],
        base_head: str | None,
    ) -> tuple[GeneratedPageTarget, ...]:
        rendered: list[GeneratedPageTarget] = []
        for route in routes:
            assigned = tuple(
                sorted(
                    (fact for fact in facts.values() if _eligible(fact) and _route_for(fact) in route.keys),
                    key=lambda item: item.fact_id,
                )
            )
            if assigned:
                tokens = {f"F{index:03d}": fact for index, fact in enumerate(assigned, 1)}
                prompt = tuple(SynthesisFact(token, fact.text, _source_summary(fact)) for token, fact in tokens.items())
                try:
                    prose = await self._renderer.render(
                        title=route.title,
                        facts=prompt,
                        existing_body=self._existing_user_body(route.page_id, base_head),
                    )
                except Exception as exc:
                    raise FactSynthesisError(f"renderer failed for {route.page_id}") from exc
                generated, citations = _validate_and_humanize(prose, tokens)
            else:
                generated, citations = b"", ()
            metadata = {
                **route.metadata,
                "fact_citations": [{"fact_id": fact_id, "version": version} for fact_id, version in citations],
            }
            rendered.append(
                GeneratedPageTarget(route.page_id, route.path, route.title, route.aliases, generated, metadata)
            )
        return tuple(rendered)

    def _existing_user_body(self, page_id: str, base_head: str | None) -> str:
        if base_head is None:
            return ""
        try:
            record = self._wiki.read_page(page_id, at=base_head)
        except KeyError:
            return ""
        return extract_user_body(record.content, expected_page_id=page_id).decode()

    def _validate_and_publish(
        self,
        feed: FactChangeFeed,
        targets: tuple[GeneratedPageTarget, ...],
        facts: Mapping[str, Fact],
        base_head: str | None,
        reason: str,
    ):
        assert feed.through_revision is not None
        with self._ledger.locked_facts() as current:
            self._validate_live(feed, targets, facts, current)
            return self._wiki.publish_generated(
                targets,
                source_revision=feed.through_revision,
                base_head=base_head,
                actor="Synthesis",
                origin=CONSUMER_ID,
                reason=reason,
            )

    @staticmethod
    def _validate_live(
        feed: FactChangeFeed,
        targets: tuple[GeneratedPageTarget, ...],
        facts: Mapping[str, Fact],
        current: Mapping[str, Fact],
    ) -> None:
        pinned = dict(facts)
        cited_versions = {
            (citation["fact_id"], citation["version"])
            for target in targets
            for citation in target.metadata["fact_citations"]
        }
        cited = {fact_id for fact_id, _version in cited_versions}
        affected = set(feed.affected_fact_ids)
        for fact_id in affected | cited:
            label = "affected fact" if fact_id in affected else "fact"
            try:
                live = current[fact_id]
                expected = pinned[fact_id]
            except KeyError as exc:
                raise FactSynthesisError(f"{label} changed before publication: {fact_id}") from exc
            if (
                live.version != expected.version
                or live.status != expected.status
                or live.certainty != expected.certainty
            ):
                raise FactSynthesisError(f"{label} changed before publication: {fact_id}")
            if fact_id in cited and (not _eligible(live) or (fact_id, expected.version) not in cited_versions):
                raise FactSynthesisError(f"{label} changed before publication: {fact_id}")

    def _has_trusted_publication(self, feed: FactChangeFeed, reason: str) -> bool:
        through = feed.through_revision
        if through is None:
            return False
        for commit in self._wiki.repository.history():
            if commit.actor != "Synthesis" or commit.origin != CONSUMER_ID or commit.reason != reason:
                continue
            if not commit.changes:
                continue
            valid = True
            published_page = False
            for change in commit.changes:
                after = change.after
                if after is None or after.state is not ResourceState.ACTIVE or not after.path.endswith(".md"):
                    valid = False
                    break
                if after.path.rsplit("/", 1)[-1] == README_FILENAME:
                    continue
                page = self._wiki.repository.read(after.resource_id, at=commit.commit_id)
                if (
                    parse_page(page, expected_page_id=after.resource_id).metadata.get("generated_from_revision")
                    != through
                ):
                    valid = False
                    break
                published_page = True
            if valid and published_page:
                return True
        return False

    def _archived_authority(self, head: str | None) -> tuple[set[str], set[str]]:
        if head is None:
            return set(), set()
        names: set[str] = set()
        projects: set[str] = set()
        for resource in self._wiki.repository.list_resources(at=head, include_archived=True):
            if resource.state is not ResourceState.ARCHIVED or not resource.path.endswith(".md"):
                continue
            page = parse_page(
                self._wiki.repository.read(resource.resource_id, at=head),
                expected_page_id=resource.resource_id,
            )
            names.update(
                _names(record_path=resource.path, title=page.title, aliases=page.aliases, page_id=page.page_id)
            )
            scope = page.metadata.get("scope")
            if isinstance(scope, Mapping) and scope.get("kind") == "project" and isinstance(scope.get("key"), str):
                projects.add(scope["key"])
        return names, projects


def _eligible(fact: Fact) -> bool:
    return fact.status == "active" and fact.certainty == "confirmed"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _route_for(fact: Fact) -> tuple[str, str]:
    if fact.lifecycle == "temporary":
        return "active-work", ""
    if fact.scope["kind"] == "project" and fact.scope["key"]:
        return "project", str(fact.scope["key"])
    primary = fact.subjects[0].strip()
    return ("me", "") if _normal(primary) == "me" else ("topic", _normal(primary))


def _route_from_tuple(value: tuple[str, str | None, str]) -> tuple[str, str]:
    scope_kind, scope_key, subject = value
    if scope_kind == "project" and scope_key:
        return "project", scope_key
    return ("me", "") if _normal(subject) == "me" else ("topic", _normal(subject))


def _fixed(page_id: str, path: str, title: str) -> _Route:
    return _Route(page_id, "", title, page_id, path, {}, keys=((page_id, ""),))


def _existing_route(record: WikiPageRecord) -> _Route:
    metadata = {
        key: value
        for key, value in record.page.metadata.items()
        if key not in {"generated_from_revision", "fact_citations"}
    }
    scope = metadata.get("scope")
    if isinstance(scope, Mapping) and scope.get("kind") == "project" and isinstance(scope.get("key"), str):
        return _Route(
            "project",
            scope["key"],
            record.page.title,
            record.page.page_id,
            record.resource.path,
            metadata,
            record.page.aliases,
            (("project", scope["key"]),),
        )
    if record.page.page_id == "active-work":
        return _Route(
            "active-work",
            "",
            record.page.title,
            record.page.page_id,
            record.resource.path,
            metadata,
            record.page.aliases,
            (("active-work", ""),),
        )
    if record.page.page_id == "me":
        return _Route(
            "me",
            "",
            record.page.title,
            record.page.page_id,
            record.resource.path,
            metadata,
            record.page.aliases,
            (("me", ""),),
        )
    return _Route(
        "topic",
        record.page.title,
        record.page.title,
        record.page.page_id,
        record.resource.path,
        metadata,
        record.page.aliases,
        (("topic", record.page.title),),
    )


def _at_key(route: _Route, key: tuple[str, str]) -> _Route:
    return _Route(key[0], key[1], route.title, route.page_id, route.path, route.metadata, route.aliases, (key,))


def _without_keys(route: _Route) -> _Route:
    return _Route(
        route.kind,
        route.key,
        route.title,
        route.page_id,
        route.path,
        route.metadata,
        route.aliases,
    )


def _merge_route(first: _Route, second: _Route) -> _Route:
    return _Route(
        first.kind,
        first.key,
        first.title,
        first.page_id,
        first.path,
        first.metadata,
        first.aliases,
        tuple(dict.fromkeys((*first.keys, *second.keys))),
    )


def _match_topic(pages: Sequence[WikiPageRecord], subject: str) -> WikiPageRecord | None:
    wanted = _normal(subject)
    by_id = {record.page.page_id: record for record in pages}
    for record in pages:
        if record.resource.path.rsplit("/", 1)[-1] == README_FILENAME:
            continue
        if record.resource.path.startswith(AUTOMATIONS_PATH_PREFIX):
            continue
        if record.page.metadata.get("producer_automation_id") is not None:
            continue
        names = {_normal(record.page.title), *(_normal(alias) for alias in record.page.aliases)}
        if wanted not in names:
            continue
        if record.page.lifecycle == "active":
            return record
        if record.page.lifecycle == "redirect" and record.page.redirect_to in by_id:
            target = by_id[record.page.redirect_to]
            if (
                target.resource.path.rsplit("/", 1)[-1] != README_FILENAME
                and not target.resource.path.startswith(AUTOMATIONS_PATH_PREFIX)
                and (target.page.metadata.get("producer_automation_id") is None)
            ):
                return target
    return None


def _names(*, record_path: str, title: str, aliases: Sequence[str], page_id: str) -> set[str]:
    stem = record_path[:-3] if record_path.endswith(".md") else record_path
    return {_normal(item) for item in (title, *aliases, record_path, stem, page_id) if item.strip()}


def _archived_match(archived: set[str], route: _Route) -> bool:
    return bool(
        archived
        & _names(
            record_path=route.path,
            title=route.title,
            aliases=route.aliases,
            page_id=route.page_id,
        )
    )


def _identity(kind: str, value: str) -> str:
    identity = value if kind == "project" else _normal(value)
    return sha256(f"{kind}\0{identity}".encode()).hexdigest()[:12]


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", _normal(value)).strip("-")
    return cleaned or "topic"


def _normal(value: str) -> str:
    return value.strip().casefold()


def _display_subject(facts: Sequence[Fact]) -> str:
    return min((fact.subjects[0].strip() for fact in facts), key=lambda item: (_normal(item), item))


def _display_scope_key(facts: Sequence[Fact]) -> str:
    return min(
        (str(fact.scope["key"]) for fact in facts if fact.scope["key"]),
        key=lambda item: item,
    )


def _citation_fallbacks(
    pages: Sequence[WikiPageRecord],
    facts: Sequence[Fact],
    prior_facts: Mapping[str, Fact],
) -> dict[tuple[str, str], WikiPageRecord]:
    by_fact: dict[str, list[WikiPageRecord]] = {}
    current = {fact.fact_id: fact for fact in facts}
    for page in pages:
        for fact_id, version in _citation_entries(page):
            fact = current.get(fact_id)
            prior = prior_facts.get(fact_id)
            if (
                fact is None
                or prior is None
                or _route_for(fact) != _route_for(prior)
                or version not in {fact.version, prior.version}
            ):
                continue
            by_fact.setdefault(fact_id, []).append(page)

    by_route: dict[tuple[str, str], set[str]] = {}
    records = {page.page.page_id: page for page in pages}
    for fact in facts:
        candidates = {page.page.page_id for page in by_fact.get(fact.fact_id, ())}
        if len(candidates) == 1:
            by_route.setdefault(_route_for(fact), set()).update(candidates)
    return {route: records[next(iter(page_ids))] for route, page_ids in by_route.items() if len(page_ids) == 1}


def _source_summary(fact: Fact) -> str:
    if fact.evidence_class == "inferred":
        return "inferred"
    kinds = {str(source.get("kind", "chat_message")) for source in fact.sources}
    if "chat_message" in kinds or "chat" in kinds:
        return "chat"
    if "gmail" in kinds or "email" in kinds:
        return "email"
    return sorted(kinds)[0] if kinds else "chat"


def _validate_and_humanize(prose: object, tokens: Mapping[str, Fact]) -> tuple[bytes, tuple[tuple[str, str], ...]]:
    if not isinstance(prose, str):
        raise FactSynthesisError("renderer must return a string")
    if prose.strip() == NO_ADDITIONAL_FACTS:
        return (NO_ADDITIONAL_FACTS + "\n").encode(), ()
    cited: list[str] = []
    for line in prose.splitlines():
        if not line.strip():
            continue
        if line.lstrip().startswith("#"):
            if "fact:" in line or _TOKEN.search(line):
                raise FactSynthesisError("headings must not contain opaque fact tokens")
            continue
        groups = list(_CITATION_GROUP.finditer(line))
        if not groups:
            raise FactSynthesisError("every claim line needs a valid fact citation")
        scrubbed = _CITATION_GROUP.sub("", line)
        if "fact:" in scrubbed or (_TOKEN.search(scrubbed) and "fact:" in line):
            raise FactSynthesisError("malformed fact citation")
        for group in groups:
            cited.extend(_TOKEN.findall(group.group(0)))
    if not cited:
        raise FactSynthesisError("renderer returned no fact citations")
    unknown = set(cited) - set(tokens)
    if unknown:
        raise FactSynthesisError(f"renderer cited unknown token: {min(unknown)}")

    def replace(match: re.Match[str]) -> str:
        labels: list[str] = []
        for token in _TOKEN.findall(match.group(0)):
            label = (
                "inferred" if tokens[token].evidence_class == "inferred" else f"from {_source_summary(tokens[token])}"
            )
            if label not in labels:
                labels.append(label)
        return (
            "("
            + " + ".join(label.removeprefix("from ") if index else label for index, label in enumerate(labels))
            + ")"
        )

    human = _CITATION_GROUP.sub(replace, prose).strip()
    if not human:
        raise FactSynthesisError("renderer returned blank prose")
    citations = tuple((tokens[token].fact_id, tokens[token].version) for token in dict.fromkeys(cited))
    return (human + "\n").encode(), citations


def _citation_ids(record: WikiPageRecord) -> set[str]:
    return {fact_id for fact_id, _version in _citation_entries(record)}


def _needs_reconciliation(record: WikiPageRecord, facts: Mapping[str, Fact]) -> bool:
    generated = extract_generated_region(record.content, expected_page_id=record.page.page_id)
    citations = _citation_entries(record)
    if generated is not None and generated.strip() == NO_ADDITIONAL_FACTS.encode():
        return bool(citations)
    if not generated:
        return True
    if not citations:
        return True
    return any(
        fact_id not in facts or facts[fact_id].version != version or not _eligible(facts[fact_id])
        for fact_id, version in citations
    )


def _citation_entries(record: WikiPageRecord) -> tuple[tuple[str, str], ...]:
    value = record.page.metadata.get("fact_citations")
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    result: list[tuple[str, str]] = []
    for item in value:
        if (
            isinstance(item, Mapping)
            and isinstance(item.get("fact_id"), str)
            and isinstance(item.get("version"), str)
            and re.fullmatch(r"[0-9a-f]{64}", item["version"])
        ):
            result.append((item["fact_id"], item["version"]))
    return tuple(result)


def _reason(feed: FactChangeFeed) -> str:
    return f"synthesize facts {feed.from_revision or 'start'}..{feed.through_revision}"


def _daily_reason(revision: str, day: date) -> str:
    return f"synthesize daily {day.isoformat()} at {revision}"
