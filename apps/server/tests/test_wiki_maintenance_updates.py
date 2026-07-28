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
