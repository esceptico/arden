from dataclasses import replace
from pathlib import Path

import pytest

from arden.revisions import Archive, ChangeSet, Create, ManagedFileRepository, RevisionConflictError, Update
from arden.wiki import LinkStatus, WikiAmbiguityError, WikiService, WikiValidationError, create_page


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


def test_backlinks_resolve_title_alias_and_path_and_exclude_hidden_contexts(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    target = create_page(title="Target", page_id="target", aliases=("Alias",))
    source = create_page(
        title="Source",
        page_id="source",
        body=(
            b"[[Target]] [[Alias]] [[notes/target]]\n"
            b"`[[Target]]`\n<!-- [[Target]] -->\n[off](https://x.test/[[Target]])\n"
        ),
    )
    _seed(repo, ("target", "notes/target.md", target), ("source", "source.md", source))

    links = WikiService(repo).backlinks("target")
    assert [link.status for link in links] == [LinkStatus.RESOLVED] * 3
    assert [link.node.page for link in links] == ["Target", "Alias", "notes/target"]


def test_backlinks_reports_ambiguous_and_unresolved_links_without_guessing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    first = create_page(title="Same", page_id="first")
    second = create_page(title="same", page_id="second")
    source = create_page(title="Source", page_id="source", body=b"[[Same]] [[Missing]]")
    _seed(repo, ("first", "one.md", first), ("second", "two.md", second), ("source", "source.md", source))
    service = WikiService(repo)

    with pytest.raises(WikiAmbiguityError):
        service.list_pages()
    references = service.links("source")
    assert [reference.status for reference in references] == [LinkStatus.AMBIGUOUS, LinkStatus.UNRESOLVED]


def test_rename_is_atomic_and_preserves_link_bytes(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    target = create_page(title="Old", page_id="target", body=b"Self [[Old]]")
    source = create_page(
        title="Source",
        page_id="source",
        body=b"before ![[ Old #part|shown ]] and [[notes/old.md#frag|alias]]\r\n",
    )
    _seed(repo, ("target", "notes/old.md", target), ("source", "source.md", source))
    service = WikiService(repo)
    target_record = service.read_page("target")
    plan = service.prepare_rename(
        "target",
        new_path="notes/new.md",
        new_title="New",
        expected_version=target_record.resource.version_id,
        base_head=repo.head,
    )
    assert (plan.link_count, plan.page_count) == (3, 2)
    commit = service.apply_rename(plan)
    assert len(commit.changes) == 3

    assert repo.get("target").path == "notes/new.md"
    redirect = service.read_page(plan.redirect_page_id)
    assert redirect.page.lifecycle == "redirect"
    assert redirect.page.redirect_to == "target"
    rewritten = repo.read("source")
    assert b"![[ New #part|shown ]]" in rewritten
    assert b"[[notes/new.md#frag|alias]]\r\n" in rewritten
    assert b"[[New]]" in repo.read("target")
    assert len(repo.history(resource_id="target")) == 2
    assert service.apply_rename(plan) == commit
    report = service.link_report_for_path("notes/old.md")
    assert report.head == commit.commit_id
    assert report.page.resource.path == "notes/new.md"
    assert plan.redirect_page_id in {page.page.page_id for page in report.pages}


def test_root_title_link_uses_new_title_not_lowercase_path(tmp_path: Path) -> None:
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
    service.apply_rename(plan)

    assert repo.read("source").endswith(b"[[New]]")


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
    _seed(repo, ("one", "one.md", page))
    service = WikiService(repo)
    initial_version = service.read_page("one").resource.version_id
    service.archive_page("one", expected_version=initial_version, base_head=repo.head)
    archived = repo.get("one")
    assert archived.state.value == "archived"
    restored = service.restore_page("one", expected_version=archived.version_id, base_head=repo.head)
    assert restored.page.page_id == "one"

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
