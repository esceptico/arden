"""Public facade for snapshot-pinned wiki lifecycle operations."""

from hashlib import sha256

import arden.wiki.changes as changes
import arden.wiki.merge as page_merge
import arden.wiki.rename as rename
import arden.wiki.snapshots as snapshots
from arden.revisions.errors import CorruptRepositoryError, RevisionConflictError
from arden.revisions.models import Archive, ChangeSet, Commit, Create, Move, ResourceState, Restore, Update
from arden.revisions.repository import ManagedFileRepository
from arden.wiki.constants import (
    WIKI_HEALTH_ACTOR,
    WIKI_HEALTH_ORIGIN,
    WIKI_HEALTH_PATH,
    WIKI_HEALTH_REASON,
    WIKI_HEALTH_RESOURCE_ID,
    WIKI_MAINTENANCE_ACTOR,
    WIKI_MAINTENANCE_ORIGIN,
)
from arden.wiki.exceptions import (
    GeneratedRegionConflictError,
    WikiAmbiguityError,
    WikiMaintenanceEvidenceLimitError,
    WikiSnapshotChangedError,
    WikiValidationError,
)
from arden.wiki.models import (
    GeneratedPageTarget,
    LinkReference,
    PageMergePlan,
    RenamePlan,
    WikiChangesReport,
    WikiLinkReport,
    WikiMaintenanceCommit,
    WikiMaintenanceDetails,
    WikiMaintenanceFeed,
    WikiMaintenancePageUpdate,
    WikiPageRecord,
    WikiSnapshot,
)
from arden.wiki.pages import PageValidationError, WikiPage, extract_generated_region, update_generated_region
from arden.wiki.pages import create_page as build_page

__all__ = [
    "GeneratedRegionConflictError",
    "WikiAmbiguityError",
    "WikiMaintenanceEvidenceLimitError",
    "WikiService",
    "WikiSnapshotChangedError",
    "WikiValidationError",
]

WIKI_RENAME_ACTOR = "Wiki Rename"
WIKI_RENAME_ORIGIN = "wiki.rename"
WIKI_RENAME_REASON = "rename page"


