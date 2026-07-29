"""Wiki change evidence, provenance projections, and Maintenance restoration."""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from hashlib import sha256

from arden.revisions.errors import CorruptRepositoryError, RevisionConflictError
from arden.revisions.models import Commit, ResourceVersion, StorageReport
from arden.revisions.repository import ManagedFileRepository
from arden.wiki.constants import README_FILENAME, WIKI_HEALTH_RESOURCE_ID, WIKI_MAINTENANCE_ORIGIN
from arden.wiki.exceptions import WikiMaintenanceEvidenceLimitError, WikiSnapshotChangedError, WikiValidationError
from arden.wiki.models import (
    LinkReference,
    LinkStatus,
    WikiChangeCommit,
    WikiChangesReport,
    WikiChangeWarning,
    WikiInfrastructureRole,
    WikiMaintenanceCommit,
    WikiMaintenanceDetails,
    WikiMaintenanceFeed,
    WikiMaintenancePageUpdate,
    WikiPageRecord,
    WikiPageRevision,
    WikiResourceChange,
    WikiSnapshot,
)
from arden.wiki.provenance import parse_fact_provenance
from arden.wiki.snapshots import SnapshotIndex, index, parse, reference
from arden.wiki.wikilinks import WikilinkNode, parse_wikilinks

_STORAGE_INSPECTION_BYTES = 50 * 1024 * 1024
_STORAGE_NEEDS_ATTENTION_BYTES = 100 * 1024 * 1024
_MAINTENANCE_CURRENT_SCAN_BYTES = 256 * 1024
_MAINTENANCE_CURRENT_SCAN_TOTAL_BYTES = 8 * 1024 * 1024
_MAINTENANCE_CURRENT_EDITABLE_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True)
class MaintenanceCurrentContext:
    record: WikiPageRecord
    nodes: tuple[WikilinkNode, ...]


def maintenance_feed(
    repository: ManagedFileRepository,
    watermark: str | None,
) -> WikiMaintenanceFeed:
    head = repository.current_revision
    commits_newest_first = history_since(repository, head, watermark)
    commits = tuple(
        WikiMaintenanceCommit(
            commit_id=commit.commit_id,
            parent_id=commit.parent_id,
            actor=commit.actor,
            origin=commit.origin,
            reason=commit.reason,
            timestamp=commit.timestamp,
            changes=commit.changes,
        )
        for commit in reversed(commits_newest_first)
    )
    if repository.current_revision != head:
        raise WikiSnapshotChangedError("wiki changed while building a maintenance feed")
    return WikiMaintenanceFeed(watermark=watermark, through_revision=head, commits=commits)


def history_since(
    repository: ManagedFileRepository,
    head: str | None,
    watermark: str | None,
) -> tuple[Commit, ...]:
    if watermark is not None and (not isinstance(watermark, str) or not watermark):
        raise ValueError("watermark must be a nonempty commit ID or None")
    if head is None:
        if watermark is not None:
            raise KeyError(f"wiki watermark is not reachable from pinned head: {watermark}")
        return ()
    if watermark == head:
        return ()
    try:
        return repository.history(start=head, stop_before=watermark)
    except KeyError as error:
        raise KeyError(f"wiki watermark is not reachable from pinned head: {watermark}") from error


def changes_since(
    repository: ManagedFileRepository,
    watermark: str | None,
    *,
    include_diffs: bool,
    diff_char_limit: int | None,
) -> WikiChangesReport:
    if not isinstance(include_diffs, bool):
        raise TypeError("include_diffs must be a bool")
    if diff_char_limit is not None:
        if isinstance(diff_char_limit, bool) or not isinstance(diff_char_limit, int) or diff_char_limit <= 0:
            raise ValueError("diff_char_limit must be a positive integer or None")
        if not include_diffs:
            raise ValueError("diff_char_limit requires include_diffs=True")
    head = repository.head
    commits_newest_first = history_since(repository, head, watermark)
    current_records, current_warnings = maintenance_snapshot(repository, head)
    current_index = index(WikiSnapshot(head, current_records), strict_names=False)
    current_links = maintenance_links(current_records, current_index)
    warnings = list(current_warnings)
    warnings.extend(link_warnings(current_records, current_links))
    integrity = repository.integrity_report()
    storage = repository.storage_report()
    warnings.extend(WikiChangeWarning(issue.code, issue.target, issue.detail) for issue in integrity.issues)
    warnings.extend(storage_warnings(storage, str(repository.history_root)))
    reports = tuple(
        WikiChangeCommit(
            commit_id=commit.commit_id,
            parent_id=commit.parent_id,
            actor=commit.actor,
            origin=commit.origin,
            reason=commit.reason,
            timestamp=commit.timestamp,
            changes=(),
        )
        if not include_diffs
        else detail_commit(repository, commit, current_links, warnings, diff_char_limit=diff_char_limit)
        for commit in reversed(commits_newest_first)
    )
    if repository.head != head:
        raise WikiSnapshotChangedError("wiki changed while building a change feed")
    return WikiChangesReport(
        watermark=watermark,
        through_revision=head,
        commits=reports,
        warnings=dedupe_warnings(warnings),
        integrity=integrity,
        storage=storage,
        current_records=current_records,
    )


