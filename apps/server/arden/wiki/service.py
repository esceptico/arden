"""Snapshot-pinned wiki operations over :mod:`arden.revisions`."""

from __future__ import annotations

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
    Update,
)
from arden.revisions.errors import RevisionConflictError

from .models import (
    GeneratedPageTarget,
    LinkReference,
    LinkStatus,
    RenamePlan,
    RenameRewrite,
    WikiLinkReport,
    WikiPageRecord,
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
                for name in self._names(page, target.path):
                    normalized = self._normal(name)
                    if normalized in prospective_names:
                        raise WikiAmbiguityError(f"wiki name already exists: {normalized}")
                    prospective_names[normalized] = target.page_id
            else:
                current = extract_generated_region(record.content, expected_page_id=target.page_id)
                prior_exists, prior = self._last_synthesis_region(target.page_id, origin, base_head)
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

    def _last_synthesis_region(self, page_id: str, origin: str, base_head: str) -> tuple[bool, bytes | None]:
        for commit in self.repository.history(resource_id=page_id, start=base_head):
            if commit.origin != origin:
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