class WikiService:
    """Common wiki lifecycle behavior over one repository CAS boundary."""

    def __init__(self, repository: ManagedFileRepository) -> None:
        self.repository = repository

    def snapshot(self) -> WikiSnapshot:
        return snapshots.snapshot(self.repository, strict_names=True)

    def list_pages(self, *, include_redirects: bool = False) -> tuple[WikiPageRecord, ...]:
        return tuple(
            record for record in self.snapshot().pages if include_redirects or record.page.lifecycle == "active"
        )

    def readable_pages(self) -> tuple[WikiPageRecord, ...]:
        head = self.repository.current_revision
        records, _warnings = changes.maintenance_snapshot(self.repository, head)
        if self.repository.current_revision != head:
            raise WikiSnapshotChangedError("wiki changed while reading pages")
        return tuple(record for record in records if record.page.lifecycle == "active")

    def resolve_topic_name(self, name: str, *, snapshot: WikiSnapshot | None = None) -> WikiPageRecord | None:
        pinned = self.snapshot() if snapshot is None else snapshot
        if not isinstance(pinned, WikiSnapshot):
            raise TypeError("snapshot must be a WikiSnapshot or None")
        return snapshots.resolve_topic_name(pinned, name)

    def maintenance_feed(self, watermark: str | None) -> WikiMaintenanceFeed:
        return changes.maintenance_feed(self.repository, watermark)

    def changes_since(
        self, watermark: str | None, *, include_diffs: bool = True, diff_char_limit: int | None = None
    ) -> WikiChangesReport:
        return changes.changes_since(
            self.repository, watermark, include_diffs=include_diffs, diff_char_limit=diff_char_limit
        )

    def maintenance_details(
        self, commit: WikiMaintenanceCommit, *, through_revision: str, diff_char_limit: int, diff_byte_budget: int
    ) -> WikiMaintenanceDetails:
        return changes.maintenance_details(
            self.repository,
            commit,
            through_revision=through_revision,
            diff_char_limit=diff_char_limit,
            diff_byte_budget=diff_byte_budget,
        )

    def read_page(self, page_id: str, *, at: str | None = None) -> WikiPageRecord:
        record = snapshots.index(snapshots.snapshot(self.repository, strict_names=True, at=at)).pages.get(page_id)
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
        snapshots.require_markdown_path(path)
        if page_id == WIKI_HEALTH_RESOURCE_ID or path.casefold() == WIKI_HEALTH_PATH:
            raise WikiValidationError("health page is backend-managed")
        snapshot = self.snapshot()
        snapshots.require_head(snapshot.head, expected_head)
        page = build_page(title=title, body=body, page_id=page_id, aliases=aliases, metadata=metadata)
        snapshots.assert_new_names(snapshot, page, path)
        key = idempotency_key or self._key("create", snapshot.head, page.page_id, path, page.to_bytes())
        self.repository.commit(
            ChangeSet(
                (Create(page.page_id, path, page.to_bytes()),),
                actor,
                origin,
                reason,
                key,
                snapshot.head,
                snapshot.head is None,
            )
        )
        return self.read_page(page.page_id)

    def update_page(self, page_id: str, **kwargs: object) -> WikiPageRecord:
        return self.update_page_with_commit(page_id, **kwargs)[0]

    def update_page_with_commit(
        self,
        page_id: str,
        *,
        content: bytes,
        expected_version: str,
        expected_head: str,
        actor: str = "user:desktop",
        origin: str = "desktop",
        reason: str = "edit wiki page",
        idempotency_key: str | None = None,
    ) -> tuple[WikiPageRecord, str | None]:
        if not isinstance(content, bytes):
            raise TypeError("content must be bytes")
        if not isinstance(expected_version, str) or not expected_version:
            raise ValueError("expected_version must be a nonempty string")
        if not isinstance(expected_head, str) or not expected_head:
            raise ValueError("expected_head must be a nonempty string")
        snapshot = self.snapshot()
        snapshots.require_head(snapshot.head, expected_head)
        record = snapshots.index(snapshot).pages.get(page_id)
        if record is None or record.page.lifecycle != "active":
            raise KeyError(f"unknown active wiki page: {page_id}")
        if page_id == WIKI_HEALTH_RESOURCE_ID:
            raise WikiValidationError("health page is backend-managed")
        if record.resource.version_id != expected_version:
            raise RevisionConflictError(f"resource {page_id} changed: expected {expected_version}")
        replacement = snapshots.parse(record.resource, content)
        if replacement.lifecycle != "active":
            raise WikiValidationError("ordinary page edits must preserve active lifecycle")
        prospective = tuple(
            WikiPageRecord(item.resource, replacement, content) if item.page.page_id == page_id else item
            for item in snapshot.pages
        )
        snapshots.validate_prospective(snapshot, prospective)
        if content == record.content:
            if self.repository.head != snapshot.head:
                raise RevisionConflictError(
                    f"current head changed: expected {snapshot.head!r}, found {self.repository.head!r}"
                )
            return record, None
        key = idempotency_key or self._key("update", snapshot.head, page_id, expected_version, content)
        commit = self.repository.commit(
            ChangeSet((Update(page_id, expected_version, content),), actor, origin, reason, key, snapshot.head)
        )
        return self.read_page(page_id), commit.commit_id

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
        if not isinstance(source_revision, str) or not source_revision:
            raise ValueError("source_revision must be a nonempty string")
        if not isinstance(base_head, str | None):
            raise TypeError("base_head must be a string or None")
        if not isinstance(targets, tuple) or not all(isinstance(target, GeneratedPageTarget) for target in targets):
            raise TypeError("targets must be a tuple of GeneratedPageTarget values")
        snapshot = (
            WikiSnapshot(None, ())
            if base_head is None
            else snapshots.snapshot(self.repository, strict_names=True, at=base_head)
        )
        if not targets:
            return None
        records = snapshots.index(snapshot).pages
        target_ids = [target.page_id for target in targets]
        if len(target_ids) != len(set(target_ids)):
            raise WikiValidationError("generated targets must not repeat a page_id")
        archived_names: set[str] = set()
        if base_head is not None:
            for resource in self.repository.list_resources(at=base_head, include_archived=True):
                if resource.state is not ResourceState.ARCHIVED or not resource.path.endswith(".md"):
                    continue
                page = snapshots.parse(resource, self.repository.read_version(resource))
                archived_names.update(snapshots.normal(name) for name in snapshots.names_for_page(page, resource.path))
        finals: list[tuple[GeneratedPageTarget, WikiPageRecord | None, bytes]] = []
        prospective_names = {
            snapshots.normal(name): record.page.page_id
            for record in snapshot.pages
            for name in snapshots.names_for_page(record.page, record.resource.path)
        }
        for target in sorted(targets, key=lambda item: item.page_id):
            snapshots.require_markdown_path(target.path)
            record = records.get(target.page_id)
            metadata = {**target.metadata, "generated_from_revision": source_revision}
            if record is None:
                if base_head is not None:
                    try:
                        self.repository.get(target.page_id, at=base_head)
                    except KeyError:
                        pass
                    else:
                        raise WikiValidationError(f"generated target resource is unavailable: {target.page_id}")
                reused = {
                    snapshots.normal(name)
                    for name in snapshots.names_for_target(target.title, target.aliases, target.path)
                } & archived_names
                if reused:
                    raise WikiValidationError(f"generated target reuses an archived wiki name: {min(reused)}")
                content = update_generated_region(
                    build_page(page_id=target.page_id, title=target.title, aliases=target.aliases).to_bytes(),
                    expected_page_id=target.page_id,
                    generated=target.generated,
                    metadata=metadata,
                )
                page = snapshots.parse(Create(target.page_id, target.path, content), content)
                for name in {snapshots.normal(item) for item in snapshots.names_for_page(page, target.path)}:
                    if name in prospective_names:
                        raise WikiAmbiguityError(f"wiki name already exists: {name}")
                    prospective_names[name] = target.page_id
            else:
                if (
                    record.page.lifecycle != "active"
                    or record.resource.path != target.path
                    or record.page.title != target.title
                    or record.page.aliases != target.aliases
                ):
                    raise WikiValidationError("generated target identity does not match the existing page")
                current = extract_generated_region(record.content, expected_page_id=target.page_id)
                prior_exists, prior = self._last_generated_region(target.page_id, actor, origin, base_head)
                if not self._generated_region_is_safe(
                    current=current, desired=target.generated, prior_exists=prior_exists, prior=prior
                ):
                    raise GeneratedRegionConflictError(f"generated region changed by a user: {record.resource.path}")
                content = update_generated_region(
                    record.content, expected_page_id=target.page_id, generated=target.generated, metadata=metadata
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
            if self.repository.head != base_head:
                raise RevisionConflictError(
                    f"current head changed: expected {base_head!r}, found {self.repository.head!r}"
                )
            return None
        key = self._key(
            "generated",
            base_head,
            source_revision,
            tuple((target.page_id, content) for target, _record, content in finals),
        )
        return self.repository.commit(
            ChangeSet(
                operations, actor, origin, reason or "publish generated wiki pages", key, base_head, base_head is None
            )
        )

    def apply_maintenance_updates(
        self,
        updates: tuple[WikiMaintenancePageUpdate, ...],
        *,
        base_head: str,
        reason: str = "apply wiki maintenance updates",
        idempotency_key: str | None = None,
        actor: str = WIKI_MAINTENANCE_ACTOR,
        origin: str = WIKI_MAINTENANCE_ORIGIN,
    ) -> str:
        if not isinstance(updates, tuple) or not all(isinstance(item, WikiMaintenancePageUpdate) for item in updates):
            raise TypeError("updates must be a tuple of WikiMaintenancePageUpdate values")
        if not isinstance(base_head, str) or not base_head:
            raise ValueError("base_head must be a nonempty commit ID")
        snapshot = snapshots.snapshot(self.repository, strict_names=True, at=base_head)
        records = snapshots.index(snapshot).pages
        if len({item.page_id for item in updates}) != len(updates):
            raise WikiValidationError("maintenance updates must not repeat a page_id")
        if WIKI_HEALTH_RESOURCE_ID in {item.page_id for item in updates}:
            raise WikiValidationError("health page is backend-owned")
        replacements: dict[str, WikiPageRecord] = {}
        operations: list[Update] = []
        for update in updates:
            record = records.get(update.page_id)
            if record is None or record.page.lifecycle != "active":
                raise KeyError(f"unknown active wiki page: {update.page_id}")
            if record.resource.version_id != update.expected_version:
                raise RevisionConflictError(f"resource {update.page_id} changed: expected {update.expected_version}")
            page = build_page(
                page_id=record.page.page_id,
                title=update.title,
                aliases=update.aliases,
                lifecycle=record.page.lifecycle,
                redirect_to=record.page.redirect_to,
                metadata=record.page.metadata,
                body=update.body,
            )
            replacement = WikiPageRecord(
                record.resource, snapshots.parse(record.resource, page.to_bytes()), page.to_bytes()
            )
            self._assert_maintenance_preserves_owned_content(record.page, replacement.page)
            replacements[update.page_id] = replacement
            if replacement.content != record.content:
                operations.append(Update(update.page_id, update.expected_version, replacement.content))
        snapshots.validate_prospective(
            snapshot, tuple(replacements.get(record.page.page_id, record) for record in snapshot.pages)
        )
        if not operations:
            if self.repository.head != base_head:
                raise RevisionConflictError(
                    f"current head changed: expected {base_head!r}, found {self.repository.head!r}"
                )
            return base_head
        key = idempotency_key or self._key(
            "maintenance",
            base_head,
            actor,
            origin,
            reason,
            tuple((item.page_id, item.expected_version, item.title, item.aliases, item.body) for item in updates),
        )
        return self.repository.commit(ChangeSet(tuple(operations), actor, origin, reason, key, base_head)).commit_id

    def prepare_maintenance_merge(
        self,
        *,
        canonical_page_id: str,
        canonical_expected_version: str,
        loser_page_id: str,
        loser_expected_version: str,
        base_head: str,
    ) -> PageMergePlan:
        snapshots.require_head(self.repository.head, base_head)
        snapshot = snapshots.snapshot(self.repository, strict_names=False, at=base_head)
        return page_merge.prepare_plan(
            snapshot,
            canonical_page_id=canonical_page_id,
            canonical_expected_version=canonical_expected_version,
            loser_page_id=loser_page_id,
            loser_expected_version=loser_expected_version,
        )

    def prepare_current_maintenance_merge(
        self,
        *,
        canonical_page_id: str,
        loser_page_id: str,
    ) -> PageMergePlan:
        snapshot = snapshots.snapshot(self.repository, strict_names=False)
        if snapshot.head is None:
            raise WikiValidationError("page merge requires a committed wiki snapshot")
        records = snapshots.index(snapshot, strict_names=False).pages
        canonical = records.get(canonical_page_id)
        loser = records.get(loser_page_id)
        if canonical is None or canonical.page.lifecycle != "active":
            raise KeyError(f"unknown active wiki page: {canonical_page_id}")
        if loser is None or loser.page.lifecycle != "active":
            raise KeyError(f"unknown active wiki page: {loser_page_id}")
        return page_merge.prepare_plan(
            snapshot,
            canonical_page_id=canonical_page_id,
            canonical_expected_version=canonical.resource.version_id,
            loser_page_id=loser_page_id,
            loser_expected_version=loser.resource.version_id,
        )

    def apply_maintenance_merge(
        self,
        plan: PageMergePlan,
        *,
        reason: str,
        actor: str = WIKI_MAINTENANCE_ACTOR,
        origin: str = WIKI_MAINTENANCE_ORIGIN,
    ) -> str:
        snapshot = snapshots.snapshot(self.repository, strict_names=False, at=plan.base_head)
        canonical = page_merge.prepare_plan(
            snapshot,
            canonical_page_id=plan.canonical_page_id,
            canonical_expected_version=plan.canonical_expected_version,
            loser_page_id=plan.loser_page_id,
            loser_expected_version=plan.loser_expected_version,
        )
        if plan != canonical:
            raise WikiValidationError("page merge plan does not match its pinned snapshot")
        canonical_record = snapshots.index(snapshot, strict_names=False).pages[plan.canonical_page_id]
        operations = [Archive(plan.loser_page_id, plan.loser_expected_version)]
        if plan.canonical_content != canonical_record.content:
            operations.append(Update(plan.canonical_page_id, plan.canonical_expected_version, plan.canonical_content))
        operations.extend(Update(item.resource_id, item.expected_version, item.content) for item in plan.rewrites)
        return self.repository.commit(
            ChangeSet(tuple(operations), actor, origin, reason, plan.idempotency_key, plan.base_head)
        ).commit_id

    def restore_maintenance_change(
        self, page_id: str, maintenance_commit_id: str, *, expected_version: str, expected_head: str
    ) -> tuple[WikiPageRecord, str]:
        return changes.restore_maintenance_change(
            self.repository,
            self.read_page,
            self.apply_maintenance_updates,
            self._key,
            page_id,
            maintenance_commit_id,
            expected_version=expected_version,
            expected_head=expected_head,
        )

    def publish_health(self, *, body: bytes, base_head: str | None) -> Commit | None:
        if not isinstance(body, bytes):
            raise TypeError("body must be bytes")
        content = build_page(page_id=WIKI_HEALTH_RESOURCE_ID, title="Wiki health", body=body).to_bytes()
        try:
            existing = self.repository.get(WIKI_HEALTH_RESOURCE_ID, at=base_head)
        except KeyError:
            existing = None
        owner = self.repository.find_by_path(WIKI_HEALTH_PATH, at=base_head, include_archived=True)
        if (existing is not None and existing.state is ResourceState.ARCHIVED) or (
            owner is not None and owner.state is ResourceState.ARCHIVED
        ):
            if self.repository.head != base_head:
                raise RevisionConflictError(
                    f"current head changed: expected {base_head!r}, found {self.repository.head!r}"
                )
            return None
        if existing is None:
            if owner is not None:
                raise WikiValidationError(f"{WIKI_HEALTH_PATH} belongs to a different wiki resource")
            operation = Create(WIKI_HEALTH_RESOURCE_ID, WIKI_HEALTH_PATH, content)
        else:
            if existing.path != WIKI_HEALTH_PATH or owner is None or owner.resource_id != WIKI_HEALTH_RESOURCE_ID:
                raise WikiValidationError(f"{WIKI_HEALTH_PATH} must belong to the health resource")
            if self.repository.read_version(existing) == content:
                if self.repository.head != base_head:
                    raise RevisionConflictError(
                        f"current head changed: expected {base_head!r}, found {self.repository.head!r}"
                    )
                return None
            operation = Update(WIKI_HEALTH_RESOURCE_ID, existing.version_id, content)
        return self.repository.commit(
            ChangeSet(
                (operation,),
                WIKI_HEALTH_ACTOR,
                WIKI_HEALTH_ORIGIN,
                WIKI_HEALTH_REASON,
                self._key("health", base_head, content),
                base_head,
                base_head is None,
            )
        )

    def backlinks(self, page_id: str) -> tuple[LinkReference, ...]:
        return self.link_report(page_id).backlinks

    def links(self, page_id: str) -> tuple[LinkReference, ...]:
        return self.link_report(page_id).outgoing

    def link_report(self, page_id: str, *, at: str | None = None) -> WikiLinkReport:
        return snapshots.link_report(snapshots.snapshot(self.repository, strict_names=False, at=at), page_id)

    def link_report_for_path(self, path: str) -> WikiLinkReport:
        snapshot = snapshots.snapshot(self.repository, strict_names=False)
        page_index = snapshots.index(snapshot, strict_names=False)
        record = next((item for item in snapshot.pages if item.resource.path == path), None)
        if record is None:
            raise KeyError(f"unknown wiki path: {path}")
        page_id = snapshots.follow_redirect(page_index, record.page.page_id)
        if page_id is None:
            raise WikiValidationError(f"redirect at {path} has no active target")
        return snapshots.link_report(snapshot, page_id)

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
        snapshots.require_head(snapshot.head, base_head)
        record = snapshots.index(snapshot).pages.get(page_id)
        if record is None or record.page.lifecycle != "active":
            raise KeyError(f"unknown active wiki page: {page_id}")
        if page_id == WIKI_HEALTH_RESOURCE_ID:
            raise WikiValidationError("health page is backend-managed")
        snapshots.validate_prospective(snapshot, tuple(item for item in snapshot.pages if item.page.page_id != page_id))
        version = expected_version or record.resource.version_id
        key = idempotency_key or self._key("archive", snapshot.head, page_id, version)
        self.repository.commit(ChangeSet((Archive(page_id, version),), actor, origin, reason, key, snapshot.head))

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
        if page_id == WIKI_HEALTH_RESOURCE_ID:
            raise WikiValidationError("health page is backend-managed")
        head = self.repository.head
        snapshots.require_head(head, base_head)
        resource = self.repository.get(page_id, at=head)
        if resource.state is not ResourceState.ARCHIVED:
            raise WikiValidationError(f"page is not archived: {page_id}")
        content = self.repository.read_version(resource)
        page = snapshots.parse(resource, content)
        active = snapshots.snapshot(self.repository, strict_names=True, at=head)
        snapshots.assert_new_names(active, page, resource.path)
        snapshots.validate_prospective(active, (*active.pages, WikiPageRecord(resource, page, content)))
        version = expected_version or resource.version_id
        key = idempotency_key or self._key("restore", head, page_id, version)
        self.repository.commit(ChangeSet((Restore(page_id, version),), actor, origin, reason, key, head))
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
        snapshots.require_markdown_path(new_path)
        snapshot = self.snapshot()
        snapshots.require_head(snapshot.head, base_head)
        return rename.prepare_plan(
            self.repository,
            snapshot,
            page_id=page_id,
            new_path=new_path,
            new_title=new_title,
            expected_version=expected_version,
            rewrite_links=rewrite_links,
        )

    def apply_rename(
        self,
        plan: RenamePlan,
        *,
        actor: str = WIKI_RENAME_ACTOR,
        origin: str = WIKI_RENAME_ORIGIN,
        reason: str = WIKI_RENAME_REASON,
    ) -> Commit:
        if not isinstance(plan, RenamePlan):
            raise TypeError("plan must be a RenamePlan")
        canonical = rename.prepare_plan(
            self.repository,
            snapshots.snapshot(self.repository, strict_names=True, at=plan.base_head),
            page_id=plan.page_id,
            new_path=plan.new_path,
            new_title=plan.new_title,
            expected_version=plan.expected_version,
            rewrite_links=plan.rewrite_links,
        )
        if plan != canonical:
            raise WikiValidationError("rename plan does not match its pinned snapshot")
        redirect = build_page(
            page_id=plan.redirect_page_id, title=plan.old_title, lifecycle="redirect", redirect_to=plan.page_id
        ).to_bytes()
        operations = [
            Move(plan.page_id, plan.expected_version, plan.new_path, plan.moved_content),
            Create(plan.redirect_page_id, plan.old_path, redirect),
        ]
        operations.extend(Update(item.resource_id, item.expected_version, item.content) for item in plan.rewrites)
        return self.repository.commit(
            ChangeSet(tuple(operations), actor, origin, reason, plan.idempotency_key, plan.base_head)
        )

    def _last_generated_region(
        self, page_id: str, actor: str, origin: str, base_head: str
    ) -> tuple[bool, bytes | None]:
        trusted_exists = False
        trusted: bytes | None = None
        for commit in reversed(self.repository.history(resource_id=page_id, start=base_head)):
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
            if change.after is None:
                raise CorruptRepositoryError(
                    f"commit {commit.commit_id} selected an active wiki change without an after version"
                )
            after = extract_generated_region(self.repository.read_version(change.after), expected_page_id=page_id)
            if commit.actor == actor and commit.origin == origin:
                trusted_exists = True
                trusted = after
                continue
            if (
                trusted_exists
                and commit.actor == WIKI_RENAME_ACTOR
                and commit.origin == WIKI_RENAME_ORIGIN
                and commit.reason == WIKI_RENAME_REASON
                and change.before is not None
                and change.before.state is ResourceState.ACTIVE
            ):
                before = extract_generated_region(self.repository.read_version(change.before), expected_page_id=page_id)
                if before == trusted:
                    trusted = after
        return trusted_exists, trusted

    @staticmethod
    def _generated_region_is_safe(
        *, current: bytes | None, desired: bytes, prior_exists: bool, prior: bytes | None
    ) -> bool:
        return current == desired or (current == prior if prior_exists else current is None or current == b"")

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
    def _key(*parts: object) -> str:
        return "wiki-" + sha256(repr(parts).encode()).hexdigest()