def maintenance_details(
    repository: ManagedFileRepository,
    commit: WikiMaintenanceCommit,
    *,
    through_revision: str,
    diff_char_limit: int,
    diff_byte_budget: int,
) -> WikiMaintenanceDetails:
    if not isinstance(commit, WikiMaintenanceCommit):
        raise TypeError("commit must be a WikiMaintenanceCommit")
    if not isinstance(through_revision, str) or not through_revision:
        raise ValueError("through_revision must be a nonempty commit ID")
    if isinstance(diff_char_limit, bool) or not isinstance(diff_char_limit, int) or diff_char_limit <= 1:
        raise ValueError("diff_char_limit must be an integer greater than one")
    if isinstance(diff_byte_budget, bool) or not isinstance(diff_byte_budget, int) or diff_byte_budget <= 0:
        raise ValueError("diff_byte_budget must be a positive integer")
    if repository.head != through_revision:
        raise WikiSnapshotChangedError("wiki changed before loading commit details")
    remaining = diff_byte_budget
    diffs: dict[str, tuple[str, bool]] = {}
    revisions: dict[str, WikiPageRevision] = {}
    markdown_changes = tuple(change for change in commit.changes if is_markdown_change(change.before, change.after))
    for change in markdown_changes:
        resource = change.after or change.before
        if resource is None:
            raise CorruptRepositoryError(
                f"commit {commit.commit_id} contains a wiki change without before or after version"
            )
        if remaining <= 0:
            raise maintenance_evidence_limit(
                commit,
                resource.resource_id,
                section="commit evidence",
                actual_bytes=diff_byte_budget + 1,
                limit_bytes=diff_byte_budget,
                actual_bytes_at_least=True,
                suffix=b"budget-exhausted",
            )
        reserved = 0
        reserved_sizes: dict[str, int] = {}
        for section, revision in (("before page", change.before), ("after page", change.after)):
            if revision is None or revision.version_id in revisions:
                continue
            size = repository.content_size(revision)
            if size > remaining - reserved:
                raise maintenance_evidence_limit(
                    commit,
                    revision.resource_id,
                    section=section,
                    actual_bytes=diff_byte_budget - remaining + reserved + size,
                    limit_bytes=diff_byte_budget,
                    actual_bytes_at_least=False,
                    suffix=revision.blob_id.encode(),
                )
            reserved += size
            reserved_sizes[revision.version_id] = size
        page_limit = min(diff_char_limit, remaining - reserved + 1)
        page = repository.diff_versions_page(change.before, change.after, limit=page_limit, source_byte_limit=remaining)
        actual = len(page.unified_diff.encode("utf-8", errors="surrogateescape"))
        if page.has_more:
            shared_budget_exhausted = page_limit < diff_char_limit
            raise maintenance_evidence_limit(
                commit,
                resource.resource_id,
                section="diff",
                actual_bytes=diff_byte_budget - remaining + reserved + actual if shared_budget_exhausted else actual,
                limit_bytes=diff_byte_budget if shared_budget_exhausted else diff_char_limit - 1,
                actual_bytes_at_least=True,
                suffix=page.unified_diff.encode("utf-8", errors="surrogateescape"),
            )
        if actual > remaining - reserved:
            raise maintenance_evidence_limit(
                commit,
                resource.resource_id,
                section="diff",
                actual_bytes=diff_byte_budget - remaining + reserved + actual,
                limit_bytes=diff_byte_budget,
                actual_bytes_at_least=False,
                suffix=page.unified_diff.encode("utf-8", errors="surrogateescape"),
            )
        remaining -= actual
        diffs[resource.resource_id] = (page.unified_diff, True)
        for section, revision in (("before page", change.before), ("after page", change.after)):
            if revision is None or revision.version_id in revisions:
                continue
            reserved_size = reserved_sizes[revision.version_id]
            if reserved_size > remaining:
                raise maintenance_evidence_limit(
                    commit,
                    revision.resource_id,
                    section=section,
                    actual_bytes=diff_byte_budget - remaining + reserved_size,
                    limit_bytes=diff_byte_budget,
                    actual_bytes_at_least=False,
                    suffix=revision.blob_id.encode(),
                )
            content = repository.read_version(revision)
            remaining -= reserved_size
            revisions[revision.version_id] = revision_from_content(revision, content)
    current_records, current_links, current_warnings, _remaining = maintenance_current_context(
        repository,
        through_revision,
        changed_page_ids={(change.after or change.before).resource_id for change in markdown_changes},
        cached_revisions=revisions,
        remaining=remaining,
        limit=diff_byte_budget,
        commit=commit,
    )
    warnings = list(current_warnings)
    report = detail_commit(
        repository,
        commit,
        current_links,
        warnings,
        diff_char_limit=None,
        precomputed_diffs=diffs,
        precomputed_revisions=revisions,
    )
    if repository.head != through_revision:
        raise WikiSnapshotChangedError("wiki changed while loading commit details")
    return WikiMaintenanceDetails(through_revision, report, dedupe_warnings(warnings), current_records)


