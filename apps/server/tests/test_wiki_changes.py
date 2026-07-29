from datetime import UTC, datetime
from pathlib import Path

import pytest

from arden.revisions import (
    Archive,
    ChangeSet,
    Create,
    IntegrityReport,
    ManagedFileRepository,
    Move,
    ResourceState,
    ResourceVersion,
    Restore,
    StorageReport,
    Update,
)
from arden.revisions.errors import RevisionConflictError
from arden.wiki.changes import storage_warnings as _storage_warnings
from arden.wiki.models import (
    LinkStatus,
    WikiChangeCommit,
    WikiChangesReport,
    WikiChangeWarning,
    WikiFactCitation,
    WikiInfrastructureRole,
    WikiPageRevision,
    WikiResourceChange,
)
from arden.wiki.pages import create_page
from arden.wiki.service import WikiService


def _repo(tmp_path: Path) -> ManagedFileRepository:
    return ManagedFileRepository(tmp_path / "wiki")


def _commit(repo: ManagedFileRepository, operations, *, actor: str = "test", reason: str = "change"):
    return repo.commit(
        ChangeSet(
            operations=tuple(operations),
            actor=actor,
            origin="test.origin",
            reason=reason,
            idempotency_key=f"{reason}-{repo.head}",
            expected_head=repo.head,
            enforce_expected_head=repo.head is None,
        )
    )


