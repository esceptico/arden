from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from arden.revisions import Archive, ChangeSet, Create, ManagedFileRepository, RevisionConflictError, Update
from arden.wiki.models import GeneratedPageTarget
from arden.wiki.pages import extract_generated_region, parse_page, update_generated_region
from arden.wiki.service import GeneratedRegionConflictError, WikiAmbiguityError, WikiService, WikiValidationError

if TYPE_CHECKING:
    from pathlib import Path


def _repo(tmp_path: Path) -> ManagedFileRepository:
    return ManagedFileRepository(tmp_path / "wiki")


def _target(
    page_id: str,
    *,
    path: str | None = None,
    title: str | None = None,
    aliases: tuple[str, ...] = (),
    generated: bytes = b"Facts.\n",
    metadata: dict[str, object] | None = None,
) -> GeneratedPageTarget:
    return GeneratedPageTarget(
        page_id=page_id,
        path=path or f"topics/{page_id}.md",
        title=title or page_id.title(),
        aliases=aliases,
        generated=generated,
        metadata=metadata or {},
    )


def test_publish_generated_creates_multiple_pages_in_one_commit(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    service = WikiService(repo)

    commit = service.publish_generated(
        (_target("first"), _target("second", generated=b"Second facts.\n")),
        source_revision="facts-1",
        base_head=None,
    )

    assert commit is not None
    assert len(commit.changes) == 2
    assert repo.head == commit.commit_id
    assert extract_generated_region(repo.read("first"), expected_page_id="first") == b"Facts.\n"
    assert extract_generated_region(repo.read("second"), expected_page_id="second") == b"Second facts.\n"


def test_publish_generated_preserves_user_notes_after_synthesis(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    service = WikiService(repo)
    first = service.publish_generated((_target("topic"),), source_revision="facts-1", base_head=None)
    assert first is not None
    current = repo.get("topic")
    user_content = repo.read("topic") + b"\n## User notes\nKeep this.\n"
    repo.commit(
        ChangeSet(
            operations=(Update("topic", current.version_id, user_content),),
            actor="user",
            origin="wiki.user",
            reason="add notes",
            idempotency_key="user-notes",
            expected_head=repo.head,
        )
    )

    service.publish_generated(
        (_target("topic", generated=b"New facts.\n"),),
        source_revision="facts-2",
        base_head=repo.head,
    )

    content = repo.read("topic")
    assert extract_generated_region(content, expected_page_id="topic") == b"New facts.\n"
    assert content.endswith(b"\n## User notes\nKeep this.\n")


def test_generated_edit_conflicts_for_the_whole_batch(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    service = WikiService(repo)
    service.publish_generated((_target("existing"),), source_revision="facts-1", base_head=None)
    current = repo.get("existing")
    user_content = update_generated_region(
        repo.read("existing"), expected_page_id="existing", generated=b"User changed this.\n"
    )
    repo.commit(
        ChangeSet(
            operations=(Update("existing", current.version_id, user_content),),
            actor="user",
            origin="wiki.user",
            reason="edit generated region",
            idempotency_key="user-generated-edit",
            expected_head=repo.head,
        )
    )
    before = repo.head

    with pytest.raises(GeneratedRegionConflictError, match="generated region"):
        service.publish_generated(
            (_target("existing", generated=b"New facts.\n"), _target("other")),
            source_revision="facts-2",
            base_head=before,
        )

    assert repo.head == before
    assert repo.find_by_path("topics/other.md") is None


def test_publish_generated_compares_an_empty_head_exactly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo = _repo(tmp_path)
    service = WikiService(repo)
    publish = repo.commit

    def publish_after_a_concurrent_first_commit(change_set: ChangeSet):
        publish(
            ChangeSet(
                operations=(Create("other", "other.md", _page("other", "Other")),),
                actor="user",
                origin="wiki.user",
                reason="concurrent first page",
                idempotency_key="other",
            )
        )
        return publish(change_set)

    monkeypatch.setattr(repo, "commit", publish_after_a_concurrent_first_commit)

    with pytest.raises(RevisionConflictError, match="expected None"):
        service.publish_generated((_target("topic"),), source_revision="facts-1", base_head=None)

    assert repo.find_by_path("topics/topic.md") is None


def test_publish_generated_is_a_noop_when_final_content_is_unchanged(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    service = WikiService(repo)
    target = _target("topic", metadata={"fact_ids": ["fact-1"]})
    first = service.publish_generated((target,), source_revision="facts-1", base_head=None)
    assert first is not None

    assert service.publish_generated((target,), source_revision="facts-1", base_head=repo.head) is None
    assert repo.head == first.commit_id


def test_noop_publication_rejects_a_stale_pinned_head(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    service = WikiService(repo)
    target = _target("topic")
    first = service.publish_generated((target,), source_revision="facts-1", base_head=None)
    assert first is not None
    pinned_head = repo.head
    repo.commit(
        ChangeSet(
            operations=(Create("user", "user.md", _page("user", "User")),),
            actor="user",
            origin="wiki.user",
            reason="concurrent page",
            idempotency_key="concurrent-page",
            expected_head=pinned_head,
        )
    )

    with pytest.raises(RevisionConflictError, match="current head changed"):
        service.publish_generated((target,), source_revision="facts-1", base_head=pinned_head)


def test_publish_generated_retries_from_its_pinned_empty_head_after_later_commits(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    service = WikiService(repo)
    target = _target("topic")
    first = service.publish_generated((target,), source_revision="facts-1", base_head=None)
    assert first is not None
    repo.commit(
        ChangeSet(
            operations=(Create("user", "user.md", _page("user", "User")),),
            actor="user",
            origin="wiki.user",
            reason="later edit",
            idempotency_key="later-user-edit",
            expected_head=repo.head,
        )
    )

    assert service.publish_generated((target,), source_revision="facts-1", base_head=None) == first


@pytest.mark.parametrize("reuse", ["page_id", "path", "title"])
def test_publish_generated_does_not_recreate_archived_pages(tmp_path: Path, reuse: str) -> None:
    repo = _repo(tmp_path)
    service = WikiService(repo)
    created = service.publish_generated((_target("topic"),), source_revision="facts-1", base_head=None)
    assert created is not None
    service.archive_page("topic", base_head=repo.head)

    target = _target(
        "topic" if reuse == "page_id" else "other",
        path="topics/topic.md" if reuse != "title" else "topics/other.md",
        title="Topic" if reuse == "title" else None,
    )
    with pytest.raises(WikiValidationError, match=r"unavailable|archived"):
        service.publish_generated((target,), source_revision="facts-2", base_head=repo.head)


def test_archived_non_wiki_resources_do_not_block_generated_publication(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    repo.commit(
        ChangeSet(
            operations=(Create("attachment", "attachments/raw.txt", b"not a wiki page"),),
            actor="user",
            origin="attachment",
            reason="create attachment",
            idempotency_key="create-attachment",
        )
    )
    attachment = repo.get("attachment")
    repo.commit(
        ChangeSet(
            operations=(Archive("attachment", attachment.version_id),),
            actor="user",
            origin="attachment",
            reason="archive attachment",
            idempotency_key="archive-attachment",
            expected_head=repo.head,
        )
    )

    commit = WikiService(repo).publish_generated(
        (_target("topic"),),
        source_revision="facts-1",
        base_head=repo.head,
    )

    assert commit is not None


def test_publish_generated_records_fact_provenance_without_changing_page_identity(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    service = WikiService(repo)
    target = _target(
        "topic",
        metadata={"fact_ids": ["fact-1", "fact-2"], "fact_citations": {"fact-1": ["source-a"]}},
    )

    service.publish_generated((target,), source_revision="facts-9", base_head=None)
    page = parse_page(repo.read("topic"), expected_page_id="topic")

    assert page.title == "Topic"
    assert page.aliases == ()
    assert page.lifecycle == "active"
    assert page.metadata["generated_from_revision"] == "facts-9"
    assert page.metadata["fact_ids"] == ["fact-1", "fact-2"]
    assert page.metadata["fact_citations"] == {"fact-1": ["source-a"]}


@pytest.mark.parametrize(
    "targets",
    [
        (_target("first", title="Same"), _target("second", title="Same")),
        (_target("first", path="topics/same.md"), _target("second", path="topics/same.md")),
    ],
)
def test_publish_generated_rejects_target_collisions_without_a_partial_commit(
    tmp_path: Path, targets: tuple[GeneratedPageTarget, ...]
) -> None:
    repo = _repo(tmp_path)

    with pytest.raises(WikiAmbiguityError):
        WikiService(repo).publish_generated(targets, source_revision="facts-1", base_head=None)

    assert repo.head is None


def _page(page_id: str, title: str) -> bytes:
    return b"---\n" + f"page_id: {page_id}\ntitle: {title}\naliases: []\nlifecycle: active\n".encode() + b"---\n"