def detail_commit(
    repository: ManagedFileRepository,
    commit: Commit | WikiMaintenanceCommit,
    current_links: Mapping[str, tuple[tuple[LinkReference, ...], tuple[LinkReference, ...]]],
    warnings: list[WikiChangeWarning],
    *,
    diff_char_limit: int | None,
    precomputed_diffs: Mapping[str, tuple[str, bool]] | None = None,
    precomputed_revisions: Mapping[str, WikiPageRevision] | None = None,
) -> WikiChangeCommit:
    markdown_changes = tuple(change for change in commit.changes if is_markdown_change(change.before, change.after))
    markdown_ids = tuple((change.after or change.before).resource_id for change in markdown_changes)
    if precomputed_diffs is not None:
        diffs = dict(precomputed_diffs)
    elif diff_char_limit is None:
        diffs = {
            item.resource_id: (item.unified_diff, True)
            for item in repository.diff(commit.parent_id, commit.commit_id, resource_ids=markdown_ids)
        }
    else:
        diffs = {
            resource_id: (page.unified_diff, not page.has_more)
            for resource_id in markdown_ids
            for page in (repository.diff_page(commit.parent_id, commit.commit_id, resource_id, limit=diff_char_limit),)
        }
    changes: list[WikiResourceChange] = []
    for change in markdown_changes:
        resource = change.after or change.before
        if resource is None:
            raise CorruptRepositoryError(
                f"commit {commit.commit_id} contains a wiki change without before or after version"
            )
        before = revision_for(repository, change.before, precomputed_revisions)
        after = revision_for(repository, change.after, precomputed_revisions)
        for revision in (before, after):
            if revision is not None:
                if revision.validation_error is not None:
                    warnings.append(
                        WikiChangeWarning("invalid_page", revision.resource.path, revision.validation_error)
                    )
                warnings.extend(revision.provenance_warnings)
        outgoing, backlinks = current_links.get(resource.resource_id, ((), ()))
        diff, complete = diffs.get(resource.resource_id, ("", True))
        changes.append(
            WikiResourceChange(change.action, resource.resource_id, before, after, diff, outgoing, backlinks, complete)
        )
    return WikiChangeCommit(
        commit.commit_id, commit.parent_id, commit.actor, commit.origin, commit.reason, commit.timestamp, tuple(changes)
    )


def maintenance_evidence_limit(
    commit: WikiMaintenanceCommit,
    resource_id: str,
    *,
    section: str,
    actual_bytes: int,
    limit_bytes: int,
    actual_bytes_at_least: bool,
    suffix: bytes,
) -> WikiMaintenanceEvidenceLimitError:
    fingerprint = sha256(
        b"\0".join(
            (
                commit.commit_id.encode(),
                resource_id.encode(),
                section.encode(),
                str(actual_bytes).encode(),
                str(limit_bytes).encode(),
                suffix,
            )
        )
    ).hexdigest()
    return WikiMaintenanceEvidenceLimitError(
        commit_id=commit.commit_id,
        resource_id=resource_id,
        section=section,
        actual_bytes=actual_bytes,
        limit_bytes=limit_bytes,
        actual_bytes_at_least=actual_bytes_at_least,
        fingerprint=fingerprint,
    )


