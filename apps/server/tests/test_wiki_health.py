from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from arden.revisions import Archive, ChangeSet, Create, ManagedFileRepository, RevisionConflictError
from arden.wiki import (
    WikiChangeWarning,
    WikiHealthIndex,
    WikiHealthInput,
    WikiHealthIssue,
    WikiHealthIssueCode,
    WikiHealthPendingReview,
    WikiHealthProjector,
    WikiHealthWorker,
    WikiService,
    create_page,
)

if TYPE_CHECKING:
    from pathlib import Path


def _repo(tmp_path: Path) -> ManagedFileRepository:
    return ManagedFileRepository(tmp_path / "wiki")


def _input(service: WikiService, *, fact: str | None = "facts-1", index: str | None = None) -> WikiHealthInput:
    report = service.changes_since(None)
    observed = next((commit.commit_id for commit in reversed(report.commits) if commit.origin != "wiki.health"), None)
    return WikiHealthInput(
        fact_ledger_revision=fact,
        wiki=report,
        synthesis=WikiHealthWorker("Synthesis", datetime(2026, 7, 28, tzinfo=UTC), fact, fact),
        maintenance=WikiHealthWorker("Wiki Maintenance", None, observed, observed),
        index=WikiHealthIndex(index if index is not None else observed, "ready"),
    )


