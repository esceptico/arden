"""Readable wiki projection and small automatic chat context."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from arden.revisions.errors import RevisionConflictError
from arden.search.types import RawItem

from .models import WikiInfrastructureRole, WikiPageRecord
from .service import WikiService

WIKI_PAGE_SOURCE = "wiki_page"
WIKI_RESULT_LIMIT = 3
WIKI_CONTEXT_CHAR_BUDGET = 6_000
WIKI_RESIDENT_CHAR_BUDGET = 5_000
WIKI_MIN_QUERY_CHARS = 12
WIKI_QUERY_CHAR_LIMIT = 512


@dataclass(frozen=True, slots=True)
class WikiPageIndexState:
    """The last verified health checkpoint for the ``wiki_page`` partition."""

    wiki_head: str | None
    status: Literal["ready", "error", "not_ready"]
    detail: str | None = None

    def __post_init__(self) -> None:
        if self.wiki_head is not None and (not isinstance(self.wiki_head, str) or not self.wiki_head):
            raise ValueError("wiki_head must be a nonempty string or None")
        if self.status not in {"ready", "error", "not_ready"}:
            raise ValueError("status must be ready, error, or not_ready")
        if self.detail is not None and (not isinstance(self.detail, str) or not self.detail):
            raise ValueError("detail must be a nonempty string or None")


async def _readable_pages(wiki: WikiService) -> tuple[WikiPageRecord, ...]:
    """Read one coherent wiki snapshot, retrying one concurrent commit."""

    for attempt in range(2):
        try:
            return await asyncio.to_thread(wiki.readable_pages)
        except RevisionConflictError:
            if attempt == 0:
                continue
            raise
    raise AssertionError("unreachable")


def _role(record: WikiPageRecord) -> WikiInfrastructureRole:
    return WikiService._role(record.resource.resource_id, record.resource.path)


def _freshness(record: WikiPageRecord, fact_revision: str | None) -> str:
    generated = record.page.metadata.get("generated_from_revision")
    if isinstance(generated, str) and fact_revision is not None and generated != fact_revision:
        return "stale"
    return "current"


def _body(record: WikiPageRecord) -> str:
    return record.page.body.decode("utf-8").strip()


def _metadata(record: WikiPageRecord, fact_revision: str | None) -> dict[str, str]:
    return {
        "resource_path": record.resource.path,
        "resource_version": record.resource.version_id,
        "role": _role(record).value,
        "freshness": _freshness(record, fact_revision),
    }


class WikiPageIndexProjection:
    """One rebuildable index partition for currently readable wiki pages."""

    def __init__(
        self,
        wiki: WikiService,
        get_search_index: Callable[[], object | None],
        fact_revision: Callable[[], Awaitable[str | None]],
        on_state_change: Callable[[WikiPageIndexState], Awaitable[None]] | None = None,
    ) -> None:
        self._wiki = wiki
        self._get_search_index = get_search_index
        self._fact_revision = fact_revision
        self._on_state_change = on_state_change
        self._lock = asyncio.Lock()
        self._last_state = WikiPageIndexState(None, "not_ready")

    @property
    def last_state(self) -> WikiPageIndexState:
        """Immutable checkpoint from the latest completed sync attempt."""

        return self._last_state

    async def sync(self) -> object | None:
        changed_state: WikiPageIndexState | None = None
        result: object | None = None
        failure: Exception | None = None
        async with self._lock:
            for attempt in range(2):
                search_index = self._get_search_index()
                if search_index is None:
                    changed_state = self._set_state(
                        WikiPageIndexState(self._last_state.wiki_head, "not_ready", "search index is unavailable")
                    )
                    break
                try:
                    state = await self._sync(search_index)
                except Exception as error:
                    if attempt == 0 and self._get_search_index() is not search_index:
                        continue
                    changed_state = self._set_state(
                        WikiPageIndexState(None, "error", f"{type(error).__name__}: {error}".rstrip(": "))
                    )
                    failure = error
                    break
                if state.status != "ready":
                    if attempt == 0 and self._get_search_index() is not search_index:
                        continue
                    changed_state = self._set_state(state)
                    break
                if self._get_search_index() is search_index:
                    changed_state = self._set_state(state)
                    result = search_index
                    break
            else:
                changed_state = self._set_state(
                    WikiPageIndexState(None, "not_ready", "search index changed during wiki_page sync")
                )
        if changed_state is not None:
            await self._notify_state_change(changed_state)
        if failure is not None:
            raise failure
        return result

    async def search(self, query: str, *, limit: int) -> list:
        """Search one consistently bound index, retrying one concurrent reload."""

        for attempt in range(2):
            search_index = await self.sync()
            if search_index is None:
                return []
            try:
                results = await search_index.search(query, sources=[WIKI_PAGE_SOURCE], limit=limit)
            except Exception:
                if attempt == 0 and self._get_search_index() is not search_index:
                    continue
                raise
            if self._get_search_index() is search_index:
                return list(results)
        raise RuntimeError("search index changed during wiki_page search")

    async def _sync(self, search_index: object) -> WikiPageIndexState:
        """Index one pinned wiki head, retrying one concurrent wiki commit."""

        for attempt in range(2):
            wiki_head = self._wiki.repository.head
            fact_revision = await self._fact_revision()
            pages = await _readable_pages(self._wiki)
            now = datetime.now(UTC)
            items = [
                RawItem(
                    source=WIKI_PAGE_SOURCE,
                    source_id=record.page.page_id,
                    title=record.page.title,
                    content=_body(record),
                    created_at=now,
                    updated_at=now,
                    metadata=_metadata(record, fact_revision),
                )
                for record in pages
            ]
            indexed = await search_index.store.get_indexed_hashes(WIKI_PAGE_SOURCE)
            current_ids = {item.source_id for item in items}
            for source_id in set(indexed) - current_ids:
                await search_index.delete(WIKI_PAGE_SOURCE, source_id)
            for item in items:
                await search_index.upsert(
                    item.source,
                    item.source_id,
                    item.title,
                    item.content,
                    item.metadata,
                )
            if self._wiki.repository.head == wiki_head:
                return WikiPageIndexState(wiki_head, "ready")
            if attempt == 0:
                continue
        return WikiPageIndexState(None, "not_ready", "wiki head changed during index sync")

    def _set_state(self, state: WikiPageIndexState) -> WikiPageIndexState | None:
        if self._last_state == state:
            return None
        self._last_state = state
        return state

    async def _notify_state_change(self, state: WikiPageIndexState) -> None:
        if self._on_state_change is None:
            return
        await self._on_state_change(state)


@dataclass(frozen=True, slots=True)
class WikiContextBuilder:
    """Stable resident pages plus bounded query-time wiki excerpts."""

    wiki: WikiService
    projection: WikiPageIndexProjection
    fact_revision: Callable[[], Awaitable[str | None]]

    async def resident_context(self) -> str | None:
        fact_revision, pages = await asyncio.gather(
            self.fact_revision(),
            _readable_pages(self.wiki),
        )
        residents = [record for record in pages if _is_resident(record)]
        return _render_pages("## WIKI RESIDENT CONTEXT", residents, fact_revision, WIKI_RESIDENT_CHAR_BUDGET)

    async def page_context(self, path: str, *, title: str | None = None) -> dict[str, str] | None:
        """Return one bounded managed page for an explicitly attached Area."""

        pages = await _readable_pages(self.wiki)
        record = next((item for item in pages if item.resource.path == path), None)
        if record is None:
            return None
        body = _body(record)[:WIKI_CONTEXT_CHAR_BUDGET].rstrip()
        if not body:
            return None
        return {"title": title or record.page.title, "page": body}

    async def retrieval_context(self, user_message: str) -> str | None:
        query = _normalized_query(user_message)
        if query is None:
            return None
        fact_revision, current_pages = await asyncio.gather(
            self.fact_revision(),
            _readable_pages(self.wiki),
        )
        pages = {record.page.page_id: record for record in current_pages}
        residents = {record.page.page_id for record in pages.values() if _is_resident(record)}
        results = await self.projection.search(
            query,
            limit=WIKI_RESULT_LIMIT + len(residents),
        )
        selected = [
            pages[result.source_id]
            for result in results
            if result.source_id in pages and result.source_id not in residents
        ][:WIKI_RESULT_LIMIT]
        return _render_pages("## RELEVANT WIKI PAGES", selected, fact_revision, WIKI_CONTEXT_CHAR_BUDGET)


def _normalized_query(value: str) -> str | None:
    normalized = " ".join(value[:WIKI_QUERY_CHAR_LIMIT].casefold().split())[:WIKI_QUERY_CHAR_LIMIT]
    if len(normalized) < WIKI_MIN_QUERY_CHARS:
        return None
    return normalized


def _is_resident(record: WikiPageRecord) -> bool:
    path = record.resource.path.casefold()
    page_id = record.page.page_id.casefold()
    return path in {"readme.md", "directives.md", "me.md", "profile.md"} or page_id in {"directives", "me", "profile"}


def _render_pages(header: str, pages: list[WikiPageRecord], fact_revision: str | None, budget: int) -> str | None:
    blocks: list[str] = []
    used = len(header)
    for record in pages:
        body = _body(record)
        if not body:
            continue
        meta = _metadata(record, fact_revision)
        prefix = (
            f"### {record.page.title}\n"
            f"Role: {meta['role']}; freshness: {meta['freshness']}; "
            f"page: {record.page.page_id}; resource: {meta['resource_path']}\n\n"
        )
        remaining = budget - used - len(prefix) - 2
        if remaining <= 0:
            break
        excerpt = body[:remaining].rsplit("\n", 1)[0].rstrip() or body[:remaining]
        blocks.append(prefix + excerpt)
        used += len(blocks[-1]) + 2
    return header + "\n\n" + "\n\n".join(blocks) if blocks else None