def maintenance_snapshot(
    repository: ManagedFileRepository,
    head: str | None,
    *,
    cached_revisions: Mapping[str, WikiPageRevision] | None = None,
) -> tuple[tuple[WikiPageRecord, ...], tuple[WikiChangeWarning, ...]]:
    records: list[WikiPageRecord] = []
    warnings: list[WikiChangeWarning] = []
    for resource in repository.list_resources(at=head):
        if not resource.path.endswith(".md"):
            continue
        revision = None if cached_revisions is None else cached_revisions.get(resource.version_id)
        if revision is None:
            revision = revision_from_content(resource, repository.read_version(resource))
        if revision.page is None:
            warnings.append(
                WikiChangeWarning("invalid_page", resource.path, revision.validation_error or "invalid page")
            )
            continue
        warnings.extend(revision.provenance_warnings)
        records.append(WikiPageRecord(resource, revision.page, revision.content))
    return tuple(sorted(records, key=lambda record: record.page.page_id)), tuple(warnings)


def maintenance_current_context(
    repository: ManagedFileRepository,
    head: str,
    *,
    changed_page_ids: set[str],
    cached_revisions: Mapping[str, WikiPageRevision],
    remaining: int,
    limit: int,
    commit: WikiMaintenanceCommit,
) -> tuple[
    tuple[WikiPageRecord, ...],
    dict[str, tuple[tuple[LinkReference, ...], tuple[LinkReference, ...]]],
    tuple[WikiChangeWarning, ...],
    int,
]:
    contexts: list[MaintenanceCurrentContext] = []
    warnings: list[WikiChangeWarning] = []
    scanned = 0
    for resource in repository.list_resources(at=head):
        if not resource.path.endswith(".md"):
            continue
        size = repository.content_size(resource)
        if size > _MAINTENANCE_CURRENT_SCAN_BYTES:
            raise maintenance_evidence_limit(
                commit,
                resource.resource_id,
                section="current page",
                actual_bytes=size,
                limit_bytes=_MAINTENANCE_CURRENT_SCAN_BYTES,
                actual_bytes_at_least=False,
                suffix=f"{head}:{resource.blob_id}".encode(),
            )
        if size > _MAINTENANCE_CURRENT_SCAN_TOTAL_BYTES - scanned:
            raise maintenance_evidence_limit(
                commit,
                resource.resource_id,
                section="current wiki link context",
                actual_bytes=scanned + size,
                limit_bytes=_MAINTENANCE_CURRENT_SCAN_TOTAL_BYTES,
                actual_bytes_at_least=False,
                suffix=f"{head}:{resource.blob_id}".encode(),
            )
        scanned += size
        revision = cached_revisions.get(resource.version_id)
        if revision is None:
            revision = revision_from_content(resource, repository.read_version(resource))
        if revision.page is None:
            warnings.append(
                WikiChangeWarning("invalid_page", resource.path, revision.validation_error or "invalid page")
            )
            continue
        warnings.extend(revision.provenance_warnings)
        contexts.append(
            MaintenanceCurrentContext(
                record=WikiPageRecord(resource, replace(revision.page, body=b""), b""),
                nodes=parse_wikilinks(revision.page.body.decode("utf-8")),
            )
        )
    context_by_id = {context.record.page.page_id: context for context in contexts}
    metadata_records = tuple(sorted((context.record for context in contexts), key=lambda record: record.page.page_id))
    current_links = maintenance_links_from_nodes(
        tuple((context.record.page.page_id, context.nodes) for context in contexts),
        index(WikiSnapshot(head, metadata_records), strict_names=False),
    )
    warnings.extend(link_warnings(metadata_records, current_links))
    page_ids = set(changed_page_ids)
    for page_id in changed_page_ids:
        outgoing, backlinks = current_links.get(page_id, ((), ()))
        page_ids.update(item.target_page_id for item in outgoing if item.target_page_id)
        page_ids.update(item.source_page_id for item in backlinks)
    records: list[WikiPageRecord] = []
    for page_id in sorted(page_ids):
        context = context_by_id.get(page_id)
        if context is None:
            continue
        resource = context.record.resource
        size = repository.content_size(resource)
        if size > _MAINTENANCE_CURRENT_EDITABLE_BYTES:
            raise maintenance_evidence_limit(
                commit,
                resource.resource_id,
                section="current editable page",
                actual_bytes=size,
                limit_bytes=_MAINTENANCE_CURRENT_EDITABLE_BYTES,
                actual_bytes_at_least=False,
                suffix=f"{head}:{resource.blob_id}".encode(),
            )
        if size > remaining:
            raise maintenance_evidence_limit(
                commit,
                resource.resource_id,
                section="current editable page",
                actual_bytes=limit - remaining + size,
                limit_bytes=limit,
                actual_bytes_at_least=False,
                suffix=f"{head}:{resource.blob_id}".encode(),
            )
        revision = cached_revisions.get(resource.version_id)
        if revision is None:
            revision = revision_from_content(resource, repository.read_version(resource))
        if revision.page is None:
            raise WikiValidationError(f"current page changed while preparing evidence: {resource.path}")
        remaining -= size
        records.append(WikiPageRecord(resource, revision.page, revision.content))
    return tuple(records), current_links, tuple(warnings), remaining


