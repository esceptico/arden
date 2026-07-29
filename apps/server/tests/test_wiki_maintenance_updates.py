from pathlib import Path

import pytest

from arden.revisions import Archive, ChangeSet, Create, ManagedFileRepository, RevisionConflictError, Update
from arden.wiki.models import WikiMaintenancePageUpdate
from arden.wiki.pages import create_page
from arden.wiki.service import (
    GeneratedRegionConflictError,
    WikiAmbiguityError,
    WikiService,
    WikiValidationError,
)


def _repo(tmp_path: Path) -> ManagedFileRepository:
    return ManagedFileRepository(tmp_path / "wiki")


def _seed(repo: ManagedFileRepository, *pages: tuple[str, str, object]) -> None:
    repo.commit(
        ChangeSet(
            operations=tuple(Create(page_id, path, page.to_bytes()) for page_id, path, page in pages),
            actor="test",
            origin="test",
            reason="seed wiki",
            idempotency_key="seed-" + "-".join(page_id for page_id, _path, _page in pages),
        )
    )


def _update(
    service: WikiService, page_id: str, *, title: str, aliases: tuple[str, ...], body: bytes
) -> WikiMaintenancePageUpdate:
    record = service.read_page(page_id)
    return WikiMaintenancePageUpdate(page_id, record.resource.version_id, title, aliases, body)