def test_changes_since_empty_and_noop_watermark(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    service = WikiService(repo)

    empty = service.changes_since(None)
    assert empty.through_revision is None
    assert empty.commits == ()

    created = service.create_page(path="one.md", title="One", page_id="one")
    no_op = service.changes_since(repo.head)
    assert no_op.through_revision == repo.head
    assert no_op.commits == ()
    assert created.page.page_id == "one"


def test_changes_since_keeps_non_markdown_commits_in_the_contiguous_chain(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    page = create_page(title="One", page_id="one")
    markdown = _commit(repo, (Create("one", "one.md", page.to_bytes()),), reason="page")
    attachment = _commit(repo, (Create("asset", "assets/data.bin", b"\x00\x01"),), reason="attachment")

    report = WikiService(repo).changes_since(markdown.commit_id)

    assert report.through_revision == attachment.commit_id
    assert [commit.commit_id for commit in report.commits] == [attachment.commit_id]
    assert report.commits[0].changes == ()


def test_changes_since_scopes_diff_work_to_markdown_and_can_omit_it(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    page = create_page(title="One", page_id="one")
    _commit(
        repo,
        (
            Create("one", "one.md", page.to_bytes()),
            Create("asset", "assets/data.bin", b"private attachment"),
        ),
        reason="mixed",
    )
    service = WikiService(repo)
    original_diff = repo.diff
    selected: list[tuple[str, ...] | None] = []

    def capture_diff(base, target, *, resource_ids=None):
        selected.append(resource_ids)
        return original_diff(base, target, resource_ids=resource_ids)

    monkeypatch.setattr(repo, "diff", capture_diff)
    report = service.changes_since(None)
    assert selected == [("one",)]
    assert "+title: One" in report.commits[0].changes[0].unified_diff

    def reject_diff(*_args, **_kwargs):
        raise AssertionError("metadata-only feeds must not build diffs")

    monkeypatch.setattr(repo, "diff", reject_diff)
    metadata_only = service.changes_since(None, include_diffs=False)
    assert metadata_only.commits[0].changes == ()


def test_changes_since_rejects_invalid_unknown_and_unreachable_watermarks(tmp_path: Path) -> None:
    service = WikiService(_repo(tmp_path))
    with pytest.raises(ValueError, match="watermark"):
        service.changes_since("")
    with pytest.raises(KeyError, match="not reachable"):
        service.changes_since("not-a-commit")

    service.create_page(path="one.md", title="One", page_id="one")
    with pytest.raises(KeyError, match="not reachable"):
        service.changes_since("0" * 64)


def test_changes_since_is_chronological_and_includes_content_move_archive_restore(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    page = create_page(title="One", page_id="one")
    first = _commit(repo, (Create("one", "one.md", page.to_bytes()),), reason="create")
    second = _commit(
        repo,
        (Update("one", repo.get("one").version_id, page.with_body(b"Edited\n").to_bytes()),),
        reason="edit",
    )
    third = _commit(repo, (Move("one", repo.get("one").version_id, "notes/one.md"),), reason="move")
    fourth = _commit(repo, (Archive("one", repo.get("one").version_id),), reason="archive")
    fifth = _commit(repo, (Restore("one", repo.get("one").version_id),), reason="restore")

    report = WikiService(repo).changes_since(None)
    assert [commit.commit_id for commit in report.commits] == [
        first.commit_id,
        second.commit_id,
        third.commit_id,
        fourth.commit_id,
        fifth.commit_id,
    ]
    assert [commit.changes[0].action for commit in report.commits] == ["create", "update", "move", "archive", "restore"]
    assert "Edited" in report.commits[1].changes[0].unified_diff
    assert report.commits[2].changes[0].before.resource.path == "one.md"
    assert report.commits[2].changes[0].after.resource.path == "notes/one.md"
    assert report.commits[3].changes[0].after.resource.state.value == "archived"
    assert report.commits[4].changes[0].after.resource.state.value == "active"

    since_second = WikiService(repo).changes_since(second.commit_id)
    assert [commit.commit_id for commit in since_second.commits] == [third.commit_id, fourth.commit_id, fifth.commit_id]


def test_changes_include_actor_diff_and_synthesis_provenance(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    page = create_page(
        title="Topic",
        page_id="topic",
        metadata={
            "generated_from_revision": "f" * 64,
            "fact_citations": [{"fact_id": "fact-1", "version": "a" * 64}],
        },
        body=b"Text\n",
    )
    commit = _commit(repo, (Create("topic", "topics/topic.md", page.to_bytes()),), actor="Synthesis", reason="publish")

    change = WikiService(repo).changes_since(None).commits[0]
    revision = change.changes[0].after
    assert change.actor == "Synthesis"
    assert change.origin == "test.origin"
    assert change.reason == "publish"
    assert revision is not None
    assert revision.page is not None
    assert revision.generated_from_revision == "f" * 64
    assert revision.fact_citations[0].fact_id == "fact-1"
    assert "+Text" in change.changes[0].unified_diff
    assert change.commit_id == commit.commit_id


def test_changes_include_redirect_pages_without_a_page_type(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    target = create_page(title="New", page_id="target")
    redirect = create_page(title="Old", page_id="old-redirect", lifecycle="redirect", redirect_to="target")
    _commit(
        repo,
        (Create("target", "new.md", target.to_bytes()), Create("old-redirect", "old.md", redirect.to_bytes())),
        reason="rename",
    )

    changes = WikiService(repo).changes_since(None).commits[0].changes
    redirected = next(change for change in changes if change.resource_id == "old-redirect").after
    assert redirected is not None
    assert redirected.page is not None
    assert redirected.page.lifecycle == "redirect"
    assert redirected.page.redirect_to == "target"
    assert redirected.role is WikiInfrastructureRole.COMMON


def test_changes_expose_current_links_backlinks_and_mechanical_warnings(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    target = create_page(title="Target", page_id="target")
    source = create_page(title="Source", page_id="source", body=b"[[Target]] [[Missing]]\n")
    _commit(repo, (Create("target", "target.md", target.to_bytes()), Create("source", "source.md", source.to_bytes())))

    report = WikiService(repo).changes_since(None)
    target_change = next(change for change in report.commits[0].changes if change.resource_id == "target")
    source_change = next(change for change in report.commits[0].changes if change.resource_id == "source")
    assert [reference.source_page_id for reference in target_change.current_backlinks] == ["source"]
    assert [reference.status for reference in source_change.current_outgoing] == [
        LinkStatus.RESOLVED,
        LinkStatus.UNRESOLVED,
    ]
    assert any(warning.code == "unresolved_link" and warning.target == "source.md" for warning in report.warnings)


def test_invalid_markdown_is_reported_without_losing_history(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    valid = create_page(title="Valid", page_id="valid")
    _commit(repo, (Create("valid", "valid.md", valid.to_bytes()),), reason="valid")
    bad = _commit(repo, (Create("bad", "bad.md", b"not a page\n"),), reason="bad")

    report = WikiService(repo).changes_since(None)
    bad_change = next(commit for commit in report.commits if commit.commit_id == bad.commit_id).changes[0]
    assert bad_change.after is not None
    assert bad_change.after.page is None
    assert "invalid wiki page" in bad_change.after.validation_error
    assert any(warning.code == "invalid_page" and warning.target == "bad.md" for warning in report.warnings)
    assert [commit.reason for commit in report.commits] == ["valid", "bad"]


def test_invalid_provenance_is_visible_without_invalidating_the_page(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    page = create_page(
        title="Topic",
        page_id="topic",
        metadata={
            "generated_from_revision": "not-a-revision",
            "fact_citations": [
                {"fact_id": "", "version": "a" * 64},
                {"fact_id": "good", "version": "not-a-revision"},
                "not a citation",
            ],
        },
    )
    _commit(repo, (Create("topic", "topic.md", page.to_bytes()),))

    report = WikiService(repo).changes_since(None)
    revision = report.commits[0].changes[0].after
    assert revision is not None
    assert revision.page is not None
    assert revision.validation_error is None
    assert revision.generated_from_revision is None
    assert revision.fact_citations == ()
    assert {warning.code for warning in revision.provenance_warnings} == {
        "invalid_generated_from_revision",
        "invalid_fact_citations",
    }
    assert {warning.code for warning in report.warnings} >= {
        "invalid_generated_from_revision",
        "invalid_fact_citations",
    }


def test_nonlist_provenance_is_visible_without_invalidating_the_page(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    page = create_page(
        title="Topic",
        page_id="topic",
        metadata={"generated_from_revision": 7, "fact_citations": "not a list"},
    )
    _commit(repo, (Create("topic", "topic.md", page.to_bytes()),))

    revision = WikiService(repo).changes_since(None).commits[0].changes[0].after
    assert revision is not None and revision.page is not None
    assert [warning.code for warning in revision.provenance_warnings] == [
        "invalid_generated_from_revision",
        "invalid_fact_citations",
    ]


def test_storage_warning_thresholds_are_pure_and_exact() -> None:
    def report(total_bytes: int) -> StorageReport:
        return StorageReport(total_bytes, 0, 0, 0, 0, 0, 0, 0, 0)

    assert _storage_warnings(report(50 * 1024 * 1024 - 1), "history") == ()
    assert [warning.code for warning in _storage_warnings(report(50 * 1024 * 1024), "history")] == [
        "storage_inspection"
    ]
    assert [warning.code for warning in _storage_warnings(report(100 * 1024 * 1024), "history")] == [
        "storage_needs_attention"
    ]


def test_changes_since_fails_if_the_head_changes_while_deriving_the_report(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    page = create_page(title="One", page_id="one")
    _commit(repo, (Create("one", "one.md", page.to_bytes()),))
    original = repo.storage_report

    def storage_report_then_commit() -> StorageReport:
        result = original()
        later = create_page(title="Later", page_id="later")
        _commit(repo, (Create("later", "later.md", later.to_bytes()),), reason="concurrent")
        return result

    monkeypatch.setattr(repo, "storage_report", storage_report_then_commit)
    with pytest.raises(RevisionConflictError, match="building a change feed"):
        WikiService(repo).changes_since(None)


def test_change_feed_models_are_plain_values() -> None:
    resource = ResourceVersion("one", "one.md", "a" * 64, ResourceState.ACTIVE, "b" * 64)
    page = create_page(title="One", page_id="one")
    citation = WikiFactCitation("fact-1", "c" * 64)
    revision = WikiPageRevision(
        resource=resource,
        content=page.to_bytes(),
        page=page,
        validation_error=None,
        role=WikiInfrastructureRole.COMMON,
        generated_from_revision=None,
        fact_citations=[citation],
    )
    change = WikiResourceChange("create", "one", None, revision, "", [], [])
    changes = [change]
    commit = WikiChangeCommit(
        "d" * 64,
        None,
        "test",
        "test",
        "test",
        datetime.now(UTC),
        changes,
    )
    changes.append(change)
    report = WikiChangesReport(
        None,
        "d" * 64,
        [commit],
        [WikiChangeWarning("notice", "one.md", "evidence")],
        IntegrityReport(True, None, 0, 0, ()),
        StorageReport(0, 0, 0, 0, 0, 0, 0, 0, 0),
    )
    assert revision.fact_citations == [citation]
    assert len(commit.changes) == 2
    assert report.commits == [commit]


def test_change_feed_page_metadata_is_deeply_immutable(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    page = create_page(
        title="One",
        page_id="one",
        metadata={"nested": {"items": [{"value": 1}]}},
    )
    _commit(repo, (Create("one", "one.md", page.to_bytes()),))

    revision = WikiService(repo).changes_since(None).commits[0].changes[0].after
    assert revision is not None and revision.page is not None
    nested = revision.page.metadata["nested"]
    with pytest.raises(TypeError):
        nested["new"] = True
    with pytest.raises(TypeError):
        nested["items"][0]["value"] = 2


@pytest.mark.parametrize(
    ("page_id", "path", "role"),
    [
        ("me", "custom.md", WikiInfrastructureRole.ME),
        ("other", "active-work.md", WikiInfrastructureRole.ACTIVE_WORK),
        ("readme", "README.md", WikiInfrastructureRole.README),
        ("nested-readme", "projects/README.md", WikiInfrastructureRole.README),
        ("daily", "daily/2026-07-28.md", WikiInfrastructureRole.DAILY),
        ("insight", "insights/july.md", WikiInfrastructureRole.INSIGHT),
        ("ordinary", "projects/one.md", WikiInfrastructureRole.COMMON),
    ],
)
def test_roles_use_only_stable_id_path_or_folder_contracts(
    tmp_path: Path, page_id: str, path: str, role: WikiInfrastructureRole
) -> None:
    repo = _repo(tmp_path)
    page = create_page(title="Anything", page_id=page_id, metadata={"page_type": "misleading"})
    _commit(repo, (Create(page_id, path, page.to_bytes()),))

    revision = WikiService(repo).changes_since(None).commits[0].changes[0].after
    assert revision is not None
    assert revision.role is role


def test_disconnected_pages_do_not_create_orphan_warnings(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    pages = (
        ("me", "me.md"),
        ("active-work", "active-work.md"),
        ("readme", "README.md"),
        ("nested-readme", "nested/README.md"),
        ("daily", "daily/2026-07-28.md"),
        ("insight", "insights/2026-07.md"),
        ("common", "notes/common.md"),
    )
    _commit(
        repo,
        tuple(
            Create(page_id, path, create_page(title=page_id.title(), page_id=page_id).to_bytes())
            for page_id, path in pages
        ),
    )

    warnings = WikiService(repo).changes_since(None).warnings
    assert all(warning.code != "orphan_page" for warning in warnings)