def maintenance_links(
    records: tuple[WikiPageRecord, ...],
    page_index: SnapshotIndex,
) -> dict[str, tuple[tuple[LinkReference, ...], tuple[LinkReference, ...]]]:
    return maintenance_links_from_nodes(
        tuple((record.page.page_id, parse_wikilinks(record.page.body.decode("utf-8"))) for record in records),
        page_index,
    )


def maintenance_links_from_nodes(
    pages: Sequence[tuple[str, Sequence[WikilinkNode]]],
    page_index: SnapshotIndex,
) -> dict[str, tuple[tuple[LinkReference, ...], tuple[LinkReference, ...]]]:
    outgoing: dict[str, tuple[LinkReference, ...]] = {}
    backlinks: dict[str, list[LinkReference]] = {page_id: [] for page_id, _nodes in pages}
    for page_id, nodes in pages:
        references = tuple(reference(page_index, page_id, node) for node in nodes)
        outgoing[page_id] = references
        for item in references:
            if item.target_page_id is not None:
                backlinks.setdefault(item.target_page_id, []).append(item)
    return {page_id: (outgoing[page_id], tuple(backlinks[page_id])) for page_id, _nodes in pages}


def link_warnings(
    records: tuple[WikiPageRecord, ...],
    links: Mapping[str, tuple[tuple[LinkReference, ...], tuple[LinkReference, ...]]],
) -> tuple[WikiChangeWarning, ...]:
    warnings: list[WikiChangeWarning] = []
    for record in records:
        outgoing, _backlinks = links[record.page.page_id]
        for item in outgoing:
            if item.status is LinkStatus.RESOLVED:
                continue
            target = item.node.page or "#fragment"
            warnings.append(
                WikiChangeWarning(
                    f"{item.status.value}_link",
                    record.resource.path,
                    f"{target} ({', '.join(item.candidates)})" if item.candidates else target,
                )
            )
    return tuple(warnings)


def revision_for(
    repository: ManagedFileRepository,
    resource: ResourceVersion | None,
    cached_revisions: Mapping[str, WikiPageRevision] | None,
) -> WikiPageRevision | None:
    if resource is None:
        return None
    if cached_revisions is not None and (cached := cached_revisions.get(resource.version_id)) is not None:
        return cached
    return revision_from_content(resource, repository.read_version(resource))


def revision_from_content(resource: ResourceVersion, content: bytes) -> WikiPageRevision:
    try:
        page = parse(resource, content)
    except WikiValidationError as error:
        return WikiPageRevision(
            resource, content, None, str(error), role(resource.resource_id, resource.path), None, ()
        )
    provenance = parse_fact_provenance(page.metadata, resource.path)
    return WikiPageRevision(
        resource,
        content,
        page,
        None,
        role(resource.resource_id, resource.path),
        provenance.generated_from_revision,
        provenance.citations,
        provenance.warnings,
    )


