"""Pinned wiki rename planning, parsed-link rewrites, and redirect identity."""

from dataclasses import replace
from hashlib import sha256

from arden.revisions.errors import RevisionConflictError
from arden.revisions.models import Commit, ResourceChange, ResourceState
from arden.revisions.repository import ManagedFileRepository
from arden.wiki.constants import WIKI_HEALTH_RESOURCE_ID
from arden.wiki.exceptions import WikiValidationError
from arden.wiki.models import LinkStatus, RenamePlan, RenameRewrite, WikiPageRecord, WikiSnapshot
from arden.wiki.pages import WikiPage
from arden.wiki.pages import create_page as build_page
from arden.wiki.snapshots import (
    assert_new_names,
    index,
    parse,
    reference,
    require_ordinary_page_path,
    snapshot,
)
from arden.wiki.wikilinks import WikilinkNode, parse_wikilinks, rewrite_page_targets


def prepare_plan(
    repository: ManagedFileRepository,
    snapshot: WikiSnapshot,
    *,
    page_id: str,
    new_path: str,
    new_title: str,
    expected_version: str,
    rewrite_links: bool,
) -> RenamePlan:
    require_ordinary_page_path(new_path)
    page_index = index(snapshot)
    record = page_index.pages.get(page_id)
    if record is None or record.page.lifecycle != "active":
        raise WikiValidationError("rename requires an active, nonredirect page")
    if page_id == WIKI_HEALTH_RESOURCE_ID:
        raise WikiValidationError("health page is backend-managed")
    if record.resource.version_id != expected_version:
        raise RevisionConflictError(f"resource {page_id} changed: expected {expected_version}")
    replacement = record.page.with_title(new_title)
    assert_rename_names(snapshot, record, replacement, new_path)
    redirect_page_id = redirect_id(page_id, record.resource.path)
    assert_redirect_resource_available(repository, redirect_page_id, snapshot.head)

    rewrites: list[RenameRewrite] = []
    moved_content = replacement.to_bytes()
    link_count = 0
    page_count = 0
    if rewrite_links:
        for source in snapshot.pages:
            if source.page.lifecycle != "active":
                continue
            references = tuple(
                item
                for node in parse_wikilinks(source.page.body.decode("utf-8"))
                if (item := reference(page_index, source.page.page_id, node)).target_page_id == page_id
                and item.status is LinkStatus.RESOLVED
            )
            if not references:
                continue
            link_count += len(references)
            page_count += 1
            targets = {item.node: rename_target(item.node, new_path) for item in references}
            rewritten_body = rewrite_page_targets(source.page.body.decode("utf-8"), targets).encode("utf-8")
            if source.page.page_id == page_id:
                moved_content = replace(replacement, body=rewritten_body).to_bytes()
                continue
            prefix_size = len(source.content) - len(source.page.body)
            rewrites.append(
                RenameRewrite(
                    resource_id=source.resource.resource_id,
                    expected_version=source.resource.version_id,
                    content=source.content[:prefix_size] + rewritten_body,
                    replacements=references,
                )
            )

    return RenamePlan(
        base_head=snapshot.head,
        page_id=page_id,
        expected_version=expected_version,
        old_path=record.resource.path,
        new_path=new_path,
        old_title=record.page.title,
        new_title=new_title,
        moved_content=moved_content,
        redirect_page_id=redirect_page_id,
        rewrite_links=rewrite_links,
        link_count=link_count,
        page_count=page_count,
        rewrites=tuple(rewrites),
        idempotency_key=rename_key(snapshot.head, page_id, expected_version, new_path, new_title, rewrite_links),
    )


def assert_rename_names(
    snapshot: WikiSnapshot,
    current: WikiPageRecord,
    replacement: WikiPage,
    new_path: str,
) -> None:
    others = tuple(record for record in snapshot.pages if record.page.page_id != current.page.page_id)
    assert_new_names(WikiSnapshot(snapshot.head, others), replacement, new_path)
    redirect = build_page(
        page_id=redirect_id(current.page.page_id, current.resource.path),
        title=current.page.title,
        lifecycle="redirect",
        redirect_to=current.page.page_id,
    )
    assert_new_names(
        WikiSnapshot(
            snapshot.head,
            (*others, WikiPageRecord(replace(current.resource, path=new_path), replacement, replacement.to_bytes())),
        ),
        redirect,
        current.resource.path,
    )


def assert_redirect_resource_available(
    repository: ManagedFileRepository,
    resource_id: str,
    head: str | None,
) -> None:
    try:
        repository.get(resource_id, at=head)
    except KeyError:
        return
    raise WikiValidationError(f"redirect resource already exists: {resource_id}")


def rename_target(node: WikilinkNode, new_path: str) -> str:
    if node.page is None:
        raise WikiValidationError("rename rewrite requires a page link target")
    return new_path if node.page.casefold().endswith(".md") else new_path[:-3]


def redirect_id(page_id: str, old_path: str) -> str:
    return f"redirect-{sha256(f'{page_id}:{old_path}'.encode()).hexdigest()}"


def rename_key(
    base_head: str | None,
    page_id: str,
    expected_version: str,
    new_path: str,
    new_title: str,
    rewrite_links: bool,
) -> str:
    return (
        "wiki-"
        + sha256(
            repr(("rename", base_head, page_id, expected_version, new_path, new_title, rewrite_links)).encode()
        ).hexdigest()
    )


def is_exact_generated_rewrite(
    repository: ManagedFileRepository,
    commit: Commit,
    source_change: ResourceChange,
    page_id: str,
) -> bool:
    """Whether one commit changes a generated region only by exact moved-link rewrites."""

    before = source_change.before
    after = source_change.after
    if (
        commit.parent_id is None
        or before is None
        or after is None
        or before.state is not ResourceState.ACTIVE
        or after.state is not ResourceState.ACTIVE
    ):
        return False
    prior = snapshot(repository, at=commit.parent_id)
    page_index = index(prior)
    source = page_index.pages.get(page_id)
    if source is None or source.resource != before:
        return False

    moved: dict[str, str] = {}
    for change in commit.changes:
        if change.action != "move" or change.before is None or change.after is None:
            continue
        if change.before.state is not ResourceState.ACTIVE or change.after.state is not ResourceState.ACTIVE:
            continue
        target = page_index.pages.get(change.before.resource_id)
        if target is None or target.resource != change.before:
            return False
        moved[change.before.resource_id] = change.after.path
    if not moved:
        return False

    references = tuple(
        item
        for node in parse_wikilinks(source.page.body.decode("utf-8"))
        if (item := reference(page_index, page_id, node)).status is LinkStatus.RESOLVED and item.target_page_id in moved
    )
    if not references:
        return False
    targets = {item.node: rename_target(item.node, moved[item.target_page_id]) for item in references}
    if not targets:
        return False
    rewritten_body = rewrite_page_targets(source.page.body.decode("utf-8"), targets).encode("utf-8")
    if source_change.action == "move":
        after_page = parse(after, repository.read_version(after))
        expected = after_page.with_body(rewritten_body).to_bytes()
    else:
        prefix_size = len(source.content) - len(source.page.body)
        expected = source.content[:prefix_size] + rewritten_body
    return repository.read_version(after) == expected