def test_topic_name_resolution_uses_only_titles_and_aliases_from_one_snapshot(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _seed(
        repo,
        ("bicycle", "topics/bike.md", create_page(page_id="bicycle", title="Bicycle", aliases=("Cycle",))),
    )
    service = WikiService(repo)
    snapshot = service.snapshot()

    assert service.resolve_topic_name("bIcYcLe", snapshot=snapshot).page.page_id == "bicycle"
    assert service.resolve_topic_name(" cycle ", snapshot=snapshot).page.page_id == "bicycle"
    assert service.resolve_topic_name("topics/bike", snapshot=snapshot) is None
    assert service.resolve_topic_name("bike.md", snapshot=snapshot) is None


def test_maintenance_updates_are_atomic_identity_preserving_and_owned_by_maintenance(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    generated = b"<!-- generated -->\nFact.\n<!-- /generated -->\n\nNotes.\n"
    page = create_page(
        page_id="topic",
        title="Topic",
        aliases=("Old topic",),
        body=generated,
        metadata={
            "generated_from_revision": "a" * 64,
            "fact_citations": [{"fact_id": "fact-1", "version": "b" * 64}],
        },
    )
    _seed(repo, ("topic", "topics/topic.md", page))
    service = WikiService(repo)
    before = service.read_page("topic")

    head = service.apply_maintenance_updates(
        (
            _update(
                service,
                "topic",
                title="Better topic",
                aliases=("Topic",),
                body=b"<!-- generated -->\nFact.\n<!-- /generated -->\n\nImproved notes.\n",
            ),
        ),
        base_head=repo.head,
        reason="clarify topic notes",
    )

    assert head == repo.head
    commit = repo.history(start=head)[0]
    assert (commit.actor, commit.origin, commit.reason) == (
        "Wiki Maintenance",
        "wiki.maintenance",
        "clarify topic notes",
    )
    after = service.read_page("topic")
    assert (after.resource.path, after.page.page_id, after.page.lifecycle, after.page.redirect_to) == (
        before.resource.path,
        before.page.page_id,
        before.page.lifecycle,
        before.page.redirect_to,
    )
    assert after.page.metadata["generated_from_revision"] == "a" * 64
    assert after.page.metadata["fact_citations"] == before.page.metadata["fact_citations"]
    assert b"<!-- generated -->\nFact.\n<!-- /generated -->" in after.content


def test_maintenance_rejects_generated_edits_and_name_collisions_without_partial_commit(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    generated = b"<!-- generated -->\nFact.\n<!-- /generated -->\n"
    _seed(
        repo,
        ("one", "one.md", create_page(page_id="one", title="One", body=generated)),
        ("two", "two.md", create_page(page_id="two", title="Two")),
    )
    service = WikiService(repo)
    initial = repo.head

    with pytest.raises(GeneratedRegionConflictError):
        service.apply_maintenance_updates(
            (_update(service, "one", title="One", aliases=(), body=b"changed\n"),),
            base_head=initial,
        )
    assert repo.head == initial

    with pytest.raises(WikiAmbiguityError):
        service.apply_maintenance_updates(
            (
                _update(service, "one", title="Changed", aliases=(), body=generated),
                _update(service, "two", title="Changed", aliases=(), body=b""),
            ),
            base_head=initial,
        )
    assert repo.head == initial
    assert service.read_page("one").page.title == "One"


def test_maintenance_requires_exact_head_and_resource_versions_even_for_noop(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _seed(repo, ("one", "one.md", create_page(page_id="one", title="One")))
    service = WikiService(repo)
    base = repo.head
    update = _update(service, "one", title="One", aliases=(), body=b"")

    assert service.apply_maintenance_updates((update,), base_head=base) == base
    assert repo.head == base

    repo.commit(
        ChangeSet(
            operations=(Create("other", "other.md", create_page(page_id="other", title="Other").to_bytes()),),
            actor="test",
            origin="test",
            reason="move head",
            idempotency_key="move-head",
            expected_head=base,
        )
    )
    with pytest.raises(RevisionConflictError, match="current head changed"):
        service.apply_maintenance_updates((update,), base_head=base)
    current = repo.get("one")
    repo.commit(
        ChangeSet(
            operations=(Update("one", current.version_id, repo.read("one") + b"\n"),),
            actor="test",
            origin="test",
            reason="change page",
            idempotency_key="change-one",
            expected_head=repo.head,
        )
    )
    with pytest.raises(RevisionConflictError, match="resource one changed"):
        service.apply_maintenance_updates((update,), base_head=repo.head)


def test_maintenance_never_recreates_or_edits_archived_pages(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _seed(repo, ("one", "one.md", create_page(page_id="one", title="One")))
    record = repo.get("one")
    repo.commit(
        ChangeSet(
            operations=(Archive("one", record.version_id),),
            actor="test",
            origin="test",
            reason="archive",
            idempotency_key="archive-one",
            expected_head=repo.head,
        )
    )

    with pytest.raises(KeyError, match="unknown active wiki page"):
        WikiService(repo).apply_maintenance_updates(
            (WikiMaintenancePageUpdate("one", repo.get("one").version_id, "One", (), b""),),
            base_head=repo.head,
        )


def test_maintenance_cannot_edit_the_backend_owned_health_page(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    service = WikiService(repo)
    assert service.publish_health(body=b"Healthy.\n", base_head=None) is not None
    initial = repo.head

    with pytest.raises(WikiValidationError, match="backend-owned"):
        service.apply_maintenance_updates(
            (_update(service, "health", title="Changed", aliases=(), body=b"Model edit.\n"),),
            base_head=initial,
        )

    assert repo.head == initial
    assert service.read_page("health").page.body == b"Healthy.\n"


def test_maintenance_retry_replays_before_head_compare_and_swap(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _seed(repo, ("one", "one.md", create_page(page_id="one", title="One")))
    service = WikiService(repo)
    base = repo.head
    update = _update(service, "one", title="One", aliases=(), body=b"Maintained.\n")

    committed = service.apply_maintenance_updates(
        (update,),
        base_head=base,
        reason="maintain one",
        idempotency_key="maintenance-one",
    )
    other = create_page(page_id="other", title="Other")
    repo.commit(
        ChangeSet(
            operations=(Create("other", "other.md", other.to_bytes()),),
            actor="test",
            origin="test",
            reason="later unrelated commit",
            idempotency_key="later-unrelated",
            expected_head=committed,
        )
    )

    replayed = service.apply_maintenance_updates(
        (update,),
        base_head=base,
        reason="maintain one",
        idempotency_key="maintenance-one",
    )
    assert replayed == committed
    assert repo.head != replayed


def test_restore_maintenance_change_restores_fields_and_records_user_provenance(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    generated = b"<!-- generated -->\nCanonical fact.\n<!-- /generated -->"
    original = create_page(
        page_id="topic",
        title="Original topic",
        aliases=("Original alias",),
        body=generated + b"\n\nOriginal notes.\n",
        metadata={
            "generated_from_revision": "a" * 64,
            "fact_citations": [{"fact_id": "fact-1", "version": "b" * 64}],
        },
    )
    _seed(repo, ("topic", "topics/topic.md", original))
    service = WikiService(repo)
    before = service.read_page("topic")
    maintenance_commit_id = service.apply_maintenance_updates(
        (
            _update(
                service,
                "topic",
                title="Maintained topic",
                aliases=("Maintained alias",),
                body=generated + b"\n\nMaintained notes.\n",
            ),
        ),
        base_head=repo.head,
        reason="clarify topic",
    )
    maintained = service.read_page("topic")

    restored, restore_commit_id = service.restore_maintenance_change(
        "topic",
        maintenance_commit_id,
        expected_version=maintained.resource.version_id,
        expected_head=maintenance_commit_id,
    )

    assert restore_commit_id == repo.head
    assert (restored.page.title, restored.page.aliases, restored.page.body) == (
        before.page.title,
        before.page.aliases,
        before.page.body,
    )
    assert restored.page.metadata == maintained.page.metadata
    assert b"<!-- generated -->\nCanonical fact.\n<!-- /generated -->" in restored.content
    restore_commit = repo.history(start=restore_commit_id, limit=1)[0]
    assert (restore_commit.actor, restore_commit.origin) == ("user:desktop", "desktop.restore")
    assert restore_commit.reason == f"restore Maintenance commit {maintenance_commit_id}"


def test_restore_maintenance_change_rejects_nonmaintenance_and_nonupdate_commits(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _seed(repo, ("one", "one.md", create_page(page_id="one", title="One")))
    service = WikiService(repo)
    before = service.read_page("one")
    _record, user_commit_id = service.update_page_with_commit(
        "one",
        content=before.page.with_body(b"User edit.\n").to_bytes(),
        expected_version=before.resource.version_id,
        expected_head=repo.head,
    )
    assert user_commit_id is not None
    current = service.read_page("one")

    with pytest.raises(WikiValidationError, match="not a Wiki Maintenance change"):
        service.restore_maintenance_change(
            "one",
            user_commit_id,
            expected_version=current.resource.version_id,
            expected_head=repo.head,
        )

    created = service.create_page(
        path="two.md",
        title="Two",
        page_id="two",
        expected_head=repo.head,
        actor="Wiki Maintenance",
        origin="wiki.maintenance",
    )
    create_commit_id = repo.head
    assert create_commit_id is not None
    with pytest.raises(WikiValidationError, match="exactly one page update"):
        service.restore_maintenance_change(
            "two",
            create_commit_id,
            expected_version=created.resource.version_id,
            expected_head=create_commit_id,
        )


def test_restore_maintenance_change_rejects_an_unreachable_commit(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _seed(repo, ("one", "one.md", create_page(page_id="one", title="One")))
    service = WikiService(repo)
    current = service.read_page("one")

    foreign_repo = _repo(tmp_path / "foreign")
    _seed(foreign_repo, ("one", "one.md", create_page(page_id="one", title="One")))
    foreign_service = WikiService(foreign_repo)
    foreign_commit = foreign_service.apply_maintenance_updates(
        (_update(foreign_service, "one", title="Foreign", aliases=(), body=b"Foreign.\n"),),
        base_head=foreign_repo.head,
    )

    with pytest.raises(KeyError, match="not a reachable change"):
        service.restore_maintenance_change(
            "one",
            foreign_commit,
            expected_version=current.resource.version_id,
            expected_head=repo.head,
        )


def test_restore_maintenance_change_rejects_stale_head_version_and_later_page_edit(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _seed(repo, ("one", "one.md", create_page(page_id="one", title="One")))
    service = WikiService(repo)
    before = service.read_page("one")
    maintenance_commit_id = service.apply_maintenance_updates(
        (_update(service, "one", title="One", aliases=(), body=b"Maintained.\n"),),
        base_head=repo.head,
    )
    maintained = service.read_page("one")

    with pytest.raises(RevisionConflictError, match="resource one changed"):
        service.restore_maintenance_change(
            "one",
            maintenance_commit_id,
            expected_version=before.resource.version_id,
            expected_head=maintenance_commit_id,
        )

    other = create_page(page_id="other", title="Other")
    repo.commit(
        ChangeSet(
            operations=(Create("other", "other.md", other.to_bytes()),),
            actor="test",
            origin="test",
            reason="later unrelated change",
            idempotency_key="later-unrelated-restore",
            expected_head=repo.head,
        )
    )
    with pytest.raises(RevisionConflictError, match="current head changed"):
        service.restore_maintenance_change(
            "one",
            maintenance_commit_id,
            expected_version=maintained.resource.version_id,
            expected_head=maintenance_commit_id,
        )

    current_head = repo.head
    current = service.read_page("one")
    _record, later_page_commit = service.update_page_with_commit(
        "one",
        content=current.page.with_body(b"Later user edit.\n").to_bytes(),
        expected_version=current.resource.version_id,
        expected_head=current_head,
    )
    assert later_page_commit is not None
    later = service.read_page("one")
    with pytest.raises(RevisionConflictError, match="changed after Maintenance commit"):
        service.restore_maintenance_change(
            "one",
            maintenance_commit_id,
            expected_version=later.resource.version_id,
            expected_head=later_page_commit,
        )


def test_restore_maintenance_change_allows_a_later_unrelated_commit(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    _seed(repo, ("one", "one.md", create_page(page_id="one", title="One")))
    service = WikiService(repo)
    original = service.read_page("one")
    maintenance_commit_id = service.apply_maintenance_updates(
        (_update(service, "one", title="Maintained", aliases=(), body=b"Maintained.\n"),),
        base_head=repo.head,
    )
    maintained = service.read_page("one")
    service.create_page(path="other.md", title="Other", page_id="other", expected_head=repo.head)
    current_head = repo.head
    assert current_head is not None

    restored, _restore_commit_id = service.restore_maintenance_change(
        "one",
        maintenance_commit_id,
        expected_version=maintained.resource.version_id,
        expected_head=current_head,
    )

    assert (restored.page.title, restored.page.body) == (original.page.title, original.page.body)
    assert service.read_page("other").page.title == "Other"


def test_restore_maintenance_change_rejects_name_and_generated_region_violations(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    generated = b"<!-- generated -->\nFact.\n<!-- /generated -->\n"
    _seed(
        repo,
        ("target", "target.md", create_page(page_id="target", title="Old", body=generated)),
        ("source", "source.md", create_page(page_id="source", title="Source", body=b"See [[Old]].\n")),
    )
    service = WikiService(repo)
    maintenance_commit_id = service.apply_maintenance_updates(
        (_update(service, "target", title="New", aliases=(), body=generated),),
        base_head=repo.head,
    )
    maintained = service.read_page("target")
    service.create_page(path="other.md", title="Old", page_id="other", expected_head=repo.head)
    collision_head = repo.head
    with pytest.raises(WikiAmbiguityError, match="ambiguous wiki name"):
        service.restore_maintenance_change(
            "target",
            maintenance_commit_id,
            expected_version=maintained.resource.version_id,
            expected_head=collision_head,
        )
    assert repo.head == collision_head

    region_repo = _repo(tmp_path / "region")
    _seed(
        region_repo,
        ("topic", "topic.md", create_page(page_id="topic", title="Topic", body=generated)),
    )
    original = region_repo.get("topic")
    changed_generated = create_page(
        page_id="topic",
        title="Topic",
        body=b"<!-- generated -->\nChanged.\n<!-- /generated -->\n",
    )
    region_commit = region_repo.commit(
        ChangeSet(
            operations=(Update("topic", original.version_id, changed_generated.to_bytes()),),
            actor="Wiki Maintenance",
            origin="wiki.maintenance",
            reason="invalid generated edit",
            idempotency_key="invalid-generated-edit",
            expected_head=region_repo.head,
        )
    )
    region_service = WikiService(region_repo)
    changed = region_service.read_page("topic")
    with pytest.raises(GeneratedRegionConflictError, match="generated page content"):
        region_service.restore_maintenance_change(
            "topic",
            region_commit.commit_id,
            expected_version=changed.resource.version_id,
            expected_head=region_commit.commit_id,
        )


def test_restore_maintenance_change_rejects_invalid_historical_identity(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    invalid = create_page(page_id="wrong-id", title="Invalid")
    seed = repo.commit(
        ChangeSet(
            operations=(Create("topic", "topic.md", invalid.to_bytes()),),
            actor="test",
            origin="test",
            reason="seed invalid identity",
            idempotency_key="seed-invalid-identity",
        )
    )
    valid = create_page(page_id="topic", title="Valid")
    maintenance = repo.commit(
        ChangeSet(
            operations=(Update("topic", repo.get("topic").version_id, valid.to_bytes()),),
            actor="Wiki Maintenance",
            origin="wiki.maintenance",
            reason="repair identity",
            idempotency_key="repair-invalid-identity",
            expected_head=seed.commit_id,
        )
    )
    service = WikiService(repo)
    current = service.read_page("topic")

    with pytest.raises(WikiValidationError, match="page_id does not match"):
        service.restore_maintenance_change(
            "topic",
            maintenance.commit_id,
            expected_version=current.resource.version_id,
            expected_head=maintenance.commit_id,
        )