def role(resource_id: str, path: str) -> WikiInfrastructureRole:
    if resource_id == "me" or path == "me.md":
        return WikiInfrastructureRole.ME
    if resource_id == "active-work" or path == "active-work.md":
        return WikiInfrastructureRole.ACTIVE_WORK
    if path.rsplit("/", 1)[-1] == README_FILENAME:
        return WikiInfrastructureRole.README
    if path.startswith("daily/"):
        return WikiInfrastructureRole.DAILY
    if path.startswith("insights/"):
        return WikiInfrastructureRole.INSIGHT
    return WikiInfrastructureRole.COMMON


def is_markdown_change(before: ResourceVersion | None, after: ResourceVersion | None) -> bool:
    return (before is not None and before.path.endswith(".md")) or (after is not None and after.path.endswith(".md"))


def dedupe_warnings(warnings: list[WikiChangeWarning]) -> tuple[WikiChangeWarning, ...]:
    seen: set[tuple[str, str, str]] = set()
    result: list[WikiChangeWarning] = []
    for warning in warnings:
        key = (warning.code, warning.target, warning.evidence)
        if key not in seen:
            seen.add(key)
            result.append(warning)
    return tuple(result)


def storage_warnings(storage: StorageReport, target: str) -> tuple[WikiChangeWarning, ...]:
    if storage.total_bytes >= _STORAGE_NEEDS_ATTENTION_BYTES:
        return (WikiChangeWarning("storage_needs_attention", target, str(storage.total_bytes)),)
    if storage.total_bytes >= _STORAGE_INSPECTION_BYTES:
        return (WikiChangeWarning("storage_inspection", target, str(storage.total_bytes)),)
    return ()


def restore_maintenance_change(
    repository: ManagedFileRepository,
    read_page: Callable[[str], WikiPageRecord],
    apply_updates: Callable[..., str],
    make_key: Callable[..., str],
    page_id: str,
    maintenance_commit_id: str,
    *,
    expected_version: str,
    expected_head: str,
) -> tuple[WikiPageRecord, str]:
    if repository.head != expected_head:
        raise RevisionConflictError(f"current head changed: expected {expected_head!r}, found {repository.head!r}")
    from arden.wiki.snapshots import snapshot

    current = index(snapshot(repository, strict_names=True, at=expected_head)).pages.get(page_id)
    if current is None or current.page.lifecycle != "active":
        raise KeyError(f"unknown active wiki page: {page_id}")
    if page_id == WIKI_HEALTH_RESOURCE_ID:
        raise WikiValidationError("health page is backend-managed")
    if current.resource.version_id != expected_version:
        raise RevisionConflictError(f"resource {page_id} changed: expected {expected_version}")
    page_history = repository.history(resource_id=page_id, start=expected_head)
    commit = next((candidate for candidate in page_history if candidate.commit_id == maintenance_commit_id), None)
    if commit is None:
        raise KeyError(f"Maintenance commit is not a reachable change for page {page_id}: {maintenance_commit_id}")
    if commit.origin != WIKI_MAINTENANCE_ORIGIN:
        raise WikiValidationError(f"commit is not a Wiki Maintenance change: {maintenance_commit_id}")
    matching = tuple(
        change
        for change in commit.changes
        if (change.after or change.before) is not None and (change.after or change.before).resource_id == page_id
    )
    if len(matching) != 1:
        raise WikiValidationError(
            f"commit must contain exactly one page update with before and after versions: {page_id}"
        )
    change = matching[0]
    if change.action != "update" or change.before is None or change.after is None:
        raise WikiValidationError(
            f"commit must contain exactly one page update with before and after versions: {page_id}"
        )
    if not page_history or page_history[0].commit_id != maintenance_commit_id:
        raise RevisionConflictError(f"resource {page_id} changed after Maintenance commit {maintenance_commit_id}")
    if current.resource.version_id != change.after.version_id:
        raise RevisionConflictError(f"resource {page_id} changed after Maintenance commit {maintenance_commit_id}")
    before = parse(change.before, repository.read_version(change.before))
    reason = f"restore Maintenance commit {maintenance_commit_id}"
    restored_commit_id = apply_updates(
        (WikiMaintenancePageUpdate(page_id, expected_version, before.title, before.aliases, before.body),),
        base_head=expected_head,
        reason=reason,
        idempotency_key=make_key(
            "maintenance-restore", page_id, maintenance_commit_id, expected_version, expected_head
        ),
        actor="user:desktop",
        origin="desktop.restore",
    )
    if restored_commit_id == expected_head:
        raise WikiValidationError(f"Maintenance commit has no restorable page fields: {maintenance_commit_id}")
    return read_page(page_id), restored_commit_id
