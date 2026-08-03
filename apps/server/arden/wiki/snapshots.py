"""Pinned wiki snapshot indexing, name resolution, and link validation."""

from dataclasses import dataclass

from arden.revisions.errors import RevisionConflictError
from arden.revisions.models import ResourceVersion
from arden.revisions.repository import ManagedFileRepository
from arden.wiki.constants import README_FILENAME, WIKI_HEALTH_PATH, WIKI_HEALTH_RESOURCE_ID
from arden.wiki.exceptions import WikiAmbiguityError, WikiValidationError
from arden.wiki.models import LinkReference, LinkStatus, WikiLinkReport, WikiPageRecord, WikiSnapshot
from arden.wiki.pages import PageValidationError, WikiPage, parse_page
from arden.wiki.wikilinks import WikilinkNode, parse_wikilinks


@dataclass(frozen=True, slots=True)
class SnapshotIndex:
    names: dict[str, tuple[str, ...]]
    pages: dict[str, WikiPageRecord]


def snapshot(
    repository: ManagedFileRepository,
    *,
    at: str | None = None,
) -> WikiSnapshot:
    head = repository.head if at is None else at
    records: list[WikiPageRecord] = []
    for resource in repository.list_resources(at=head):
        if not resource.path.endswith(".md"):
            continue
        content = repository.read_version(resource)
        records.append(WikiPageRecord(resource, parse(resource, content), content))
    result = WikiSnapshot(head=head, pages=tuple(sorted(records, key=lambda record: record.page.page_id)))
    validate_redirects(index(result))
    return result


def parse(resource: ResourceVersion, content: bytes) -> WikiPage:
    try:
        return parse_page(content, expected_page_id=resource.resource_id)
    except PageValidationError as error:
        raise WikiValidationError(f"invalid wiki page {resource.path}: {error}") from error


def index(snapshot: WikiSnapshot) -> SnapshotIndex:
    pages = {record.page.page_id: record for record in snapshot.pages}
    names: dict[str, set[str]] = {}
    for record in snapshot.pages:
        for name in names_for_page(record.page, record.resource.path):
            names.setdefault(normal(name), set()).add(record.page.page_id)
    return SnapshotIndex(names={name: tuple(sorted(ids)) for name, ids in names.items()}, pages=pages)


def resolve_topic_name(snapshot: WikiSnapshot, name: str) -> WikiPageRecord | None:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("topic name must be a nonempty string")
    wanted = normal(name)
    matches = tuple(
        record
        for record in snapshot.pages
        if wanted in {normal(record.page.title), *(normal(alias) for alias in record.page.aliases)}
    )
    if not matches:
        return None
    if len(matches) != 1:
        raise WikiAmbiguityError(f"ambiguous wiki topic name {name!r}")
    return matches[0]


def link_report(snapshot: WikiSnapshot, page_id: str) -> WikiLinkReport:
    page_index = index(snapshot)
    record = page_index.pages.get(page_id)
    if record is None or record.page.lifecycle != "active":
        raise KeyError(f"unknown active wiki page: {page_id}")
    outgoing = tuple(reference(page_index, page_id, node) for node in parse_wikilinks(record.page.body.decode("utf-8")))
    backlinks: list[LinkReference] = []
    for source in snapshot.pages:
        for node in parse_wikilinks(source.page.body.decode("utf-8")):
            item = reference(page_index, source.page.page_id, node)
            if item.target_page_id == page_id:
                backlinks.append(item)
    return WikiLinkReport(snapshot.head, record, snapshot.pages, outgoing, tuple(backlinks))


def reference(page_index: SnapshotIndex, source_page_id: str, node: WikilinkNode) -> LinkReference:
    if node.page is None:
        return LinkReference(source_page_id, node, LinkStatus.UNRESOLVED)
    candidates = page_index.names.get(normal(node.page), ())
    if not candidates:
        return LinkReference(source_page_id, node, LinkStatus.UNRESOLVED)
    target = follow_redirect(page_index, candidates[0])
    if target is None:
        return LinkReference(source_page_id, node, LinkStatus.UNRESOLVED)
    return LinkReference(source_page_id, node, LinkStatus.RESOLVED, target)


def follow_redirect(page_index: SnapshotIndex, page_id: str) -> str | None:
    seen: set[str] = set()
    while True:
        if page_id in seen:
            return None
        seen.add(page_id)
        page = page_index.pages[page_id].page
        if page.lifecycle == "active":
            return page_id
        if page.redirect_to not in page_index.pages:
            return None
        page_id = page.redirect_to


def validate_redirects(page_index: SnapshotIndex) -> None:
    for page_id, record in page_index.pages.items():
        if record.page.lifecycle == "redirect" and follow_redirect(page_index, page_id) is None:
            raise WikiValidationError(f"redirect {page_id} has a missing target or cycle")


def validate_prospective(snapshot: WikiSnapshot, pages: tuple[WikiPageRecord, ...]) -> None:
    validate_redirects(index(WikiSnapshot(snapshot.head, pages)))


def names_for_page(page: WikiPage, path: str) -> tuple[str, ...]:
    """Link-addressable names: the path and its stem. Titles are display-only."""

    if page.page_id == WIKI_HEALTH_RESOURCE_ID and path == WIKI_HEALTH_PATH:
        return (path,)
    return (path, path[:-3] if path.endswith(".md") else path)


def names_for_target(path: str) -> tuple[str, ...]:
    return (path, path[:-3] if path.endswith(".md") else path)


def normal(value: str) -> str:
    return value.strip().casefold()


def require_markdown_path(path: str) -> None:
    if (
        not isinstance(path, str)
        or not path.endswith(".md")
        or path.startswith("/")
        or "\\" in path
        or "\x00" in path
        or "//" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        raise WikiValidationError("wiki pages must use a .md path")


def require_ordinary_page_path(path: str) -> None:
    """Reject nested directory contracts from public page operations."""

    require_markdown_path(path)
    if "/" in path and path.rsplit("/", 1)[-1].casefold() == README_FILENAME.casefold():
        raise WikiValidationError("nested README.md paths are reserved for automatic directory contracts")


def require_head(actual: str | None, requested: str | None) -> None:
    if requested is not None and actual != requested:
        raise RevisionConflictError(f"current head changed: expected {requested!r}, found {actual!r}")


def assert_new_names(snapshot: WikiSnapshot, page: WikiPage, path: str) -> None:
    for record in snapshot.pages:
        if record.resource.path.casefold() == path.casefold():
            raise WikiAmbiguityError(f"wiki path already exists: {path}")
    names = {normal(name) for name in names_for_page(page, path)}
    existing = index(snapshot).names
    for name in names:
        if name in existing:
            raise WikiAmbiguityError(f"wiki name already exists: {name}")
