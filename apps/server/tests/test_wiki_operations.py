from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from arden.revisions import Archive, ChangeSet, Create, ManagedFileRepository, RevisionConflictError, Update
from arden.wiki.models import LinkStatus
from arden.wiki.pages import create_page
from arden.wiki.service import WikiAmbiguityError, WikiService, WikiValidationError


def _repo(tmp_path: Path) -> ManagedFileRepository:
    return ManagedFileRepository(tmp_path / "wiki")


def _seed(repo: ManagedFileRepository, *pages: tuple[str, str, object]) -> None:
    operations = []
    for page_id, path, page in pages:
        operations.append(Create(page_id, path, page.to_bytes()))
    repo.commit(
        ChangeSet(
            operations=tuple(operations),
            actor="test",
            origin="test",
            reason="seed wiki",
            idempotency_key="seed-" + "-".join(page_id for page_id, _path, _page in pages),
        )
    )


def test_create_read_and_reject_malformed_resource_identity(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    service = WikiService(repo)
    created = service.create_page(path="notes/one.md", title="One", page_id="one", aliases=("First",))

    assert created.page.page_id == "one"
    assert service.read_page("one").resource.path == "notes/one.md"

    repo.commit(
        ChangeSet(
            operations=(Create("wrong-resource", "bad.md", create_page(title="Bad", page_id="other").to_bytes()),),
            actor="test",
            origin="test",
            reason="bad fixture",
            idempotency_key="bad-resource",
        )
    )
    with pytest.raises(WikiValidationError, match="expected resource identity"):
        service.list_pages()


def test_update_page_preserves_exact_bytes_and_requires_page_and_tree_versions(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    service = WikiService(repo)
    original = service.create_page(path="notes/one.md", title="One", page_id="one")
    candidate = (
        b"---\n"
        b"page_id: one\n"
        b"title: Updated\n"
        b"aliases:\n"
        b"- First\n"
        b"lifecycle: active\n"
        b"nested:\n"
        b"  values: [one, two]\n"
        b"---\n"
        b"Exact body bytes.\n"
    )

    updated = service.update_page(
        "one",
        content=candidate,
        expected_version=original.resource.version_id,
        expected_head=repo.head,
    )

    assert updated.content == candidate
    assert repo.read("one") == candidate
    assert updated.page.page_id == "one"
    assert updated.page.title == "Updated"
    assert updated.page.metadata["nested"]["values"] == ("one", "two")
    commit = repo.history(resource_id="one", limit=1)[0]
    assert (commit.actor, commit.origin, commit.reason) == ("user:desktop", "desktop", "edit wiki page")

    head = repo.head
    with pytest.raises(RevisionConflictError, match="resource one changed"):
        service.update_page(
            "one",
            content=candidate + b"stale",
            expected_version=original.resource.version_id,
            expected_head=head,
        )
    with pytest.raises(WikiValidationError, match="expected resource identity"):
        service.update_page(
            "one",
            content=candidate.replace(b"page_id: one", b"page_id: other"),
            expected_version=updated.resource.version_id,
            expected_head=head,
        )
    with pytest.raises(WikiValidationError, match="preserve active lifecycle"):
        service.update_page(
            "one",
            content=candidate.replace(b"lifecycle: active", b"lifecycle: redirect\nredirect_to: one"),
            expected_version=updated.resource.version_id,
            expected_head=head,
        )

    repo.commit(
        ChangeSet(
            operations=(Update("one", updated.resource.version_id, candidate + b"concurrent"),),
            actor="test",
            origin="test",
            reason="race",
            idempotency_key="race",
            expected_head=head,
        )
    )
    with pytest.raises(RevisionConflictError, match="current head changed"):
        service.update_page(
            "one",
            content=candidate + b"stale tree",
            expected_version=updated.resource.version_id,
            expected_head=head,
        )


def test_update_page_can_guard_only_the_observed_page_version(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    service = WikiService(repo)
    observed = service.create_page(path="notes/one.md", title="One", page_id="one")
    service.create_page(path="notes/two.md", title="Two", page_id="two", expected_head=repo.head)

    updated = service.update_page(
        "one",
        content=observed.page.with_body(b"Updated after an unrelated commit.\n").to_bytes(),
        expected_version=observed.resource.version_id,
    )

    assert updated.page.body == b"Updated after an unrelated commit.\n"
    current_head = repo.head
    repo.commit(
        ChangeSet(
            operations=(
                Update("one", updated.resource.version_id, updated.page.with_body(b"Concurrent.\n").to_bytes()),
            ),
            actor="test",
            origin="test",
            reason="same-page race",
            idempotency_key="same-page-race",
            expected_head=current_head,
        )
    )
    with pytest.raises(RevisionConflictError, match="resource one changed"):
        service.update_page(
            "one",
            content=updated.page.with_body(b"Stale overwrite.\n").to_bytes(),
            expected_version=updated.resource.version_id,
        )


def test_ordinary_page_operations_cannot_take_over_backend_health(tmp_path: Path) -> None:
    fresh = WikiService(_repo(tmp_path / "fresh"))
    with pytest.raises(WikiValidationError, match="backend-managed"):
        fresh.create_page(path="Health.md", title="Reserved")

    service = WikiService(_repo(tmp_path))
    service.publish_health(body=b"Healthy.\n", base_head=None)
    health = service.read_page("health")

    with pytest.raises(WikiValidationError, match="backend-managed"):
        service.update_page(
            "health",
            content=health.content,
            expected_version=health.resource.version_id,
            expected_head=service.repository.head,
        )
    with pytest.raises(WikiValidationError, match="backend-managed"):
        service.archive_page(
            "health",
            expected_version=health.resource.version_id,
            base_head=service.repository.head,
        )
    with pytest.raises(WikiValidationError, match="backend-managed"):
        service.prepare_rename(
            "health",
            new_path="other.md",
            new_title="Other",
            expected_version=health.resource.version_id,
            base_head=service.repository.head,
        )
    with pytest.raises(WikiValidationError, match="backend-managed"):
        service.move_page(
            "health",
            new_path="other.md",
            expected_version=health.resource.version_id,
            expected_head=service.repository.head,
        )


def test_backlinks_resolve_only_path_links_and_exclude_hidden_contexts(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    target = create_page(title="Target", page_id="target", aliases=("Alias",))
    source = create_page(
        title="Source",
        page_id="source",
        body=(
            b"[[Target]] [[Alias]] [[notes/target]] [[notes/target.md]]\n"
            b"`[[notes/target]]`\n<!-- [[notes/target]] -->\n[off](https://x.test/[[notes/target]])\n"
        ),
    )
    _seed(repo, ("target", "notes/target.md", target), ("source", "source.md", source))

    links = WikiService(repo).backlinks("target")
    assert [link.status for link in links] == [LinkStatus.RESOLVED] * 2
    assert [link.node.page for link in links] == ["notes/target", "notes/target.md"]


def test_pages_sharing_a_title_coexist_and_title_links_stay_unresolved(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = create_page(title="Same", page_id="first")
    second = create_page(title="same", page_id="second")
    source = create_page(title="Source", page_id="source", body=b"[[Same]] [[Missing]] [[one]]")
    _seed(repo, ("first", "one.md", first), ("second", "two.md", second), ("source", "source.md", source))
    service = WikiService(repo)

    assert len(service.list_pages()) == 3
    references = service.links("source")
    assert [reference.status for reference in references] == [
        LinkStatus.UNRESOLVED,
        LinkStatus.UNRESOLVED,
        LinkStatus.RESOLVED,
    ]


def test_rename_is_atomic_and_preserves_link_bytes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    target = create_page(title="Old", page_id="target", body=b"Self [[Old]]")
    source = create_page(
        title="Source",
        page_id="source",
        body=b"before ![[ Old #part|shown ]] and [[notes/old.md#frag|alias]]\r\n",
    )
    readme = create_page(title="Notes", page_id="notes-readme", body=b"Purpose: test notes.\n")
    _seed(
        repo,
        ("notes-readme", "notes/README.md", readme),
        ("target", "notes/old.md", target),
        ("source", "source.md", source),
    )
    service = WikiService(repo)
    target_record = service.read_page("target")
    plan = service.prepare_rename(
        "target",
        new_path="archive/new.md",
        new_title="New",
        expected_version=target_record.resource.version_id,
        base_head=repo.head,
    )
    assert (plan.link_count, plan.page_count) == (1, 1)
    commit = service.apply_rename(plan)
    assert len(commit.changes) == 4

    assert repo.get("target").path == "archive/new.md"
    assert any(record.resource.path == "archive/README.md" for record in service.snapshot().pages)
    redirect = service.read_page(plan.redirect_page_id)
    assert redirect.page.lifecycle == "redirect"
    assert redirect.page.redirect_to == "target"
    rewritten = repo.read("source")
    assert b"![[ Old #part|shown ]]" in rewritten
    assert b"[[archive/new.md#frag|alias]]\r\n" in rewritten
    assert b"[[Old]]" in repo.read("target")
    assert len(repo.history(resource_id="target")) == 2
    assert service.apply_rename(plan) == commit
    report = service.link_report_for_path("notes/old.md")
    assert report.head == commit.commit_id
    assert report.page.resource.path == "archive/new.md"
    assert plan.redirect_page_id in {page.page.page_id for page in report.pages}


def test_move_preserves_identity_and_rewrites_only_resolved_path_links(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    target = create_page(
        title="Old",
        page_id="target",
        aliases=("Alias",),
        metadata={"source": "test"},
        body=b"Self [[notes/old]] [[Old]] [[notes/old.md]]\n",
    )
    source = create_page(
        title="Source",
        page_id="source",
        body=b"[[notes/old]] [[notes/old.md#part|shown]] [[Old]] [[Alias]]\n",
    )
    readme = create_page(title="Notes", page_id="notes-readme", body=b"Purpose: test notes.\n")
    _seed(
        repo,
        ("notes-readme", "notes/README.md", readme),
        ("target", "notes/old.md", target),
        ("source", "source.md", source),
    )
    service = WikiService(repo)
    before = service.read_page("target")

    moved, commit_id = service.move_page(
        "target",
        new_path="archive/new.md",
        expected_version=before.resource.version_id,
        expected_head=repo.head,
    )

    assert commit_id == repo.head
    assert moved.page.page_id == "target"
    assert moved.page.title == "Old"
    assert moved.page.aliases == ("Alias",)
    assert moved.page.metadata["source"] == "test"
    assert moved.resource.path == "archive/new.md"
    assert b"[[archive/new]] [[Old]] [[archive/new.md]]" in moved.content
    assert service.read_page("source").page.body == b"[[archive/new]] [[archive/new.md#part|shown]] [[Old]] [[Alias]]\n"
    assert service.read_page("directory-readme-" + sha256(b"archive/README.md").hexdigest()[:16])
    assert all(page.page.lifecycle != "redirect" for page in service.list_pages(include_redirects=True))

    replayed, replay_commit = service.move_page(
        "target",
        new_path="archive/new.md",
        expected_version=before.resource.version_id,
        expected_head=repo.head,
    )
    assert replayed == moved
    assert replay_commit is None


def test_move_rejects_stale_or_colliding_destination_without_changes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    target = create_page(title="Target", page_id="target")
    collision = create_page(title="Collision", page_id="collision")
    _seed(repo, ("target", "target.md", target), ("collision", "collision.md", collision))
    service = WikiService(repo)
    target_record = service.read_page("target")
    head = repo.head

    with pytest.raises(WikiAmbiguityError, match="path already exists"):
        service.move_page(
            "target",
            new_path="collision.md",
            expected_version=target_record.resource.version_id,
            expected_head=head,
        )
    assert repo.head == head

    repo.commit(
        ChangeSet(
            operations=(
                Update(
                    "collision", service.read_page("collision").resource.version_id, collision.to_bytes() + b"later\n"
                ),
            ),
            actor="test",
            origin="test",
            reason="concurrent edit",
            idempotency_key="move-concurrent-edit",
            expected_head=head,
        )
    )
    with pytest.raises(RevisionConflictError, match="current head changed"):
        service.move_page(
            "target",
            new_path="moved.md",
            expected_version=target_record.resource.version_id,
            expected_head=head,
        )


def test_root_stem_link_rewrites_to_the_new_stem(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    target = create_page(title="Old", page_id="target")
    source = create_page(title="Source", page_id="source", body=b"[[old]]")
    _seed(repo, ("target", "old.md", target), ("source", "source.md", source))
    service = WikiService(repo)

    plan = service.prepare_rename(
        "target",
        new_path="new.md",
        new_title="New",
        expected_version=repo.get("target").version_id,
        base_head=repo.head,
    )
    service.apply_rename(plan)

    assert repo.read("source").endswith(b"[[new]]")


def test_rename_rejects_forged_plan_without_changes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    target = create_page(title="Old", page_id="target")
    source = create_page(title="Source", page_id="source", body=b"[[Old]]")
    _seed(repo, ("target", "old.md", target), ("source", "source.md", source))
    service = WikiService(repo)
    plan = service.prepare_rename(
        "target",
        new_path="new.md",
        new_title="New",
        expected_version=repo.get("target").version_id,
        base_head=repo.head,
    )
    forged = (
        replace(plan, moved_content=b"forged"),
        replace(plan, redirect_page_id="forged"),
        replace(plan, old_path="forged.md"),
        replace(plan, idempotency_key="forged"),
        replace(plan, rewrites=(replace(plan.rewrites[0], content=b"forged"),)),
        replace(plan, rewrites=()),
    )
    before = repo.head
    for candidate in forged:
        with pytest.raises(WikiValidationError, match="rename plan"):
            service.apply_rename(candidate)
        assert repo.head == before
        assert repo.get("target").path == "old.md"


def test_rename_collision_stale_head_and_opt_out_are_all_or_nothing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    target = create_page(title="Old", page_id="target")
    other = create_page(title="Taken", page_id="other")
    source = create_page(title="Source", page_id="source", body=b"[[Old]]")
    _seed(repo, ("target", "old.md", target), ("other", "taken.md", other), ("source", "source.md", source))
    service = WikiService(repo)
    target_record = service.read_page("target")
    with pytest.raises(WikiAmbiguityError):
        service.prepare_rename(
            "target",
            new_path="taken.md",
            new_title="New",
            expected_version=target_record.resource.version_id,
            base_head=repo.head,
        )

    plan = service.prepare_rename(
        "target",
        new_path="new.md",
        new_title="New",
        expected_version=target_record.resource.version_id,
        base_head=repo.head,
        rewrite_links=False,
    )
    repo.commit(
        ChangeSet(
            operations=(Create("unrelated", "u.md", create_page(title="U", page_id="unrelated").to_bytes()),),
            actor="test",
            origin="test",
            reason="move head",
            idempotency_key="move-head",
        )
    )
    with pytest.raises(RevisionConflictError, match="current head changed"):
        service.apply_rename(plan)
    assert repo.get("target").path == "old.md"
    assert repo.read("source") == source.to_bytes()

    fresh = service.prepare_rename(
        "target",
        new_path="new.md",
        new_title="New",
        expected_version=repo.get("target").version_id,
        base_head=repo.head,
        rewrite_links=False,
    )
    service.apply_rename(fresh)
    assert repo.read("source") == source.to_bytes()


def test_archive_restore_and_reject_dangling_or_cyclic_redirects(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    page = create_page(title="One", page_id="one")
    _seed(repo, ("one", "notes/one.md", page))
    service = WikiService(repo)
    initial_version = service.read_page("one").resource.version_id
    service.archive_page("one", expected_version=initial_version, base_head=repo.head)
    archived = repo.get("one")
    assert archived.state.value == "archived"
    restored = service.restore_page("one", expected_version=archived.version_id, base_head=repo.head)
    assert restored.page.page_id == "one"
    assert {change.action for change in repo.history(limit=1)[0].changes} == {"create", "restore"}
    assert any(record.resource.path == "notes/README.md" for record in service.snapshot().pages)

    repo = _repo(tmp_path / "redirects")
    broken = create_page(title="Broken", page_id="broken", lifecycle="redirect", redirect_to="missing")
    _seed(repo, ("broken", "broken.md", broken))
    with pytest.raises(WikiValidationError, match="missing target"):
        WikiService(repo).list_pages(include_redirects=True)

    repo = _repo(tmp_path / "cycle")
    first = create_page(title="First", page_id="first", lifecycle="redirect", redirect_to="second")
    second = create_page(title="Second", page_id="second", lifecycle="redirect", redirect_to="first")
    _seed(repo, ("first", "first.md", first), ("second", "second.md", second))
    with pytest.raises(WikiValidationError, match="cycle"):
        WikiService(repo).list_pages(include_redirects=True)


def test_archive_and_restore_reject_prospective_dangling_redirects(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    page = create_page(title="Old", page_id="target")
    _seed(repo, ("target", "old.md", page))
    service = WikiService(repo)
    plan = service.prepare_rename(
        "target",
        new_path="new.md",
        new_title="New",
        expected_version=repo.get("target").version_id,
        base_head=repo.head,
    )
    service.apply_rename(plan)
    before = repo.head
    with pytest.raises(WikiValidationError, match="missing target"):
        service.archive_page("target", expected_version=repo.get("target").version_id, base_head=repo.head)
    assert repo.head == before

    repo = _repo(tmp_path / "restore")
    redirect = create_page(title="Redirect", page_id="redirect", lifecycle="redirect", redirect_to="missing")
    _seed(repo, ("redirect", "redirect.md", redirect))
    current = repo.get("redirect")
    repo.commit(
        ChangeSet(
            operations=(Archive("redirect", current.version_id),),
            actor="test",
            origin="test",
            reason="archive invalid redirect",
            idempotency_key="archive-invalid-redirect",
        )
    )
    before = repo.head
    with pytest.raises(WikiValidationError, match="missing target"):
        WikiService(repo).restore_page(
            "redirect", expected_version=repo.get("redirect").version_id, base_head=repo.head
        )
    assert repo.head == before


def test_rename_requires_current_version_and_safe_path(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    page = create_page(title="One", page_id="one")
    _seed(repo, ("one", "one.md", page))
    service = WikiService(repo)
    with pytest.raises(WikiValidationError, match=r"\.md path"):
        service.prepare_rename(
            "one",
            new_path="../escape.md",
            new_title="Two",
            expected_version=repo.get("one").version_id,
            base_head=repo.head,
        )
    with pytest.raises(WikiValidationError, match=r"\.md path"):
        service.prepare_rename(
            "one",
            new_path="/escape.md",
            new_title="Two",
            expected_version="stale",
            base_head=repo.head,
        )
    with pytest.raises(RevisionConflictError, match="expected stale"):
        service.prepare_rename(
            "one",
            new_path="two.md",
            new_title="Two",
            expected_version="stale",
            base_head=repo.head,
        )


def test_directory_readmes_are_created_atomically_and_protected_while_children_are_active(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    service = WikiService(repo)
    created = service.create_page(
        path="automations/digest/today.md",
        title="Today",
        page_id="today",
        expected_head=None,
    )
    root_readme_id = "directory-readme-" + sha256(b"automations/README.md").hexdigest()[:16]
    digest_readme_id = "directory-readme-" + sha256(b"automations/digest/README.md").hexdigest()[:16]
    root_readme = service.read_page(root_readme_id)
    digest_readme = service.read_page(digest_readme_id)

    assert {change.after.path for change in repo.history(limit=1)[0].changes if change.after is not None} == {
        "automations/README.md",
        "automations/digest/README.md",
        "automations/digest/today.md",
    }
    assert b"## Purpose" in root_readme.content
    assert b"## Producers" in digest_readme.content
    assert b"## Consumers" in digest_readme.content
    assert b"## Boundaries" in digest_readme.content
    assert b"## Retention" in digest_readme.content
    assert root_readme.page.title == "Automations README"
    assert digest_readme.page.title == "Digest README"
    assert b"before the creating task finishes" in digest_readme.content
    assert b"today.md" not in digest_readme.content
    assert not digest_readme.page.title.startswith("Directory:")

    with pytest.raises(WikiValidationError, match="README paths are fixed"):
        service.prepare_rename(
            digest_readme_id,
            new_path="automations/digest/guide.md",
            new_title="Digest contract",
            expected_version=digest_readme.resource.version_id,
            base_head=repo.head,
        )
    assert service.read_page(digest_readme_id).resource.path == "automations/digest/README.md"

    with pytest.raises(WikiValidationError, match="active pages remain"):
        service.archive_page(
            digest_readme_id,
            expected_version=service.read_page(digest_readme_id).resource.version_id,
            base_head=repo.head,
        )
    with pytest.raises(WikiValidationError, match="README paths are fixed"):
        service.move_page(
            digest_readme_id,
            new_path="automations/digest/guide.md",
            expected_version=service.read_page(digest_readme_id).resource.version_id,
            expected_head=repo.head,
        )
    service.archive_page("today", expected_version=created.resource.version_id, base_head=repo.head)
    service.archive_page(
        digest_readme_id,
        expected_version=service.read_page(digest_readme_id).resource.version_id,
        base_head=repo.head,
    )
    restored_content = digest_readme.content
    service.create_page(
        path="automations/digest/tomorrow.md",
        title="Tomorrow",
        page_id="tomorrow",
        expected_head=repo.head,
    )
    assert service.read_page(digest_readme_id).content == restored_content
    assert {change.action for change in repo.history(limit=1)[0].changes} == {"create", "restore"}


@pytest.mark.parametrize(
    ("directory", "title", "marker"),
    (
        ("topics", "Topics README", b"verify important claims"),
        ("daily", "Daily README", b"timeless truth"),
        ("automations", "Automations README", b"Child READMEs"),
        ("insights", "Insights README", b"supporting evidence, inference, and open questions"),
        ("projects", "Projects README", b"cross-project knowledge"),
    ),
)
def test_builtin_directory_readmes_have_specific_agent_contracts(
    tmp_path: Path,
    directory: str,
    title: str,
    marker: bytes,
) -> None:
    service = WikiService(_repo(tmp_path))
    service.create_page(path=f"{directory}/example.md", title="Example")
    readme = next(record for record in service.snapshot().pages if record.resource.path == f"{directory}/README.md")

    assert readme.page.title == title
    assert b"guide" not in readme.content.lower()
    assert marker in readme.content
    assert b"## Boundaries" in readme.content


def test_directory_readme_title_does_not_claim_the_directory_topic_name(tmp_path: Path) -> None:
    service = WikiService(_repo(tmp_path))
    service.create_page(path="catalog.md", title="Topics")

    service.create_page(path="topics/example.md", title="Example")

    readme = next(record for record in service.snapshot().pages if record.resource.path == "topics/README.md")
    assert readme.page.title == "Topics README"


def test_root_readme_remains_an_ordinary_home_page(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    service = WikiService(repo)
    home = service.create_page(
        path="README.md",
        title="Home",
        page_id="home",
        expected_head=None,
    )
    service.create_page(
        path="topics/one.md",
        title="One",
        page_id="one",
        expected_head=repo.head,
    )

    moved, commit_id = service.move_page(
        "home",
        new_path="home.md",
        expected_version=home.resource.version_id,
        expected_head=repo.head,
    )

    assert commit_id is not None
    assert moved.resource.path == "home.md"


def test_ordinary_operations_cannot_claim_nested_directory_readme_paths(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    service = WikiService(repo)
    page = service.create_page(path="one.md", title="One", page_id="one", expected_head=None)

    with pytest.raises(WikiValidationError, match="reserved for automatic directory contracts"):
        service.create_page(path="notes/README.md", title="Notes", page_id="notes-readme", expected_head=repo.head)
    with pytest.raises(WikiValidationError, match="reserved for automatic directory contracts"):
        service.move_page(
            "one",
            new_path="notes/README.md",
            expected_version=page.resource.version_id,
            expected_head=repo.head,
        )
    with pytest.raises(WikiValidationError, match="reserved for automatic directory contracts"):
        service.prepare_rename(
            "one",
            new_path="notes/Readme.md",
            new_title="Notes",
            expected_version=page.resource.version_id,
            base_head=repo.head,
        )

    assert service.read_page("one").resource.path == "one.md"