def test_health_projects_typed_evidence_and_mechanics(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    service = WikiService(repo)
    service.create_page(path="source.md", title="Source", page_id="source", body=b"[[Missing]]\n")
    report = service.changes_since(None)
    input_value = WikiHealthInput(
        fact_ledger_revision="facts-2",
        wiki=report,
        synthesis=WikiHealthWorker("Synthesis", datetime(2026, 7, 28, tzinfo=UTC), "facts-1", "facts-2"),
        maintenance=WikiHealthWorker("Wiki Maintenance", None, None, report.through_revision),
        index=WikiHealthIndex(None, "ready", "awaiting wiki_page sync"),
        issues=(WikiHealthIssue(WikiHealthIssueCode.FACT_REVIEW_DUE, "fact-9", "expired review date"),),
        pending_reviews=(WikiHealthPendingReview("review-1", "Decide whether to merge topic pages"),),
    )

    result = WikiHealthProjector(service).project(input_value)

    assert result.commit is not None
    assert result.commit.actor == "backend"
    assert result.commit.origin == "wiki.health"
    content = repo.read("health").decode()
    assert "Overall status: **attention needed**" in content
    assert f"Wiki: `{report.through_revision}`" in content
    assert "| Synthesis | behind | `facts-1` | 2026-07-28T00:00:00+00:00 |" in content
    assert "**unresolved_link** — `source.md`: Missing" in content
    assert "**index_behind** — `wiki_page`: awaiting wiki_page sync" in content
    assert "**fact_review_due** — `fact-9`: expired review date" in content
    assert "`review-1` — Decide whether to merge topic pages" in content
    assert {issue.code for issue in result.issues} == {
        WikiHealthIssueCode.UNRESOLVED_LINK,
        WikiHealthIssueCode.INDEX_BEHIND,
        WikiHealthIssueCode.FACT_REVIEW_DUE,
    }


def test_health_preserves_an_index_error_at_the_current_revision(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    service = WikiService(repo)
    service.create_page(path="one.md", title="One", page_id="one")
    value = _input(service)
    value = replace(value, index=WikiHealthIndex(value.wiki.through_revision, "error", "embedding failed"))

    WikiHealthProjector(service).project(value)

    content = repo.read("health").decode()
    assert "Overall status: **attention needed**" in content
    assert "## Search and link index\n\n- Status: error" in content
    assert "- Detail: embedding failed" in content


def test_health_maps_ambiguous_links_to_unresolved_link_issues(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    pages = (
        Create("first", "first.md", create_page(title="Shared", page_id="first").to_bytes()),
        Create("second", "second.md", create_page(title="Shared", page_id="second").to_bytes()),
        Create("source", "source.md", create_page(title="Source", page_id="source", body=b"[[Shared]]\n").to_bytes()),
    )
    repo.commit(
        ChangeSet(
            operations=pages,
            actor="user",
            origin="wiki.user",
            reason="ambiguous names",
            idempotency_key="ambiguous-names",
            expected_head=None,
            enforce_expected_head=True,
        )
    )
    service = WikiService(repo)

    result = WikiHealthProjector(service).project(_input(service))

    issue = next(issue for issue in result.issues if issue.code is WikiHealthIssueCode.UNRESOLVED_LINK)
    assert issue.target == "source.md"
    assert issue.evidence.startswith("ambiguous: Shared")


@pytest.mark.parametrize(
    ("warning_code", "total_bytes", "expected"),
    [
        ("storage_inspection", 50 * 1024 * 1024, "inspection recommended"),
        ("storage_needs_attention", 100 * 1024 * 1024, "attention needed"),
    ],
)
def test_health_surfaces_revision_history_storage_warnings(
    tmp_path: Path, warning_code: str, total_bytes: int, expected: str
) -> None:
    repo = _repo(tmp_path)
    service = WikiService(repo)
    service.create_page(path="one.md", title="One", page_id="one")
    value = _input(service)
    report = replace(
        value.wiki,
        warnings=(*value.wiki.warnings, WikiChangeWarning(warning_code, "history", str(total_bytes))),
        storage=replace(value.wiki.storage, total_bytes=total_bytes),
    )

    WikiHealthProjector(service).project(replace(value, wiki=report))

    content = repo.read("health").decode()
    assert "Overall status: **attention needed**" in content
    assert (
        f"## Revision history storage\n\n- Status: {expected}\n"
        "- Thresholds: 50 MiB inspection; 100 MiB attention"
    ) in content


def test_health_ignores_its_own_commit_and_is_a_noop_when_inputs_are_identical(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    service = WikiService(repo)
    projector = WikiHealthProjector(service)

    first = projector.project(_input(service))
    assert first.commit is not None
    second = projector.project(_input(service))

    assert second.commit is None
    assert second.observed_wiki_revision is None
    assert repo.head == first.commit.commit_id


def test_health_does_not_recreate_an_archived_resource(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    service = WikiService(repo)
    projector = WikiHealthProjector(service)
    created = projector.project(_input(service))
    assert created.commit is not None
    health = repo.get("health")
    repo.commit(
        ChangeSet(
            operations=(Archive("health", health.version_id),),
            actor="user",
            origin="wiki.user",
            reason="archive health",
            idempotency_key="archive-health",
            expected_head=repo.head,
        )
    )

    result = projector.project(_input(service))

    assert result.commit is None
    assert repo.get("health").state.value == "archived"


def test_archived_health_noop_still_checks_the_exact_head(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    service = WikiService(repo)
    projector = WikiHealthProjector(service)
    created = projector.project(_input(service))
    assert created.commit is not None
    health = repo.get("health")
    repo.commit(
        ChangeSet(
            operations=(Archive("health", health.version_id),),
            actor="user",
            origin="wiki.user",
            reason="archive health",
            idempotency_key="archive-health",
            expected_head=repo.head,
        )
    )
    stale = _input(service)
    service.create_page(path="later.md", title="Later", page_id="later")

    with pytest.raises(RevisionConflictError):
        projector.project(stale)


def test_health_uses_the_report_head_as_a_whole_tree_compare_and_swap(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    service = WikiService(repo)
    projector = WikiHealthProjector(service)
    stale = _input(service)
    page = create_page(title="Later", page_id="later")
    repo.commit(
        ChangeSet(
            operations=(Create("later", "later.md", page.to_bytes()),),
            actor="user",
            origin="wiki.user",
            reason="concurrent change",
            idempotency_key="later",
            expected_head=repo.head,
        )
    )

    with pytest.raises(RevisionConflictError):
        projector.project(stale)
    assert repo.find_by_path("health.md") is None


def test_health_rejects_a_partial_change_report(tmp_path: Path) -> None:
    service = WikiService(_repo(tmp_path))
    service.create_page(path="one.md", title="One", page_id="one")
    with pytest.raises(ValueError, match="whole-wiki"):
        WikiHealthInput(
            fact_ledger_revision=None,
            wiki=service.changes_since(service.repository.head),
            synthesis=WikiHealthWorker("Synthesis", None, None, None),
            maintenance=WikiHealthWorker("Wiki Maintenance", None, None, None),
            index=WikiHealthIndex(None, "ready"),
        )
