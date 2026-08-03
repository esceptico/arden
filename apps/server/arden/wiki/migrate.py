"""One-time projection of stored title/alias wikilinks onto path links."""

import logging

from arden.revisions.models import ChangeSet, Update
from arden.wiki.service import WikiService
from arden.wiki.snapshots import index, normal, reference
from arden.wiki.wikilinks import parse_wikilinks

_logger = logging.getLogger(__name__)

MIGRATION_ACTOR = "backend"
MIGRATION_ORIGIN = "wiki.migration"


def migrate_title_links(service: WikiService) -> str | None:
    """Rewrite title/alias wikilinks to `[[path-stem|display]]` links.

    Titles left the unique-name index, so title-style links no longer resolve.
    Each link that uniquely names one active page under the old title/alias
    semantics becomes a path link keeping its visible text. Ambiguous or
    unknown names stay untouched for Wiki Maintenance to repair.
    """

    snapshot = service.snapshot()
    page_index = index(snapshot)
    by_name: dict[str, set[str]] = {}
    for record in snapshot.pages:
        if record.page.lifecycle != "active":
            continue
        for name in (record.page.title, *record.page.aliases):
            by_name.setdefault(normal(name), set()).add(record.page.page_id)

    operations = []
    for record in snapshot.pages:
        if record.page.lifecycle != "active":
            continue
        body = record.page.body
        edits: list[tuple[int, int, bytes]] = []
        for node in parse_wikilinks(body.decode("utf-8")):
            if node.page is None:
                continue
            if reference(page_index, record.page.page_id, node).target_page_id is not None:
                continue
            targets = by_name.get(normal(node.page), set())
            if len(targets) != 1:
                continue
            target = page_index.pages[next(iter(targets))]
            stem = target.resource.path[:-3]
            fragment = f"#{node.fragment}" if node.fragment else ""
            display = node.alias or node.page
            marker = "!" if node.embed else ""
            edits.append((node.start, node.end, f"{marker}[[{stem}{fragment}|{display}]]".encode()))
        if not edits:
            continue
        rewritten = bytearray()
        cursor = 0
        for start, end, replacement in sorted(edits):
            rewritten.extend(body[cursor:start])
            rewritten.extend(replacement)
            cursor = end
        rewritten.extend(body[cursor:])
        content = record.page.with_body(bytes(rewritten)).to_bytes()
        operations.append(Update(record.page.page_id, record.resource.version_id, content))

    if not operations:
        return None
    commit = service.repository.commit(
        ChangeSet(
            tuple(operations),
            MIGRATION_ACTOR,
            MIGRATION_ORIGIN,
            "rewrite title wikilinks to path links",
            f"wiki-title-links-{snapshot.head}",
            snapshot.head,
        )
    )
    _logger.info("migrated title wikilinks on %d pages", len(operations))
    return commit.commit_id
