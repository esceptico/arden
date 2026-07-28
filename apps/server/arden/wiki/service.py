"""Snapshot-pinned wiki operations over :mod:`arden.revisions`."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from hashlib import sha256

from arden.revisions import (
    Archive,
    ChangeSet,
    Commit,
    Create,
    ManagedFileRepository,
    Move,
    ResourceState,
    Restore,
    StorageReport,
    Update,
)
from arden.revisions.errors import RevisionConflictError

from .models import (
    GeneratedPageTarget,
    LinkReference,
    LinkStatus,
    RenamePlan,
    RenameRewrite,
    WikiChangeCommit,
    WikiChangesReport,
    WikiChangeWarning,
    WikiFactCitation,
    WikiInfrastructureRole,
    WikiLinkReport,
    WikiMaintenancePageUpdate,
    WikiPageRecord,
    WikiPageRevision,
    WikiResourceChange,
    WikiSnapshot,
)
from .pages import PageValidationError, WikiPage, extract_generated_region, parse_page, update_generated_region
from .pages import create_page as build_page
from .wikilinks import WikilinkNode, parse_wikilinks, rewrite_page_targets


class WikiValidationError(ValueError):
    """The visible wiki tree violates a domain invariant."""


class WikiAmbiguityError(WikiValidationError):
    """A page name can resolve to more than one page."""


class GeneratedRegionConflictError(WikiValidationError):
    """A user changed a Synthesis-owned generated region."""


_STORAGE_INSPECTION_BYTES = 50 * 1024 * 1024
_STORAGE_NEEDS_ATTENTION_BYTES = 100 * 1024 * 1024


def _storage_warnings(storage: StorageReport, target: str) -> tuple[WikiChangeWarning, ...]:
    """Classify storage without touching the filesystem, for deterministic maintenance."""

    if storage.total_bytes >= _STORAGE_NEEDS_ATTENTION_BYTES:
        return (WikiChangeWarning("storage_needs_attention", target, str(storage.total_bytes)),)
    if storage.total_bytes >= _STORAGE_INSPECTION_BYTES:
        return (WikiChangeWarning("storage_inspection", target, str(storage.total_bytes)),)
    return ()


@dataclass(frozen=True, slots=True)
class _Index:
    names: dict[str, tuple[str, ...]]
    pages: dict[str, WikiPageRecord]


class WikiService:
    """Common wiki lifecycle and rename behavior.

    Every read used by an operation is pinned to one repository head.  Writes
    use that same head as a whole-tree compare-and-swap.
    """

    def __init__(self, repository: ManagedFileRepository) -> None:
        self.repository = repository

    def snapshot(self) -> WikiSnapshot:
        return self._snapshot(strict_names=True)

    def list_pages(self, *, include_redirects: bool = False) -> tuple[WikiPageRecord, ...]:
        snapshot = self.snapshot()
        return tuple(record for record in snapshot.pages if include_redirects or record.page.lifecycle == "active")

    def readable_pages(self) -> tuple[WikiPageRecord, ...]:
        """Return valid active pages while independently omitting malformed ones."""

        head = self.repository.head
        records, _warnings = self._maintenance_snapshot(head)
        if self.repository.head != head:
            raise RevisionConflictError("wiki changed while reading pages")
        return tuple(record for record in records if record.page.lifecycle == "active")

    def changes_since(self, watermark: str | None) -> WikiChangesReport:
        """Return chronological managed-Markdown history through one pinned head.

        This deliberately reports malformed historical blobs instead of using the
        strict page snapshot: maintenance must be able to show the bad revision
        that requires repair.
        """

        if watermark is not None and (not isinstance(watermark, str) or not watermark):
            raise ValueError("watermark must be a nonempty commit ID or None")
        head = self.repository.head
        history = () if head is None else self.repository.history(start=head)
        commits_newest_first = tuple(history)
        commit_ids = {commit.commit_id for commit in commits_newest_first}
        if watermark is not None and watermark not in commit_ids:
            raise KeyError(f"wiki watermark is not reachable from pinned head: {watermark}")

        # Repository history is linear.  Stop at the watermark, then expose the
        # natural oldest-to-newest review order.
        selected: list[Commit] = []
        for commit in commits_newest_first:
            if commit.commit_id == watermark:
                break
            selected.append(commit)

        current_records, current_warnings = self._maintenance_snapshot(head)
        current_index = self._index(WikiSnapshot(head, current_records), strict_names=False)
        current_links = self._maintenance_links(current_records, current_index)
        warnings = list(current_warnings)
        warnings.extend(self._link_warnings(current_records, current_links))
        integrity = self.repository.integrity_report()
        storage = self.repository.storage_report()
        warnings.extend(WikiChangeWarning(issue.code, issue.target, issue.detail) for issue in integrity.issues)
        warnings.extend(_storage_warnings(storage, str(self.repository.history_root)))

        reports: list[WikiChangeCommit] = []
        for commit in reversed(selected):
            diffs = {
                item.resource_id: item.unified_diff for item in self.repository.diff(commit.parent_id, commit.commit_id)
            }
            changes: list[WikiResourceChange] = []
            for change in commit.changes:
                resource = change.after or change.before
                assert resource is not None
                if not self._is_markdown_change(change.before, change.after):
                    continue
                before = self._revision_at(change.before, commit.parent_id)
                after = self._revision_at(change.after, commit.commit_id)
                for revision in (before, after):
                    if revision is not None:
                        if revision.validation_error is not None:
                            warnings.append(
                                WikiChangeWarning("invalid_page", revision.resource.path, revision.validation_error)
                            )
                        warnings.extend(revision.provenance_warnings)
                outgoing, backlinks = current_links.get(resource.resource_id, ((), ()))
                changes.append(
                    WikiResourceChange(
                        action=change.action,
                        resource_id=resource.resource_id,
                        before=before,
                        after=after,
                        unified_diff=diffs.get(resource.resource_id, ""),
                        current_outgoing=outgoing,
                        current_backlinks=backlinks,
                    )
                )
            reports.append(
                WikiChangeCommit(
                    commit_id=commit.commit_id,
                    parent_id=commit.parent_id,
                    actor=commit.actor,
                    origin=commit.origin,
                    reason=commit.reason,
                    timestamp=commit.timestamp,
                    changes=tuple(changes),
                )
            )
        if self.repository.head != head:
            raise RevisionConflictError("wiki changed while building a change feed")
        return WikiChangesReport(
            watermark=watermark,
            through_revision=head,
            commits=tuple(reports),
            warnings=tuple(self._dedupe_warnings(warnings)),
            integrity=integrity,
            storage=storage,
        )

    def read_page(self, page_id: str) -> WikiPageRecord:
        record = self._index(self.snapshot()).pages.get(page_id)
        if record is None:
            raise KeyError(f"unknown active wiki page: {page_id}")
        return record

    def create_page(
        self,
        *,
        path: str,
        title: str,
        body: bytes = b"",
        page_id: str | None = None,
        aliases: tuple[str, ...] = (),
        metadata: dict[str, object] | None = None,
        expected_head: str | None = None,
        actor: str = "wiki",
        origin: str = "wiki",
        reason: str = "create page",
        idempotency_key: str | None = None,
    ) -> WikiPageRecord:
        self._require_markdown_path(path)
        snapshot = self.snapshot()
        self._require_head(snapshot.head, expected_head)
        page = build_page(title=title, body=body, page_id=page_id, aliases=aliases, metadata=metadata)
        self._assert_new_names(snapshot, page, path)
        key = idempotency_key or self._key("create", snapshot.head, page.page_id, path, page.to_bytes())
        self.repository.commit(
            ChangeSet(
                operations=(Create(page.page_id, path, page.to_bytes()),),
                actor=actor,
                origin=origin,
                reason=reason,
                idempotency_key=key,
                expected_head=snapshot.head,
            )
        )
        return self.read_page(page.page_id)

    def publish_generated(
        self,
        targets: tuple[GeneratedPageTarget, ...],
        *,
        source_revision: str,
        base_head: str | None,
        actor: str = "Synthesis",
        origin: str = "memory.synthesis",
        reason: str | None = None,
    ) -> Commit | None:
        """Atomically publish Synthesis-owned regions from one wiki snapshot."""

        if not isinstance(source_revision, str) or not source_revision:
            raise ValueError("source_revision must be a nonempty string")
        if not isinstance(base_head, str | None):
            raise TypeError("base_head must be a string or None")
        if not isinstance(targets, tuple) or not all(isinstance(target, GeneratedPageTarget) for target in targets):
            raise TypeError("targets must be a tuple of GeneratedPageTarget values")

        snapshot = (
            WikiSnapshot(head=None, pages=()) if base_head is None else self._snapshot(strict_names=True, at=base_head)
        )
        if not targets:
            return None

        records = self._index(snapshot).pages
        target_ids = [target.page_id for target in targets]
        if len(target_ids) != len(set(target_ids)):
            raise WikiValidationError("generated targets must not repeat a page_id")
        archived_names: set[str] = set()
        if base_head is not None:
            for resource in self.repository.list_resources(at=base_head, include_archived=True):
                if resource.state is not ResourceState.ARCHIVED or not resource.path.endswith(".md"):
                    continue
                page = self._parse(resource, self.repository.read(resource.resource_id, at=base_head))
                archived_names.update(self._normal(name) for name in self._names(page, resource.path))
        for target in targets:
            self._require_markdown_path(target.path)
            record = records.get(target.page_id)
            if record is not None:
                if record.page.lifecycle != "active":
                    raise WikiValidationError("generated targets must be active pages")
                if (
                    record.resource.path != target.path
                    or record.page.title != target.title
                    or record.page.aliases != target.aliases
                ):
                    raise WikiValidationError("generated target identity does not match the existing page")
            elif base_head is not None:
                try:
                    self.repository.get(target.page_id, at=base_head)
                except KeyError:
                    pass
                else:
                    raise WikiValidationError(f"generated target resource is unavailable: {target.page_id}")
                reused = {self._normal(name) for name in self._names_for_target(target)} & archived_names
                if reused:
                    raise WikiValidationError(f"generated target reuses an archived wiki name: {min(reused)}")

        finals: list[tuple[GeneratedPageTarget, WikiPageRecord | None, bytes]] = []
        prospective_names = {
            self._normal(name): record.page.page_id
            for record in snapshot.pages
            for name in self._names(record.page, record.resource.path)
        }
        for target in sorted(targets, key=lambda item: item.page_id):
            record = records.get(target.page_id)
            metadata = {**target.metadata, "generated_from_revision": source_revision}
            if record is None:
                empty = build_page(
                    page_id=target.page_id,
                    title=target.title,
                    aliases=target.aliases,
                ).to_bytes()
                content = update_generated_region(
                    empty,
                    expected_page_id=target.page_id,
                    generated=target.generated,
                    metadata=metadata,
                )
                page = self._parse(Create(target.page_id, target.path, content), content)
                for normalized in {self._normal(name) for name in self._names(page, target.path)}:
                    if normalized in prospective_names:
                        raise WikiAmbiguityError(f"wiki name already exists: {normalized}")
                    prospective_names[normalized] = target.page_id
            else:
                current = extract_generated_region(record.content, expected_page_id=target.page_id)
                prior_exists, prior = self._last_synthesis_region(target.page_id, actor, origin, base_head)
                if not self._generated_region_is_safe(
                    current=current,
                    desired=target.generated,
                    prior_exists=prior_exists,
                    prior=prior,
                ):
                    raise GeneratedRegionConflictError(f"generated region changed by a user: {record.resource.path}")
                content = update_generated_region(
                    record.content,
                    expected_page_id=target.page_id,
                    generated=target.generated,
                    metadata=metadata,
                )
            finals.append((target, record, content))

        operations = tuple(
            Create(target.page_id, target.path, content)
            if record is None
            else Update(target.page_id, record.resource.version_id, content)
            for target, record, content in finals
            if record is None or content != record.content
        )
        if not operations:
            current_head = self.repository.head
            if current_head != base_head:
                raise RevisionConflictError(f"current head changed: expected {base_head!r}, found {current_head!r}")
            return None
        key = self._key(
            "generated",
            base_head,
            source_revision,
            tuple((target.page_id, content) for target, _record, content in finals),
        )
        return self.repository.commit(
            ChangeSet(
                operations=operations,
                actor=actor,
                origin=origin,
                reason=reason or "publish generated wiki pages",
                idempotency_key=key,
                expected_head=base_head,
                enforce_expected_head=base_head is None,
            )
        )

    def apply_maintenance_updates(
        self,
        updates: tuple[WikiMaintenancePageUpdate, ...],
        *,
        base_head: str,
        reason: str = "apply wiki maintenance updates",
        idempotency_key: str | None = None,
    ) -> str:
        """Atomically apply ordinary edits without changing page identity.

        Maintenance may alter only title, aliases, and body.  Generated content
        and Synthesis provenance remain owned by their existing publisher.
        """

        if not isinstance(updates, tuple) or not all(isinstance(item, WikiMaintenancePageUpdate) for item in updates):
            raise TypeError("updates must be a tuple of WikiMaintenancePageUpdate values")
        if not isinstance(base_head, str) or not base_head:
            raise ValueError("base_head must be a nonempty commit ID")
        if not isinstance(reason, str) or not reason:
            raise ValueError("reason must be a nonempty string")
        if idempotency_key is not None and (not isinstance(idempotency_key, str) or not idempotency_key):
            raise ValueError("idempotency_key must be a nonempty string or None")

        snapshot = self._snapshot(strict_names=True, at=base_head)
        records = self._index(snapshot).pages
        page_ids = [item.page_id for item in updates]
        if len(page_ids) != len(set(page_ids)):
            raise WikiValidationError("maintenance updates must not repeat a page_id")

        replacements: dict[str, WikiPageRecord] = {}
        operations: list[Update] = []
        for update in updates:
            record = records.get(update.page_id)
            if record is None or record.page.lifecycle != "active":
                raise KeyError(f"unknown active wiki page: {update.page_id}")
            if record.resource.version_id != update.expected_version:
                raise RevisionConflictError(f"resource {update.page_id} changed: expected {update.expected_version}")

            if (
                record.page.title == update.title
                and record.page.aliases == update.aliases
                and record.page.body == update.body
            ):
                replacement = record
            else:
                replacement_page = build_page(
                    page_id=record.page.page_id,
                    title=update.title,
                    aliases=update.aliases,
                    lifecycle=record.page.lifecycle,
                    redirect_to=record.page.redirect_to,
                    metadata=record.page.metadata,
                    body=update.body,
                )
                content = replacement_page.to_bytes()
                parsed = self._parse(record.resource, content)
                self._assert_maintenance_preserves_owned_content(record.page, parsed)
                replacement = WikiPageRecord(record.resource, parsed, content)
            replacements[update.page_id] = replacement
            if replacement.content != record.content:
                operations.append(Update(update.page_id, update.expected_version, replacement.content))

        prospective = tuple(replacements.get(record.page.page_id, record) for record in snapshot.pages)
        self._validate_prospective(snapshot, prospective)
        if not operations:
            if self.repository.head != base_head:
                raise RevisionConflictError(
                    f"current head changed: expected {base_head!r}, found {self.repository.head!r}"
                )
            return base_head

        key = idempotency_key or self._key(
            "maintenance",
            base_head,
            reason,
            tuple((item.page_id, item.expected_version, item.title, item.aliases, item.body) for item in updates),
        )
        commit = self.repository.commit(
            ChangeSet(
                operations=tuple(operations),
                actor="Wiki Maintenance",
                origin="wiki.maintenance",
                reason=reason,
                idempotency_key=key,
                expected_head=base_head,
            )
        )
        return commit.commit_id

    def publish_health(self, *, body: bytes, base_head: str | None) -> Commit | None:
        """Publish the single backend-owned, read-only health page.

        ``health`` is deliberately not a generated-region page: its complete
        body is a deterministic projection.  It therefore has one narrow
        writer and cannot touch any user page.
        """

        if not isinstance(body, bytes):
            raise TypeError("body must be bytes")
        if not isinstance(base_head, str | None):
            raise TypeError("base_head must be a string or None")

        content = build_page(page_id="health", title="Wiki health", body=body).to_bytes()
        try:
            existing = self.repository.get("health", at=base_head)
        except KeyError:
            existing = None
        path_owner = self.repository.find_by_path("health.md", at=base_head, include_archived=True)

        if (existing is not None and existing.state is ResourceState.ARCHIVED) or (
            path_owner is not None and path_owner.state is ResourceState.ARCHIVED
        ):
            current_head = self.repository.head
            if current_head != base_head:
                raise RevisionConflictError(f"current head changed: expected {base_head!r}, found {current_head!r}")
            return None
        if existing is None:
            if path_owner is not None:
                raise WikiValidationError("health.md belongs to a different wiki resource")
            operation = Create("health", "health.md", content)
        else:
            if existing.path != "health.md":
                raise WikiValidationError("health resource must remain at health.md")
            if path_owner is None or path_owner.resource_id != "health":
                raise WikiValidationError("health.md must belong to the health resource")
            if self.repository.read("health", at=base_head) == content:
                if self.repository.head != base_head:
                    raise RevisionConflictError(
                        f"current head changed: expected {base_head!r}, found {self.repository.head!r}"
                    )
                return None
            operation = Update("health", existing.version_id, content)

        return self.repository.commit(
            ChangeSet(
                operations=(operation,),
                actor="backend",
                origin="wiki.health",
                reason="project wiki health",
                idempotency_key=self._key("health", base_head, content),
                expected_head=base_head,
                enforce_expected_head=base_head is None,
            )
        )

    def backlinks(self, page_id: str) -> tuple[LinkReference, ...]:
        """Report links to ``page_id`` without guessing unresolved names."""

        return self.link_report(page_id).backlinks

    def links(self, page_id: str) -> tuple[LinkReference, ...]:
        """Return outgoing links with explicit resolution status."""

        return self.link_report(page_id).outgoing

    def link_report(self, page_id: str) -> WikiLinkReport:
        """Resolve both directions from one immutable wiki snapshot."""

        snapshot = self._snapshot(strict_names=False)
        return self._link_report(snapshot, page_id)

    def link_report_for_path(self, path: str) -> WikiLinkReport:
        """Resolve an active or redirect path and its links from one snapshot."""

        snapshot = self._snapshot(strict_names=False)
        index = self._index(snapshot, strict_names=False)
        record = next(
            (candidate for candidate in snapshot.pages if candidate.resource.path == path),
            None,
        )
        if record is None:
            raise KeyError(f"unknown wiki path: {path}")
        target_id = self._follow_redirect(index, record.page.page_id)
        if target_id is None:
            raise WikiValidationError(f"redirect at {path} has no active target")
        return self._link_report(snapshot, target_id)

    def _link_report(self, snapshot: WikiSnapshot, page_id: str) -> WikiLinkReport:
        index = self._index(snapshot, strict_names=False)
        record = index.pages.get(page_id)
        if record is None or record.page.lifecycle != "active":
            raise KeyError(f"unknown active wiki page: {page_id}")
        outgoing = tuple(
            self._reference(index, page_id, node) for node in parse_wikilinks(record.page.body.decode("utf-8"))
        )
        backlinks: list[LinkReference] = []
        for source in snapshot.pages:
            for node in parse_wikilinks(source.page.body.decode("utf-8")):
                reference = self._reference(index, source.page.page_id, node)
                if reference.target_page_id == page_id:
                    backlinks.append(reference)
        return WikiLinkReport(
            head=snapshot.head,
            page=record,
            pages=snapshot.pages,
            outgoing=outgoing,
            backlinks=tuple(backlinks),
        )

    def archive_page(
        self,
        page_id: str,
        *,
        expected_version: str | None = None,
        base_head: str | None = None,
        actor: str = "wiki",
        origin: str = "wiki",
        reason: str = "archive page",
        idempotency_key: str | None = None,
    ) -> None:
        snapshot = self.snapshot()
        self._require_head(snapshot.head, base_head)
        record = self._index(snapshot).pages.get(page_id)
        if record is None or record.page.lifecycle != "active":
            raise KeyError(f"unknown active wiki page: {page_id}")
        self._validate_prospective(snapshot, tuple(item for item in snapshot.pages if item.page.page_id != page_id))
        version = expected_version or record.resource.version_id
        key = idempotency_key or self._key("archive", snapshot.head, page_id, version)
        self.repository.commit(
            ChangeSet(
                operations=(Archive(page_id, version),),
                actor=actor,
                origin=origin,
                reason=reason,
                idempotency_key=key,
                expected_head=snapshot.head,
            )
        )

    def restore_page(
        self,
        page_id: str,
        *,
        expected_version: str | None = None,
        base_head: str | None = None,
        actor: str = "wiki",
        origin: str = "wiki",
        reason: str = "restore page",
        idempotency_key: str | None = None,
    ) -> WikiPageRecord:
        head = self.repository.head
        self._require_head(head, base_head)
        resource = self.repository.get(page_id, at=head)
        if resource.state is not ResourceState.ARCHIVED:
            raise WikiValidationError(f"page is not archived: {page_id}")
        content = self.repository.read(page_id, at=head)
        page = self._parse(resource, content)
        active = self._snapshot(strict_names=True, at=head)
        self._assert_new_names(active, page, resource.path)
        self._validate_prospective(active, (*active.pages, WikiPageRecord(resource, page, content)))
        version = expected_version or resource.version_id
        key = idempotency_key or self._key("restore", head, page_id, version)
        self.repository.commit(
            ChangeSet(
                operations=(Restore(page_id, version),),
                actor=actor,
                origin=origin,
                reason=reason,
                idempotency_key=key,
                expected_head=head,
            )
        )
        return self.read_page(page_id)

    def prepare_rename(
        self,
        page_id: str,
        *,
        new_path: str,
        new_title: str,
        expected_version: str,
        base_head: str | None,
        rewrite_links: bool = True,
    ) -> RenamePlan:
        self._require_markdown_path(new_path)
        snapshot = self.snapshot()
        self._require_head(snapshot.head, base_head)
        return self._plan_rename(
            snapshot,
            page_id=page_id,
            new_path=new_path,
            new_title=new_title,
            expected_version=expected_version,
            rewrite_links=rewrite_links,
        )

    def _plan_rename(
        self,
        snapshot: WikiSnapshot,
        *,
        page_id: str,
        new_path: str,
        new_title: str,
        expected_version: str,
        rewrite_links: bool,
    ) -> RenamePlan:
        self._require_markdown_path(new_path)
        index = self._index(snapshot)
        record = index.pages.get(page_id)
        if record is None or record.page.lifecycle != "active":
            raise WikiValidationError("rename requires an active, nonredirect page")
        if record.resource.version_id != expected_version:
            raise RevisionConflictError(f"resource {page_id} changed: expected {expected_version}")
        replacement = record.page.with_title(new_title)
        self._assert_rename_names(snapshot, record, replacement, new_path)
        redirect_id = self._redirect_id(page_id, record.resource.path)
        self._assert_redirect_resource_available(redirect_id, snapshot.head)

        rewrites: list[RenameRewrite] = []
        moved_content = replacement.to_bytes()
        link_count = 0
        page_count = 0
        if rewrite_links:
            for source in snapshot.pages:
                if source.page.lifecycle != "active":
                    continue
                references = tuple(
                    reference
                    for node in parse_wikilinks(source.page.body.decode("utf-8"))
                    if (reference := self._reference(index, source.page.page_id, node)).target_page_id == page_id
                    and reference.status is LinkStatus.RESOLVED
                )
                if not references:
                    continue
                link_count += len(references)
                page_count += 1
                targets = {
                    reference.node: self._rename_target(reference.node, new_path, new_title) for reference in references
                }
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

        key = self._key("rename", snapshot.head, page_id, expected_version, new_path, new_title, rewrite_links)
        return RenamePlan(
            base_head=snapshot.head,
            page_id=page_id,
            expected_version=expected_version,
            old_path=record.resource.path,
            new_path=new_path,
            old_title=record.page.title,
            new_title=new_title,
            moved_content=moved_content,
            redirect_page_id=redirect_id,
            rewrite_links=rewrite_links,
            link_count=link_count,
            page_count=page_count,
            rewrites=tuple(rewrites),
            idempotency_key=key,
        )

    def apply_rename(
        self,
        plan: RenamePlan,
        *,
        actor: str = "wiki",
        origin: str = "wiki",
        reason: str = "rename page",
    ) -> Commit:
        if not isinstance(plan, RenamePlan):
            raise TypeError("plan must be a RenamePlan")
        canonical = self._plan_rename(
            self._snapshot(strict_names=True, at=plan.base_head),
            page_id=plan.page_id,
            new_path=plan.new_path,
            new_title=plan.new_title,
            expected_version=plan.expected_version,
            rewrite_links=plan.rewrite_links,
        )
        if plan != canonical:
            raise WikiValidationError("rename plan does not match its pinned snapshot")
        redirect = build_page(
            page_id=plan.redirect_page_id,
            title=plan.old_title,
            lifecycle="redirect",
            redirect_to=plan.page_id,
        ).to_bytes()
        operations = [Move(plan.page_id, plan.expected_version, plan.new_path, plan.moved_content)]
        operations.append(Create(plan.redirect_page_id, plan.old_path, redirect))
        operations.extend(
            Update(rewrite.resource_id, rewrite.expected_version, rewrite.content) for rewrite in plan.rewrites
        )
        return self.repository.commit(
            ChangeSet(
                operations=tuple(operations),
                actor=actor,
                origin=origin,
                reason=reason,
                idempotency_key=plan.idempotency_key,
                expected_head=plan.base_head,
            )
        )

    def _maintenance_snapshot(
        self, head: str | None
    ) -> tuple[tuple[WikiPageRecord, ...], tuple[WikiChangeWarning, ...]]:
        """Read active Markdown pages without letting one malformed page hide others."""

        records: list[WikiPageRecord] = []
        warnings: list[WikiChangeWarning] = []
        for resource in self.repository.list_resources(at=head):
            if not resource.path.endswith(".md"):
                continue
            content = self.repository.read(resource.resource_id, at=head)
            try:
                page = self._parse(resource, content)
            except WikiValidationError as error:
                warnings.append(WikiChangeWarning("invalid_page", resource.path, str(error)))
                continue
            _generated, _citations, provenance_warnings = self._provenance(page, resource.path)
            warnings.extend(provenance_warnings)
            records.append(WikiPageRecord(resource, page, content))
        return tuple(sorted(records, key=lambda record: record.page.page_id)), tuple(warnings)

    def _maintenance_links(
        self,
        records: tuple[WikiPageRecord, ...],
        index: _Index,
    ) -> dict[str, tuple[tuple[LinkReference, ...], tuple[LinkReference, ...]]]:
        outgoing: dict[str, tuple[LinkReference, ...]] = {}
        backlinks: dict[str, list[LinkReference]] = {record.page.page_id: [] for record in records}
        for record in records:
            references = tuple(
                self._reference(index, record.page.page_id, node)
                for node in parse_wikilinks(record.page.body.decode("utf-8"))
            )
            outgoing[record.page.page_id] = references
            for reference in references:
                if reference.target_page_id is not None:
                    backlinks.setdefault(reference.target_page_id, []).append(reference)
        return {
            record.page.page_id: (outgoing[record.page.page_id], tuple(backlinks[record.page.page_id]))
            for record in records
        }

    def _link_warnings(
        self,
        records: tuple[WikiPageRecord, ...],
        links: dict[str, tuple[tuple[LinkReference, ...], tuple[LinkReference, ...]]],
    ) -> tuple[WikiChangeWarning, ...]:
        warnings: list[WikiChangeWarning] = []
        for record in records:
            outgoing, backlinks = links[record.page.page_id]
            for reference in outgoing:
                if reference.status is LinkStatus.RESOLVED:
                    continue
                target = reference.node.page or "#fragment"
                warnings.append(
                    WikiChangeWarning(
                        f"{reference.status.value}_link",
                        record.resource.path,
                        f"{target} ({', '.join(reference.candidates)})" if reference.candidates else target,
                    )
                )
            role = self._role(record.resource.resource_id, record.resource.path)
            if (
                record.page.lifecycle == "active"
                and role
                not in {
                    WikiInfrastructureRole.README,
                    WikiInfrastructureRole.ME,
                    WikiInfrastructureRole.ACTIVE_WORK,
                    WikiInfrastructureRole.DAILY,
                }
                and not backlinks
            ):
                warnings.append(WikiChangeWarning("orphan_page", record.resource.path, record.page.page_id))
        return tuple(warnings)

    def _revision_at(self, resource, at: str | None) -> WikiPageRevision | None:
        if resource is None:
            return None
        content = self.repository.read(resource.resource_id, at=at)
        try:
            page = self._parse(resource, content)
        except WikiValidationError as error:
            return WikiPageRevision(
                resource=resource,
                content=content,
                page=None,
                validation_error=str(error),
                role=self._role(resource.resource_id, resource.path),
                generated_from_revision=None,
                fact_citations=(),
            )
        generated_from_revision, fact_citations, provenance_warnings = self._provenance(page, resource.path)
        return WikiPageRevision(
            resource=resource,
            content=content,
            page=page,
            validation_error=None,
            role=self._role(resource.resource_id, resource.path),
            generated_from_revision=generated_from_revision,
            fact_citations=fact_citations,
            provenance_warnings=provenance_warnings,
        )

    @staticmethod
    def _provenance(
        page: WikiPage, path: str
    ) -> tuple[str | None, tuple[WikiFactCitation, ...], tuple[WikiChangeWarning, ...]]:
        warnings: list[WikiChangeWarning] = []
        if "generated_from_revision" not in page.metadata:
            generated_from_revision = None
        elif isinstance(page.metadata["generated_from_revision"], str) and re.fullmatch(
            r"[0-9a-f]{64}", page.metadata["generated_from_revision"]
        ):
            generated = page.metadata["generated_from_revision"]
            assert isinstance(generated, str)
            generated_from_revision = generated
        else:
            generated_from_revision = None
            warnings.append(
                WikiChangeWarning(
                    "invalid_generated_from_revision", path, repr(page.metadata["generated_from_revision"])
                )
            )

        if "fact_citations" not in page.metadata:
            return generated_from_revision, (), tuple(warnings)
        raw_citations = page.metadata["fact_citations"]
        if not isinstance(raw_citations, Sequence) or isinstance(raw_citations, (str, bytes)):
            warnings.append(WikiChangeWarning("invalid_fact_citations", path, repr(raw_citations)))
            return generated_from_revision, (), tuple(warnings)
        citations: list[WikiFactCitation] = []
        for index, item in enumerate(raw_citations):
            if (
                isinstance(item, Mapping)
                and isinstance(item.get("fact_id"), str)
                and item["fact_id"].strip()
                and isinstance(item.get("version"), str)
                and re.fullmatch(r"[0-9a-f]{64}", item["version"])
            ):
                citations.append(WikiFactCitation(item["fact_id"], item["version"]))
            else:
                warnings.append(WikiChangeWarning("invalid_fact_citations", path, f"entry {index}: {item!r}"))
        return generated_from_revision, tuple(citations), tuple(warnings)

    @staticmethod
    def _role(resource_id: str, path: str) -> WikiInfrastructureRole:
        if resource_id == "me" or path == "me.md":
            return WikiInfrastructureRole.ME
        if resource_id == "active-work" or path == "active-work.md":
            return WikiInfrastructureRole.ACTIVE_WORK
        if path == "README.md" or path.endswith("/README.md"):
            return WikiInfrastructureRole.README
        if path.startswith("daily/"):
            return WikiInfrastructureRole.DAILY
        if path.startswith("insights/"):
            return WikiInfrastructureRole.INSIGHT
        return WikiInfrastructureRole.COMMON

    @staticmethod
    def _is_markdown_change(before, after) -> bool:
        return (before is not None and before.path.endswith(".md")) or (
            after is not None and after.path.endswith(".md")
        )

    @staticmethod
    def _dedupe_warnings(warnings: list[WikiChangeWarning]) -> tuple[WikiChangeWarning, ...]:
        seen: set[tuple[str, str, str]] = set()
        result: list[WikiChangeWarning] = []
        for warning in warnings:
            key = (warning.code, warning.target, warning.evidence)
            if key not in seen:
                seen.add(key)
                result.append(warning)
        return tuple(result)

    def _snapshot(self, *, strict_names: bool, at: str | None = None) -> WikiSnapshot:
        head = self.repository.head if at is None else at
        records: list[WikiPageRecord] = []
        for resource in self.repository.list_resources(at=head):
            if not resource.path.endswith(".md"):
                continue
            content = self.repository.read(resource.resource_id, at=head)
            records.append(WikiPageRecord(resource, self._parse(resource, content), content))
        snapshot = WikiSnapshot(head=head, pages=tuple(sorted(records, key=lambda record: record.page.page_id)))
        index = self._index(snapshot, strict_names=strict_names)
        self._validate_redirects(index)
        return snapshot

    @staticmethod
    def _parse(resource, content: bytes) -> WikiPage:
        try:
            return parse_page(content, expected_page_id=resource.resource_id)
        except PageValidationError as error:
            raise WikiValidationError(f"invalid wiki page {resource.path}: {error}") from error

    def _index(self, snapshot: WikiSnapshot, *, strict_names: bool = True) -> _Index:
        pages = {record.page.page_id: record for record in snapshot.pages}
        names: dict[str, set[str]] = {}
        for record in snapshot.pages:
            for name in self._names(record.page, record.resource.path):
                names.setdefault(self._normal(name), set()).add(record.page.page_id)
        collisions = {name: tuple(sorted(ids)) for name, ids in names.items() if len(ids) > 1}
        if strict_names and collisions:
            name, ids = next(iter(collisions.items()))
            raise WikiAmbiguityError(f"ambiguous wiki name {name!r}: {', '.join(ids)}")
        return _Index(names={name: tuple(sorted(ids)) for name, ids in names.items()}, pages=pages)

    def _reference(self, index: _Index, source_page_id: str, node: WikilinkNode) -> LinkReference:
        if node.page is None:
            return LinkReference(source_page_id, node, LinkStatus.UNRESOLVED)
        candidates = index.names.get(self._normal(node.page), ())
        if not candidates:
            return LinkReference(source_page_id, node, LinkStatus.UNRESOLVED)
        if len(candidates) != 1:
            return LinkReference(source_page_id, node, LinkStatus.AMBIGUOUS, candidates=candidates)
        target = self._follow_redirect(index, candidates[0])
        if target is None:
            return LinkReference(source_page_id, node, LinkStatus.UNRESOLVED)
        return LinkReference(source_page_id, node, LinkStatus.RESOLVED, target)

    @staticmethod
    def _follow_redirect(index: _Index, page_id: str) -> str | None:
        seen: set[str] = set()
        while True:
            if page_id in seen:
                return None
            seen.add(page_id)
            page = index.pages[page_id].page
            if page.lifecycle == "active":
                return page_id
            if page.redirect_to not in index.pages:
                return None
            page_id = page.redirect_to

    def _validate_redirects(self, index: _Index) -> None:
        for page_id, record in index.pages.items():
            if record.page.lifecycle == "redirect" and self._follow_redirect(index, page_id) is None:
                raise WikiValidationError(f"redirect {page_id} has a missing target or cycle")

    def _validate_prospective(self, snapshot: WikiSnapshot, pages: tuple[WikiPageRecord, ...]) -> None:
        index = self._index(WikiSnapshot(snapshot.head, pages))
        self._validate_redirects(index)

    def _last_synthesis_region(
        self, page_id: str, actor: str, origin: str, base_head: str
    ) -> tuple[bool, bytes | None]:
        for commit in self.repository.history(resource_id=page_id, start=base_head):
            if commit.actor != actor or commit.origin != origin:
                continue
            change = next(
                (
                    item
                    for item in commit.changes
                    if item.after is not None
                    and item.after.resource_id == page_id
                    and item.after.state is ResourceState.ACTIVE
                ),
                None,
            )
            if change is None:
                continue
            return True, extract_generated_region(
                self.repository.read(page_id, at=commit.commit_id), expected_page_id=page_id
            )
        return False, None

    @staticmethod
    def _generated_region_is_safe(
        *, current: bytes | None, desired: bytes, prior_exists: bool, prior: bytes | None
    ) -> bool:
        if current == desired:
            return True
        if prior_exists:
            return current == prior
        return current is None or current == b""

    @staticmethod
    def _assert_maintenance_preserves_owned_content(before: WikiPage, after: WikiPage) -> None:
        if (
            before.page_id != after.page_id
            or before.lifecycle != after.lifecycle
            or before.redirect_to != after.redirect_to
        ):
            raise WikiValidationError("maintenance cannot change page identity or lifecycle")
        for key in ("generated_from_revision", "fact_citations"):
            if (key in before.metadata) != (key in after.metadata) or before.metadata.get(key) != after.metadata.get(
                key
            ):
                raise WikiValidationError(f"maintenance cannot change {key}")
        try:
            before_generated = extract_generated_region(before.to_bytes(), expected_page_id=before.page_id)
            after_generated = extract_generated_region(after.to_bytes(), expected_page_id=after.page_id)
        except PageValidationError as error:
            raise WikiValidationError(f"maintenance generated region is invalid: {error}") from error
        if before_generated != after_generated:
            raise GeneratedRegionConflictError("maintenance cannot change generated page content")

    @staticmethod
    def _names(page: WikiPage, path: str) -> tuple[str, ...]:
        stem = path[:-3] if path.endswith(".md") else path
        return (page.title, *page.aliases, path, stem)

    @staticmethod
    def _names_for_target(target: GeneratedPageTarget) -> tuple[str, ...]:
        stem = target.path[:-3] if target.path.endswith(".md") else target.path
        return (target.title, *target.aliases, target.path, stem)

    @staticmethod
    def _normal(value: str) -> str:
        return value.strip().casefold()

    @staticmethod
    def _require_markdown_path(path: str) -> None:
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

    @staticmethod
    def _require_head(actual: str | None, requested: str | None) -> None:
        if requested is not None and actual != requested:
            raise RevisionConflictError(f"current head changed: expected {requested!r}, found {actual!r}")

    def _assert_new_names(self, snapshot: WikiSnapshot, page: WikiPage, path: str) -> None:
        for record in snapshot.pages:
            if record.resource.path.casefold() == path.casefold():
                raise WikiAmbiguityError(f"wiki path already exists: {path}")
        names = {self._normal(name) for name in self._names(page, path)}
        existing = self._index(snapshot).names
        for name in names:
            if name in existing:
                raise WikiAmbiguityError(f"wiki name already exists: {name}")

    def _assert_rename_names(
        self, snapshot: WikiSnapshot, current: WikiPageRecord, replacement: WikiPage, new_path: str
    ) -> None:
        others = tuple(record for record in snapshot.pages if record.page.page_id != current.page.page_id)
        temporary = WikiSnapshot(snapshot.head, others)
        self._assert_new_names(temporary, replacement, new_path)
        redirect = build_page(
            page_id=self._redirect_id(current.page.page_id, current.resource.path),
            title=current.page.title,
            lifecycle="redirect",
            redirect_to=current.page.page_id,
        )
        self._assert_new_names(
            WikiSnapshot(
                snapshot.head,
                (
                    *others,
                    WikiPageRecord(replace(current.resource, path=new_path), replacement, replacement.to_bytes()),
                ),
            ),
            redirect,
            current.resource.path,
        )

    def _assert_redirect_resource_available(self, resource_id: str, head: str | None) -> None:
        try:
            self.repository.get(resource_id, at=head)
        except KeyError:
            return
        raise WikiValidationError(f"redirect resource already exists: {resource_id}")

    @staticmethod
    def _rename_target(node: WikilinkNode, new_path: str, new_title: str) -> str:
        assert node.page is not None
        if "/" in node.page or node.page.casefold().endswith(".md"):
            return new_path if node.page.casefold().endswith(".md") else new_path[:-3]
        return new_title

    @staticmethod
    def _redirect_id(page_id: str, old_path: str) -> str:
        return f"redirect-{sha256(f'{page_id}:{old_path}'.encode()).hexdigest()}"

    @staticmethod
    def _key(*parts: object) -> str:
        return "wiki-" + sha256(repr(parts).encode()).hexdigest()
