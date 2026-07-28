"""Contract tests for the domain-neutral managed-file revision repository."""

import ast
import json
import os
import shutil
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest

import arden.revisions._anchored as anchored
from arden.revisions import (
    Archive,
    ChangeSet,
    CorruptRepositoryError,
    Create,
    IdempotencyConflictError,
    ManagedFileRepository,
    Move,
    ResourceState,
    Restore,
    RevisionConflictError,
    RevisionContentLimitError,
    UnsafePathError,
    Update,
)
from arden.revisions._codec import make_version, tree_record
from arden.revisions._storage import canonical_jsonl, sha256


def _changes(
    *operations,
    key: str = "key",
    expected_head: str | None = None,
    enforce_expected_head: bool = False,
) -> ChangeSet:
    return ChangeSet(
        operations=operations,
        actor="tester",
        origin="test",
        reason="exercise revision contract",
        idempotency_key=key,
        expected_head=expected_head,
        enforce_expected_head=enforce_expected_head,
    )


def _repo(tmp_path: Path) -> ManagedFileRepository:
    return ManagedFileRepository(tmp_path / "work")


def test_create_update_move_archive_restore_and_restore_from_commit(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    created = repo.commit(_changes(Create("note", "notes/one.md", b"one\n"), key="create"))
    first = repo.get("note")

    moved = repo.commit(_changes(Move("note", first.version_id, "notes/two.md"), key="move"))
    second = repo.get("note")
    assert moved.changes[0].action == "move"
    assert not (repo.root / "notes/one.md").exists()
    assert (repo.root / "notes/two.md").read_bytes() == b"one\n"

    repo.commit(_changes(Move("note", second.version_id, "notes/three.md", b"three\n"), key="move-content"))
    third = repo.get("note")
    assert (repo.root / "notes/three.md").read_bytes() == b"three\n"

    repo.commit(_changes(Archive("note", third.version_id), key="archive"))
    archived = repo.get("note")
    assert archived.state is ResourceState.ARCHIVED
    assert repo.list_resources() == ()
    assert not (repo.root / "notes/three.md").exists()

    restored = repo.restore_from_commit(
        "note",
        created.commit_id,
        expected_version=archived.version_id,
        actor="tester",
        origin="test",
        reason="restore exact original",
        idempotency_key="restore-original",
    )
    assert restored.changes[0].action == "restore"
    assert repo.read("note") == b"one\n"
    assert repo.get("note").path == "notes/one.md"

    repo.commit(_changes(Archive("note", repo.get("note").version_id), key="archive-again"))
    archived_again = repo.get("note")
    repo.commit(
        _changes(
            Restore("note", archived_again.version_id, path="notes/four.md", content=b"four\n"),
            key="restore-custom",
        )
    )
    assert repo.read("note") == b"four\n"
    assert repo.get("note").path == "notes/four.md"


def test_per_resource_cas_allows_unrelated_changes_but_rejects_stale_version(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    repo.commit(
        _changes(
            Create("a", "a.md", b"a0"),
            Create("b", "b.md", b"b0"),
            key="create",
        )
    )
    a0 = repo.get("a")
    b0 = repo.get("b")
    repo.commit(_changes(Update("b", b0.version_id, b"b1"), key="update-b"))
    repo.commit(_changes(Update("a", a0.version_id, b"a1"), key="update-a"))

    with pytest.raises(RevisionConflictError, match="resource a changed"):
        repo.commit(_changes(Update("a", a0.version_id, b"a2"), key="stale-a"))

    assert repo.read("a") == b"a1"
    assert repo.read("b") == b"b1"


def test_expected_head_rejects_unrelated_changes_but_idempotent_retry_wins_first(
    tmp_path: Path,
) -> None:
    repo = _repo(tmp_path)
    head = repo.commit(_changes(Create("a", "a.md", b"a0"), key="create"))
    a0 = repo.get("a")
    planned = _changes(
        Update("a", a0.version_id, b"a1"),
        key="planned",
        expected_head=head.commit_id,
    )
    repo.commit(_changes(Create("b", "b.md", b"b0"), key="unrelated"))

    with pytest.raises(RevisionConflictError, match="current head changed"):
        repo.commit(planned)
    assert repo.read("a") == b"a0"

    fresh_head = repo.head
    assert fresh_head is not None
    fresh = _changes(
        Update("a", a0.version_id, b"a1"),
        key="fresh",
        expected_head=fresh_head,
    )
    committed = repo.commit(fresh)
    assert repo.commit(fresh) == committed
    assert repo.read("a") == b"a1"


def test_expected_empty_head_rejects_the_first_concurrent_commit(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    with pytest.raises(ValueError, match="empty expected head"):
        repo.commit(
            _changes(
                Create("invalid", "invalid.md", b"invalid"),
                expected_head="a" * 64,
                enforce_expected_head=True,
            )
        )
    planned = _changes(
        Create("a", "a.md", b"a0"),
        key="planned-empty",
        expected_head=None,
        enforce_expected_head=True,
    )
    repo.commit(_changes(Create("b", "b.md", b"b0"), key="concurrent-first"))

    with pytest.raises(RevisionConflictError, match=r"expected None"):
        repo.commit(planned)

    assert repo.find_by_path("a.md") is None
    assert repo.read("b") == b"b0"


def test_resource_version_token_does_not_repeat_after_value_reverts(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    repo.commit(_changes(Create("note", "note.md", b"zero"), key="create"))
    zero = repo.get("note")
    repo.commit(_changes(Update("note", zero.version_id, b"one"), key="one"))
    one = repo.get("note")
    repo.commit(_changes(Update("note", one.version_id, b"zero"), key="back-to-zero"))
    reverted = repo.get("note")

    assert reverted.blob_id == zero.blob_id
    assert reverted.path == zero.path
    assert reverted.version_id != zero.version_id
    with pytest.raises(RevisionConflictError, match="resource note changed"):
        repo.commit(_changes(Update("note", zero.version_id, b"stale"), key="stale-after-aba"))


def test_publish_rejects_external_drift_in_an_untouched_managed_resource(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    repo.commit(
        _changes(
            Create("a", "a.md", b"a0"),
            Create("b", "b.md", b"b0"),
            key="seed",
        )
    )
    b = repo.get("b")
    (repo.root / "a.md").write_bytes(b"external")

    with pytest.raises(RevisionConflictError, match=r"working file changed: a\.md"):
        repo.commit(_changes(Update("b", b.version_id, b"b1"), key="update-b"))

    assert repo.read("a") == b"a0"
    assert repo.read("b") == b"b0"
    assert (repo.root / "a.md").read_bytes() == b"external"


def test_multi_resource_change_is_one_sorted_commit_and_history_diff_is_exact(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    initial = repo.commit(
        _changes(
            Create("z", "z.md", b"z0\n"),
            Create("a", "a.md", b"one\n"),
            key="create",
        )
    )
    a0 = repo.get("a")
    z0 = repo.get("z")
    updated = repo.commit(
        _changes(
            Update("z", z0.version_id, b"z1\n"),
            Update("a", a0.version_id, b"two\n"),
            key="update",
        )
    )

    assert updated.parent_id == initial.commit_id
    assert [change.after.resource_id for change in updated.changes if change.after] == ["a", "z"]
    assert [commit.commit_id for commit in repo.history(resource_id="a")] == [
        updated.commit_id,
        initial.commit_id,
    ]
    assert repo.history(limit=1) == (updated,)

    diffs = repo.diff(initial.commit_id, updated.commit_id)
    a_diff = next(diff for diff in diffs if diff.resource_id == "a")
    assert a_diff.unified_diff == "--- a.md\n+++ a.md\n@@ -1 +1 @@\n-one\n+two\n"


def test_content_size_uses_immutable_blob_metadata_without_reading_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    initial = repo.commit(_changes(Create("note", "note.md", b"old"), key="create"))
    repo.commit(_changes(Update("note", repo.get("note").version_id, b"newer"), key="update"))
    initial_version = repo.get("note", at=initial.commit_id)

    def reject_read(*_args: object) -> bytes:
        raise AssertionError("content size must not read a blob")

    monkeypatch.setattr(repo._storage, "read_blob", reject_read)

    assert repo.content_size(initial_version) == 3
    assert repo.content_size("note", at=initial.commit_id) == 3
    assert repo.content_size("note") == 5


def test_history_stop_before_is_exclusive_bounded_and_rejects_unreachable_stops(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    watermark = repo.commit(_changes(Create("note", "note.md", b"one"), key="one"))
    second = repo.commit(_changes(Update("note", repo.get("note").version_id, b"two"), key="two"))
    third = repo.commit(_changes(Update("note", repo.get("note").version_id, b"three"), key="three"))
    head = repo.head
    assert head == third.commit_id

    original_load = repo._load_commit
    loaded: list[str] = []

    def track_load(commit_id: object):
        loaded.append(str(commit_id))
        return original_load(commit_id)

    monkeypatch.setattr(repo, "_load_commit", track_load)

    assert [commit.commit_id for commit in repo.history(stop_before=watermark.commit_id)] == [
        third.commit_id,
        second.commit_id,
    ]
    assert loaded == [third.commit_id, second.commit_id, second.commit_id, watermark.commit_id]

    loaded.clear()
    assert repo.current_revision == head
    assert loaded == []

    assert repo.history(stop_before=head) == ()
    assert loaded == []

    other = _repo(tmp_path / "other").commit(_changes(Create("other", "other.md", b"other"), key="other"))
    with pytest.raises(KeyError, match="stop boundary is not reachable"):
        repo.history(stop_before=other.commit_id)


def test_diff_page_is_bounded_exact_unicode_safe_and_tail_addressable(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    initial = repo.commit(
        _changes(
            Create("a", "a.md", b"old\n"),
            Create("z", "z.md", b"z0\n"),
            key="create",
        )
    )
    a0 = repo.get("a")
    z0 = repo.get("z")
    updated = repo.commit(
        _changes(
            Update("a", a0.version_id, ("😀 evidence\n" * 40).encode()),
            Update("z", z0.version_id, b"z1\n"),
            key="update",
        )
    )

    expected = next(
        diff.unified_diff for diff in repo.diff(initial.commit_id, updated.commit_id) if diff.resource_id == "a"
    )
    assert [diff.resource_id for diff in repo.diff(initial.commit_id, updated.commit_id, resource_ids=("a",))] == ["a"]
    with pytest.raises(TypeError, match="iterable of resource IDs"):
        repo.diff(initial.commit_id, updated.commit_id, resource_ids="a")
    pages = []
    offset = 0
    while True:
        page = repo.diff_page(initial.commit_id, updated.commit_id, "a", offset=offset, limit=37)
        pages.append(page.unified_diff)
        assert page.offset == offset
        assert page.end_offset == offset + len(page.unified_diff)
        assert len(page.unified_diff) <= 37
        if not page.has_more:
            break
        offset = page.end_offset
    assert "".join(pages) == expected

    tail = repo.diff_page(initial.commit_id, updated.commit_id, "a", limit=37, tail=True)
    tail_offset = 0 if not expected else ((len(expected) - 1) // 37) * 37
    assert tail.unified_diff == expected[tail_offset:]
    assert tail.offset == tail_offset
    assert tail.end_offset == len(expected)
    assert not tail.has_more

    with pytest.raises(IndexError, match="outside the resource diff"):
        repo.diff_page(initial.commit_id, updated.commit_id, "a", offset=len(expected), limit=37)
    with pytest.raises(ValueError, match="positive integer"):
        repo.diff_page(initial.commit_id, updated.commit_id, "a", limit=0)

    huge = repo.commit(
        _changes(
            Update("a", repo.get("a").version_id, b"x" * 2_000_000),
            key="huge-single-line",
        )
    )
    tiny_page = repo.diff_page(updated.commit_id, huge.commit_id, "a", limit=7)
    assert len(tiny_page.unified_diff) == 7
    assert tiny_page.has_more


def test_exact_commit_and_version_diff_avoid_ref_and_tree_history_walks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    initial = repo.commit(_changes(Create("note", "note.md", b"old\n"), key="create"))
    before = repo.get("note")
    updated = repo.commit(_changes(Update("note", before.version_id, b"new\n"), key="update"))
    change = updated.changes[0]

    def reject_walk(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("exact immutable evidence must not walk ref history or trees")

    monkeypatch.setattr(repo, "_require_reachable", reject_walk)
    monkeypatch.setattr(repo, "_tree_at", reject_walk)

    assert repo.inspect_commit(updated.commit_id) == updated
    expected = "--- note.md\n+++ note.md\n@@ -1 +1 @@\n-old\n+new\n"
    page = repo.diff_versions_page(change.before, change.after, limit=1_000, source_byte_limit=1_000)
    assert page.unified_diff == expected
    assert not page.has_more
    assert initial.commit_id == updated.parent_id


def test_exact_version_diff_preflights_source_size_before_blob_reads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _repo(tmp_path)
    initial = repo.commit(_changes(Create("note", "note.md", b"old\n"), key="create"))
    updated = repo.commit(
        _changes(
            Update("note", repo.get("note").version_id, b"x" * 2_000_000),
            key="large",
        )
    )
    change = updated.changes[0]

    def reject_read(*_args: object) -> bytes:
        raise AssertionError("oversized exact evidence must be rejected before blob reads")

    monkeypatch.setattr(repo._storage, "read_blob", reject_read)

    with pytest.raises(RevisionContentLimitError) as raised:
        repo.diff_versions_page(change.before, change.after, source_byte_limit=1024)

    assert raised.value.actual_bytes > raised.value.limit_bytes
    assert repo.commit_size(initial.commit_id) > 0


def test_objects_are_immutable_content_addressed_and_deduplicated(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    commit = repo.commit(
        _changes(
            Create("first", "first.md", b"same"),
            Create("second", "second.md", b"same"),
            key="same-content",
        )
    )

    assert sorted(path.name for path in repo.history_root.joinpath("objects", "blobs").iterdir()) == [sha256(b"same")]
    assert repo._storage.write_tree(repo._storage.read_tree(commit.tree_id)) == commit.tree_id
    assert repo._storage.write_commit(repo._storage.read_commit(commit.commit_id)) == commit.commit_id
    assert len(list(repo.history_root.joinpath("objects", "trees").iterdir())) == 1
    assert len(list(repo.history_root.joinpath("commits").iterdir())) == 1
    assert [path.name for path in repo.history_root.joinpath("published").iterdir()] == [commit.commit_id]


def test_idempotency_replays_exact_request_and_rejects_conflicting_reuse(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    request = _changes(Create("note", "note.md", b"one"), key="retry")
    first = repo.commit(request)
    legacy_request_hash = sha256(
        canonical_jsonl(
            {
                "version": 1,
                "actor": "tester",
                "origin": "test",
                "reason": "exercise revision contract",
                "expected_head": None,
                "operations": [
                    {
                        "resource_id": "note",
                        "operation": "create",
                        "path": "note.md",
                        "content_hash": sha256(b"one"),
                    }
                ],
            }
        )
    )

    assert repo.commit(request) == first
    persisted = repo._storage.read_idempotency(sha256(b"retry"))
    assert persisted is not None and persisted["request_hash"] == legacy_request_hash
    assert len(repo.history()) == 1
    with pytest.raises(IdempotencyConflictError, match="reused for a different change set"):
        repo.commit(_changes(Create("note", "note.md", b"different"), key="retry"))


@pytest.mark.parametrize(
    ("failpoint", "canonical_before_recovery", "expected", "recovery_count"),
    [
        ("after_objects", b"old-a", (b"old-a", b"old-b"), 0),
        ("before_prepared", b"old-a", (b"old-a", b"old-b"), 1),
        ("after_prepared", b"old-a", (b"old-a", b"old-b"), 1),
        ("after_displaced:0", b"old-a", (b"old-a", b"old-b"), 1),
        ("after_displaced:1", b"old-a", (b"old-a", b"old-b"), 1),
        ("after_installed:0", b"old-a", (b"old-a", b"old-b"), 1),
        ("after_installed:1", b"old-a", (b"old-a", b"old-b"), 1),
        ("before_decision", b"old-a", (b"old-a", b"old-b"), 1),
        ("after_decision", b"new-a", (b"new-a", b"new-b"), 1),
        ("after_ref", b"new-a", (b"new-a", b"new-b"), 1),
        ("after_publication", b"new-a", (b"new-a", b"new-b"), 1),
        ("after_idempotency", b"new-a", (b"new-a", b"new-b"), 1),
        ("after_committed", b"new-a", (b"new-a", b"new-b"), 1),
        ("after_cleanup", b"new-a", (b"new-a", b"new-b"), 0),
    ],
)
def test_recovery_at_decision_and_ref_boundaries_never_leaves_mixed_state(
    tmp_path: Path,
    monkeypatch,
    failpoint: str,
    canonical_before_recovery: bytes,
    expected: tuple[bytes, bytes],
    recovery_count: int,
) -> None:
    repo = _repo(tmp_path)
    seeded = repo.commit(
        _changes(
            Create("a", "a.md", b"old-a"),
            Create("b", "b.md", b"old-b"),
            key="seed",
        )
    )
    a0 = repo.get("a")
    b0 = repo.get("b")

    def interrupt(point: str) -> None:
        if point == failpoint:
            raise RuntimeError(point)

    monkeypatch.setattr(repo, "_checkpoint", interrupt)
    with pytest.raises(RuntimeError, match=failpoint):
        repo.commit(
            _changes(
                Update("a", a0.version_id, b"new-a"),
                Update("b", b0.version_id, b"new-b"),
                key="interrupted",
            )
        )

    if canonical_before_recovery == b"old-a":
        assert repo._storage.read_ref() == seeded.commit_id
    else:
        assert repo._storage.read_ref() != seeded.commit_id
    monkeypatch.setattr(repo, "_checkpoint", lambda point: None)
    assert len(repo.recover()) == recovery_count
    recovered = ManagedFileRepository(repo.root)
    assert ((repo.root / "a.md").read_bytes(), (repo.root / "b.md").read_bytes()) == expected
    assert recovered.recover() == ()
    recovered_head = recovered.head
    assert recovered_head is not None
    assert recovered.inspect_commit(recovered_head).commit_id == recovered_head
    assert not any(recovered.history_root.joinpath("transactions").iterdir())


def test_same_process_concurrent_cas_has_one_winner(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    repo.commit(_changes(Create("note", "note.md", b"zero"), key="seed"))
    expected = repo.get("note").version_id
    barrier = Barrier(2)

    def update(index: int) -> str:
        contender = ManagedFileRepository(repo.root)
        barrier.wait()
        try:
            contender.commit(
                _changes(
                    Update("note", expected, str(index).encode()),
                    key=f"contender-{index}",
                )
            )
        except RevisionConflictError:
            return "conflict"
        return "committed"

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = sorted(executor.map(update, (1, 2)))

    assert outcomes == ["committed", "conflict"]
    assert ManagedFileRepository(repo.root).integrity_report().ok


def test_open_automatically_recovers_an_interrupted_materialization(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = _repo(tmp_path)
    repo.commit(_changes(Create("note", "note.md", b"old"), key="seed"))
    version = repo.get("note")

    def interrupt(point: str) -> None:
        if point == "after_installed:0":
            raise RuntimeError(point)

    monkeypatch.setattr(repo, "_checkpoint", interrupt)
    with pytest.raises(RuntimeError, match="after_installed"):
        repo.commit(_changes(Update("note", version.version_id, b"new"), key="interrupted"))

    reopened = ManagedFileRepository(repo.root)
    assert reopened.read("note") == b"old"
    assert (reopened.root / "note.md").read_bytes() == b"old"
    assert not any(reopened.history_root.joinpath("transactions").iterdir())


def test_collect_respects_grace_and_keeps_conflict_transaction_objects(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)
    repo.commit(_changes(Create("note", "note.md", b"old"), key="seed"))
    original = repo.get("note")
    orphan = repo._storage.write_blob(b"orphan")
    orphan_path = repo.history_root / "objects" / "blobs" / orphan
    now = datetime.now(UTC)
    os.utime(orphan_path, (now.timestamp(), now.timestamp()))

    retained = repo.collect(now=now, grace_period=timedelta(days=1))
    assert retained.removed == 0
    assert orphan_path.exists()

    def stop_while_pending(point: str) -> None:
        if point == "after_prepared":
            raise RuntimeError(point)

    monkeypatch.setattr(repo, "_checkpoint", stop_while_pending)
    with pytest.raises(RuntimeError):
        repo.commit(_changes(Update("note", original.version_id, b"candidate"), key="pending"))
    with repo._storage.locked():
        pending = repo._transactions.load_protected_transactions()
        reachable_commits, _, _ = repo._collection_roots()
    assert len(pending) == 1
    assert pending[0].new_head in reachable_commits
    assert len(repo.recover()) == 1

    def interrupt(point: str) -> None:
        if point == "after_installed:0":
            raise RuntimeError(point)

    monkeypatch.setattr(repo, "_checkpoint", interrupt)
    with pytest.raises(RuntimeError):
        repo.commit(_changes(Update("note", original.version_id, b"candidate"), key="conflict"))
    (repo.root / "note.md").write_bytes(b"external")
    monkeypatch.setattr(repo, "_checkpoint", lambda point: None)
    assert len(repo.recover()) == 1
    assert any(repo.history_root.joinpath("conflicts").iterdir())

    collected = repo.collect(now=now + timedelta(days=2), grace_period=timedelta())
    assert collected.removed >= 1
    assert not orphan_path.exists()
    assert repo.history_root.joinpath("objects", "blobs", sha256(b"candidate")).exists()


def test_gc_fails_before_sweep_when_a_reachable_blob_is_missing(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    repo.commit(_changes(Create("note", "note.md", b"live"), key="seed"))
    orphan = repo._storage.write_blob(b"old orphan")
    orphan_path = repo.history_root / "objects" / "blobs" / orphan
    os.utime(orphan_path, (0, 0))
    repo.history_root.joinpath("objects", "blobs", sha256(b"live")).unlink()

    with pytest.raises(CorruptRepositoryError, match="required file is missing"):
        repo.collect(now=datetime.now(UTC), grace_period=timedelta())

    assert orphan_path.exists()


def test_recovery_removes_only_valid_private_object_temps(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    object_id = sha256(b"unfinished")
    temp = repo.history_root / "objects" / "blobs" / f".{object_id}.{'a' * 32}.tmp"
    temp.write_bytes(b"unfinished")

    assert repo.recover() == ()
    assert not temp.exists()

    hostile = repo.history_root / "objects" / "blobs" / f".not-an-object.{'b' * 32}.tmp"
    hostile.write_bytes(b"keep")
    with pytest.raises(CorruptRepositoryError, match="invalid private temporary"):
        repo.recover()
    assert hostile.exists()


def test_aborted_orphan_commit_is_not_a_public_historical_revision(tmp_path: Path, monkeypatch) -> None:
    repo = _repo(tmp_path)

    def interrupt(point: str) -> None:
        if point == "after_objects":
            raise RuntimeError(point)

    monkeypatch.setattr(repo, "_checkpoint", interrupt)
    with pytest.raises(RuntimeError, match="after_objects"):
        repo.commit(_changes(Create("note", "note.md", b"unpublished"), key="orphan"))
    orphan = next(repo.history_root.joinpath("commits").iterdir()).stem

    assert repo.head is None
    with pytest.raises(KeyError, match="not reachable"):
        repo.read("note", at=orphan)
    with pytest.raises(KeyError, match="unknown commit"):
        repo.inspect_commit(orphan)
    repo._storage.compare_and_swap_ref(None, orphan)
    with pytest.raises(CorruptRepositoryError, match="current ref points to unpublished commit"):
        _ = repo.current_revision


def test_rollback_quarantines_then_restores_an_externally_raced_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = _repo(tmp_path)
    repo.commit(_changes(Create("note", "note.md", b"old"), key="seed"))
    version = repo.get("note")

    def stop_after_install(point: str) -> None:
        if point == "after_installed:0":
            raise RuntimeError(point)

    monkeypatch.setattr(repo, "_checkpoint", stop_after_install)
    with pytest.raises(RuntimeError, match="after_installed"):
        repo.commit(_changes(Update("note", version.version_id, b"candidate"), key="candidate"))

    target = repo.root / "note.md"
    original_rename = anchored._rename_no_replace
    raced = False

    def race_before_quarantine(source_parent, source, target_parent, destination):
        nonlocal raced
        if not raced and os.fspath(source) == "note.md" and os.fspath(destination) == "0000":
            replacement = repo.root / "external.tmp"
            replacement.write_bytes(b"external")
            os.replace(replacement, target)
            raced = True
        return original_rename(source_parent, source, target_parent, destination)

    monkeypatch.setattr(anchored, "_rename_no_replace", race_before_quarantine)
    monkeypatch.setattr(repo, "_checkpoint", lambda point: None)
    assert len(repo.recover()) == 1

    assert raced
    assert target.read_bytes() == b"external"
    assert repo.read("note") == b"old"
    assert any(repo.history_root.joinpath("conflicts").iterdir())


def test_displacement_race_quarantines_an_external_symlink_without_following_it(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = _repo(tmp_path)
    repo.commit(_changes(Create("note", "note.md", b"old"), key="seed"))
    version = repo.get("note")
    target = repo.root / "note.md"
    outside = tmp_path / "outside"
    outside.write_bytes(b"outside")
    original_rename = anchored._rename_no_replace
    raced = False

    def race_before_displacement(source_parent, source, target_parent, destination):
        nonlocal raced
        if not raced and os.fspath(source) == "note.md" and os.fspath(destination) == "0000":
            target.unlink()
            target.symlink_to(outside)
            raced = True
        return original_rename(source_parent, source, target_parent, destination)

    monkeypatch.setattr(anchored, "_rename_no_replace", race_before_displacement)
    with pytest.raises(RevisionConflictError, match="changed during displacement"):
        repo.commit(_changes(Update("note", version.version_id, b"new"), key="update"))

    assert raced
    assert target.read_bytes() == b"old"
    assert outside.read_bytes() == b"outside"
    assert repo.read("note") == b"old"
    assert any(repo.history_root.joinpath("conflicts").iterdir())


def test_atomic_displacement_preserves_an_external_file_replacement(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = _repo(tmp_path)
    repo.commit(_changes(Create("note", "note.md", b"old"), key="seed"))
    version = repo.get("note")
    target = repo.root / "note.md"
    original_rename = anchored._rename_no_replace
    raced = False

    def replace_before_displacement(source_parent, source, target_parent, destination):
        nonlocal raced
        if not raced and os.fspath(source) == "note.md" and os.fspath(destination) == "0000":
            replacement = repo.root / "external.tmp"
            replacement.write_bytes(b"external")
            os.replace(replacement, target)
            raced = True
        return original_rename(source_parent, source, target_parent, destination)

    monkeypatch.setattr(anchored, "_rename_no_replace", replace_before_displacement)
    with pytest.raises(RevisionConflictError, match="changed during displacement"):
        repo.commit(_changes(Update("note", version.version_id, b"new"), key="update"))

    assert raced
    assert target.read_bytes() == b"external"
    assert repo.read("note") == b"old"
    assert any(repo.history_root.joinpath("conflicts").iterdir())


def test_metadata_write_is_anchored_against_parent_symlink_swap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = _repo(tmp_path)
    blobs = repo.history_root / "objects" / "blobs"
    held = blobs.with_name("blobs-held")
    outside = tmp_path / "outside-blobs"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_bytes(b"keep")
    original_open = os.open
    raced = False

    def swap_before_temporary_open(path, flags, *args, **kwargs):
        nonlocal raced
        if (
            not raced
            and isinstance(path, str)
            and path.startswith(".")
            and path.endswith(".tmp")
            and kwargs.get("dir_fd") is not None
        ):
            blobs.rename(held)
            blobs.symlink_to(outside, target_is_directory=True)
            raced = True
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap_before_temporary_open)
    object_id = repo._storage.write_blob(b"race")
    blobs.unlink()
    held.rename(blobs)

    assert raced
    assert sentinel.read_bytes() == b"keep"
    assert not (outside / object_id).exists()
    assert repo._storage.read_blob(object_id) == b"race"


def test_install_is_anchored_against_parent_symlink_swap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = _repo(tmp_path)
    repo.commit(_changes(Create("note", "notes/note.md", b"old"), key="seed"))
    version = repo.get("note")
    notes = repo.root / "notes"
    held = repo.root / "notes-held"
    outside = tmp_path / "outside-install"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_bytes(b"keep")
    original_open = os.open
    raced = False

    def swap_before_install(path, flags, *args, **kwargs):
        nonlocal raced
        if not raced and isinstance(path, str) and path.startswith(".note.md.") and kwargs.get("dir_fd") is not None:
            notes.rename(held)
            notes.symlink_to(outside, target_is_directory=True)
            raced = True
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap_before_install)
    with pytest.raises(UnsafePathError):
        repo.commit(_changes(Update("note", version.version_id, b"new"), key="update"))
    notes.unlink()
    held.rename(notes)

    assert raced
    assert sentinel.read_bytes() == b"keep"
    assert not (outside / "note.md").exists()
    assert repo.read("note") == b"old"


def test_root_replacement_cannot_publish_into_a_detached_repository(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = _repo(tmp_path)
    repo.commit(_changes(Create("note", "note.md", b"old"), key="seed"))
    version = repo.get("note")
    root = repo.root
    held = root.with_name("work-held")
    original_open = os.open
    raced = False

    def replace_root_before_install(path, flags, *args, **kwargs):
        nonlocal raced
        if not raced and isinstance(path, str) and path.startswith(".note.md.") and kwargs.get("dir_fd") is not None:
            root.rename(held)
            root.mkdir()
            raced = True
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", replace_root_before_install)
    with pytest.raises(UnsafePathError, match="anchored directory"):
        repo.commit(_changes(Update("note", version.version_id, b"new"), key="update"))

    assert raced
    assert list(root.iterdir()) == []
    root.rmdir()
    held.rename(root)
    reopened = ManagedFileRepository(root)
    assert reopened.read("note") == b"old"
    assert (root / "note.md").read_bytes() == b"old"


def test_root_replacement_during_external_history_ref_write_reverts_publication(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "work"
    history = tmp_path / "history"
    repo = ManagedFileRepository(root, history_root=history)
    original = repo.commit(_changes(Create("note", "note.md", b"old"), key="seed"))
    version = repo.get("note")
    held = tmp_path / "work-held"
    original_open = os.open
    raced = False

    def replace_root_during_ref_write(path, flags, *args, **kwargs):
        nonlocal raced
        if (
            not raced
            and isinstance(path, str)
            and path.startswith(".current.jsonl.")
            and path.endswith(".tmp")
            and kwargs.get("dir_fd") is not None
        ):
            root.rename(held)
            root.mkdir()
            raced = True
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", replace_root_during_ref_write)
    with pytest.raises(UnsafePathError, match="anchored directory"):
        repo.commit(_changes(Update("note", version.version_id, b"new"), key="update"))

    assert raced
    assert repo._storage.read_ref() == original.commit_id
    assert list(root.iterdir()) == []
    root.rmdir()
    held.rename(root)
    reopened = ManagedFileRepository(root, history_root=history)
    assert reopened.head == original.commit_id
    assert reopened.read("note") == b"old"
    assert (root / "note.md").read_bytes() == b"old"


def test_root_replacement_during_nested_history_ref_write_uses_pinned_compensation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = _repo(tmp_path)
    original = repo.commit(_changes(Create("note", "note.md", b"old"), key="seed"))
    version = repo.get("note")
    root = repo.root
    held = root.with_name("work-held")
    original_open = os.open
    raced = False

    def replace_root_during_ref_write(path, flags, *args, **kwargs):
        nonlocal raced
        if (
            not raced
            and isinstance(path, str)
            and path.startswith(".current.jsonl.")
            and path.endswith(".tmp")
            and kwargs.get("dir_fd") is not None
        ):
            root.rename(held)
            root.mkdir()
            raced = True
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", replace_root_during_ref_write)
    with pytest.raises(UnsafePathError, match="anchored directory"):
        repo.commit(_changes(Update("note", version.version_id, b"new"), key="update"))

    assert raced
    ref = json.loads((held / ".managed-history" / "refs" / "current.jsonl").read_bytes())
    assert ref["commit_id"] == original.commit_id
    assert list(root.iterdir()) == []
    root.rmdir()
    held.rename(root)
    reopened = ManagedFileRepository(root)
    assert reopened.head == original.commit_id
    assert reopened.read("note") == b"old"
    assert (root / "note.md").read_bytes() == b"old"


def test_root_replacement_after_ref_is_reported_as_post_commit_drift(
    tmp_path: Path,
    monkeypatch,
) -> None:
    root = tmp_path / "work"
    history = tmp_path / "history"
    repo = ManagedFileRepository(root, history_root=history)
    repo.commit(_changes(Create("note", "note.md", b"old"), key="seed"))
    version = repo.get("note")
    held = tmp_path / "work-held"
    raced = False

    def replace_after_ref(point: str) -> None:
        nonlocal raced
        if point == "after_ref" and not raced:
            root.rename(held)
            root.mkdir()
            raced = True

    monkeypatch.setattr(repo, "_checkpoint", replace_after_ref)
    committed = repo.commit(_changes(Update("note", version.version_id, b"new"), key="update"))

    assert raced
    reopened_drifted = ManagedFileRepository(root, history_root=history)
    assert reopened_drifted.head == committed.commit_id
    assert [issue.code for issue in reopened_drifted.integrity_report().issues] == ["managed_file_mismatch"]
    root.rmdir()
    held.rename(root)
    reopened_restored = ManagedFileRepository(root, history_root=history)
    assert reopened_restored.head == committed.commit_id
    assert reopened_restored.integrity_report().ok
    assert (root / "note.md").read_bytes() == b"new"


def test_install_copies_a_raced_hardlinked_artifact_into_a_standalone_file(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = _repo(tmp_path)
    repo.commit(_changes(Create("note", "note.md", b"old"), key="seed"))
    version = repo.get("note")
    outside = tmp_path / "outside"
    outside.write_bytes(b"new")
    original_open = os.open
    raced = False

    def replace_artifact_before_install(path, flags, *args, **kwargs):
        nonlocal raced
        if not raced and isinstance(path, str) and path.startswith(".note.md.") and kwargs.get("dir_fd") is not None:
            transaction = next(repo.history_root.joinpath("transactions").iterdir())
            publish = transaction / "publish" / "0000"
            publish.unlink()
            os.link(outside, publish)
            raced = True
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", replace_artifact_before_install)
    repo.commit(_changes(Update("note", version.version_id, b"new"), key="update"))

    assert raced
    assert (repo.root / "note.md").read_bytes() == b"new"
    assert (repo.root / "note.md").stat().st_nlink == 1
    assert outside.read_bytes() == b"new"
    assert outside.stat().st_nlink == 1


def test_displacement_is_anchored_against_parent_symlink_swap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = _repo(tmp_path)
    repo.commit(_changes(Create("note", "notes/note.md", b"old"), key="seed"))
    version = repo.get("note")
    notes = repo.root / "notes"
    held = repo.root / "notes-held"
    outside = tmp_path / "outside-displace"
    outside.mkdir()
    outside_note = outside / "note.md"
    outside_note.write_bytes(b"old")
    sentinel = outside / "sentinel"
    sentinel.write_bytes(b"keep")
    original_rename = anchored._rename_no_replace
    raced = False

    def swap_before_displacement(source_parent, source, target_parent, destination):
        nonlocal raced
        if not raced and os.fspath(source) == "note.md" and os.fspath(destination) == "0000":
            notes.rename(held)
            notes.symlink_to(outside, target_is_directory=True)
            raced = True
        return original_rename(source_parent, source, target_parent, destination)

    monkeypatch.setattr(anchored, "_rename_no_replace", swap_before_displacement)
    with pytest.raises(UnsafePathError):
        repo.commit(_changes(Update("note", version.version_id, b"new"), key="update"))
    notes.unlink()
    held.rename(notes)

    assert raced
    assert outside_note.read_bytes() == b"old"
    assert sentinel.read_bytes() == b"keep"
    assert repo.read("note") == b"old"


def test_repository_canonicalizes_standard_macos_tmp_alias() -> None:
    with tempfile.TemporaryDirectory(dir="/tmp") as directory:
        repo = ManagedFileRepository(Path(directory) / "work")
        repo.commit(_changes(Create("note", "note.md", b"one"), key="create"))

        assert repo.root == Path(directory).resolve() / "work"
        assert repo.read("note") == b"one"


def test_paths_reject_escape_ancestor_duplicate_symlink_and_hard_link(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    for index, path in enumerate(("../escape.md", "/absolute.md", "dir/../escape.md", "dir\\escape.md")):
        with pytest.raises(UnsafePathError):
            repo.commit(_changes(Create(f"bad-{index}", path, b"bad"), key=f"bad-{index}"))

    with pytest.raises(RevisionConflictError, match="share one path"):
        repo.commit(_changes(Create("one", "same.md", b"one"), Create("two", "same.md", b"two"), key="duplicate"))
    with pytest.raises(RevisionConflictError, match="overlap"):
        repo.commit(_changes(Create("one", "a", b"one"), Create("two", "a/b", b"two"), key="ancestor"))
    with pytest.raises(ValueError, match="touch each resource only once"):
        repo.commit(_changes(Create("same", "one.md", b"one"), Create("same", "two.md", b"two"), key="repeat"))

    outside = tmp_path / "outside"
    outside.mkdir()
    (repo.root / "linked").symlink_to(outside, target_is_directory=True)
    with pytest.raises(UnsafePathError, match="symlink"):
        repo.commit(_changes(Create("linked", "linked/file.md", b"bad"), key="symlink"))

    repo.commit(_changes(Create("safe", "safe.md", b"old"), key="safe"))
    os.link(repo.root / "safe.md", repo.root / "alias.md")
    with pytest.raises(UnsafePathError, match="hard links"):
        repo.commit(_changes(Update("safe", repo.get("safe").version_id, b"new"), key="hard-link"))


def test_metadata_intermediate_and_transaction_artifact_symlinks_fail_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    metadata_repo = ManagedFileRepository(tmp_path / "metadata")
    objects = metadata_repo.history_root / "objects"
    outside_objects = tmp_path / "outside-objects"
    outside_objects.mkdir()
    sentinel = outside_objects / "sentinel"
    sentinel.write_bytes(b"keep")
    shutil.rmtree(objects)
    objects.symlink_to(outside_objects, target_is_directory=True)

    with pytest.raises(UnsafePathError, match="directory is a symlink"):
        metadata_repo.commit(_changes(Create("note", "note.md", b"one"), key="metadata-symlink"))
    assert sentinel.read_bytes() == b"keep"

    transaction_repo = ManagedFileRepository(tmp_path / "transaction")

    def interrupt(point: str) -> None:
        if point == "after_prepared":
            raise RuntimeError(point)

    monkeypatch.setattr(transaction_repo, "_checkpoint", interrupt)
    with pytest.raises(RuntimeError, match="after_prepared"):
        transaction_repo.commit(_changes(Create("note", "note.md", b"one"), key="transaction-symlink"))
    transaction = next(transaction_repo.history_root.joinpath("transactions").iterdir())
    publish = transaction / "publish"
    outside_publish = tmp_path / "outside-publish"
    shutil.move(publish, outside_publish)
    publish.symlink_to(outside_publish, target_is_directory=True)

    with pytest.raises(UnsafePathError, match="directory is a symlink"):
        ManagedFileRepository(transaction_repo.root)
    assert not (transaction_repo.root / "note.md").exists()
    assert (outside_publish / "0000").read_bytes() == b"one"


def test_corrupt_ref_manifest_and_missing_object_fail_closed(tmp_path: Path, monkeypatch) -> None:
    ref_repo = ManagedFileRepository(tmp_path / "ref")
    ref_repo.history_root.joinpath("refs", "current.jsonl").write_bytes(b'{"commit_id":"not-a-hash","version":1}\n')
    with pytest.raises(CorruptRepositoryError):
        _ = ref_repo.head

    manifest_repo = ManagedFileRepository(tmp_path / "manifest")

    def interrupt(point: str) -> None:
        if point == "after_prepared":
            raise RuntimeError(point)

    monkeypatch.setattr(manifest_repo, "_checkpoint", interrupt)
    with pytest.raises(RuntimeError):
        manifest_repo.commit(_changes(Create("note", "note.md", b"one"), key="manifest"))
    transaction = next(manifest_repo.history_root.joinpath("transactions").iterdir())
    (transaction / "manifest.jsonl").write_bytes(b"corrupt\n")
    with pytest.raises(CorruptRepositoryError):
        ManagedFileRepository(manifest_repo.root)

    object_repo = ManagedFileRepository(tmp_path / "missing")
    object_repo.commit(_changes(Create("note", "note.md", b"one"), key="object"))
    object_repo.history_root.joinpath("objects", "blobs", sha256(b"one")).unlink()
    with pytest.raises(CorruptRepositoryError):
        object_repo.read("note")


def test_tree_and_commit_semantics_fail_closed_even_when_records_are_content_addressed(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    commit = repo.commit(_changes(Create("note", "note.md", b"one"), key="seed"))

    record = repo._storage.read_commit(commit.commit_id)
    raw_changes = record["changes"]
    assert isinstance(raw_changes, list)
    raw_changes[0]["action"] = "archive"
    fabricated = repo._storage.write_commit(record)
    repo._storage.compare_and_swap_ref(commit.commit_id, fabricated)
    repo._storage.publish_commit(fabricated)
    with pytest.raises(CorruptRepositoryError, match="invalid archive semantics"):
        _ = repo.head

    tree_repo = ManagedFileRepository(tmp_path / "tree")
    blob = tree_repo._storage.write_blob(b"x")
    impossible = tree_record(
        {
            "parent": make_version("parent", "a", blob, ResourceState.ACTIVE),
            "child": make_version("child", "a/b", blob, ResourceState.ACTIVE),
        }
    )
    tree_id = tree_repo._storage.write_tree(impossible)
    with pytest.raises(CorruptRepositoryError, match="overlapping active paths"):
        tree_repo._load_tree(tree_id)


def test_recovery_rejects_decided_rows_that_do_not_equal_the_commit_tree_delta(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = _repo(tmp_path)
    repo.commit(_changes(Create("one", "one.md", b"one"), key="seed"))

    def interrupt(point: str) -> None:
        if point == "after_prepared":
            raise RuntimeError(point)

    monkeypatch.setattr(repo, "_checkpoint", interrupt)
    with pytest.raises(RuntimeError, match="after_prepared"):
        repo.commit(_changes(Create("two", "two.md", b"two"), key="two"))
    transaction = next(repo.history_root.joinpath("transactions").iterdir())
    manifest = json.loads((transaction / "manifest.jsonl").read_bytes())
    manifest["rows"] = []
    forged_content = canonical_jsonl(manifest)
    forged_id = sha256(forged_content)
    forged = transaction.with_name(forged_id)
    os.rename(transaction, forged)
    (forged / "manifest.jsonl").write_bytes(forged_content)
    (forged / "markers" / "DECIDED").write_bytes(b"")

    original_head = repo._storage.read_ref()
    with pytest.raises(CorruptRepositoryError, match="rows do not match"):
        ManagedFileRepository(repo.root)

    assert repo._storage.read_ref() == original_head
    assert not (repo.root / "two.md").exists()


def test_integrity_and_storage_reports_capture_live_state(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    commit = repo.commit(_changes(Create("note", "note.md", b"one"), key="create"))

    storage = repo.storage_report()
    integrity = repo.integrity_report()
    assert storage.resource_count == 1
    assert storage.commit_count == 1
    assert storage.object_count == 3
    assert storage.total_bytes >= storage.blob_bytes + storage.tree_bytes + storage.commit_bytes
    assert integrity.ok
    assert integrity.head_commit == commit.commit_id
    assert integrity.resources == 1
    assert integrity.objects == 3

    (repo.root / "note.md").write_bytes(b"external")
    mismatched = repo.integrity_report()
    assert not mismatched.ok
    assert [issue.code for issue in mismatched.issues] == ["managed_file_mismatch"]


def test_revisions_package_has_no_memory_wiki_agent_or_automation_dependency() -> None:
    package = Path(__file__).parents[1] / "arden" / "revisions"
    forbidden = ("arden.memory", "arden.wiki", "arden.agent", "arden.automation")
    imports: set[str] = set()
    for source in package.glob("*.py"):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)

    assert not any(name == prefix or name.startswith(f"{prefix}.") for name in imports for prefix in forbidden)
