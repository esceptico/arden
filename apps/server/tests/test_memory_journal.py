import errno
import json
import multiprocessing
import os
import shutil
from pathlib import Path

import pytest

from ntrp.memory.journal import VaultJournal


class InjectedFailure(RuntimeError):
    pass


def _cooperating_commit(root: str, content: bytes, barrier, results) -> None:
    journal = VaultJournal(Path(root))
    finish = journal._finish

    def synchronized_finish(commit_path, manifest, commit_id) -> None:
        try:
            barrier.wait(timeout=2)
        except Exception:
            pass
        finish(commit_path, manifest, commit_id)

    journal._finish = synchronized_finish
    try:
        journal.commit({Path("me.md"): content}, expected_files={Path("me.md"): b"old"})
    except Exception as exc:
        results.put((content, type(exc).__name__))
    else:
        results.put((content, "committed"))


def _seed_pair(root: Path) -> None:
    (root / "raw").mkdir(parents=True)
    (root / "me.md").write_bytes(b"old")
    (root / "raw" / "me.md").write_bytes(b"raw-old")


def _read_pair(root: Path) -> tuple[bytes, bytes]:
    return (root / "me.md").read_bytes(), (root / "raw" / "me.md").read_bytes()


def test_commit_preserves_an_external_atomic_replacement_at_finish_entry(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "me.md"
    target.write_bytes(b"old")
    journal = VaultJournal(tmp_path)
    finish = journal._finish

    def replace_then_finish(commit_path, manifest, commit_id) -> None:
        replacement = tmp_path / "external.tmp"
        replacement.write_bytes(b"external")
        os.replace(replacement, target)
        finish(commit_path, manifest, commit_id)

    monkeypatch.setattr(journal, "_finish", replace_then_finish)

    with pytest.raises(ValueError, match=r"conflict|expected state changed"):
        journal.commit({Path("me.md"): b"candidate"}, expected_files={Path("me.md"): b"old"})

    assert target.read_bytes() == b"external"
    versions = list((tmp_path / ".ntrp" / "versions").glob("*/"))
    assert len(versions) == 1
    assert (versions[0] / "staged" / "0000").read_bytes() == b"candidate"
    assert (versions[0] / "displaced" / "0000").read_bytes() == b"external"


def test_cross_process_lock_allows_only_one_expected_state_commit(tmp_path: Path) -> None:
    (tmp_path / "me.md").write_bytes(b"old")
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    results = context.Queue()
    processes = [
        context.Process(target=_cooperating_commit, args=(str(tmp_path), content, barrier, results))
        for content in (b"first", b"second")
    ]

    for process in processes:
        process.start()
    outcomes = [results.get(timeout=10) for _ in processes]
    for process in processes:
        process.join(timeout=10)

    assert all(process.exitcode == 0 for process in processes)
    assert sorted(outcome for _, outcome in outcomes) == ["JournalConflictError", "committed"]
    committed = next(content for content, outcome in outcomes if outcome == "committed")
    assert (tmp_path / "me.md").read_bytes() == committed


def test_late_open_descriptor_write_survives_in_displaced_version(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "me.md"
    target.write_bytes(b"old")
    journal = VaultJournal(tmp_path)

    with target.open("r+b") as external:

        def write_after_install(point: str) -> None:
            if point == "after_installed:0":
                external.seek(0)
                external.write(b"external-late")
                external.truncate()
                external.flush()
                os.fsync(external.fileno())

        monkeypatch.setattr(journal, "_checkpoint", write_after_install)
        journal.commit({Path("me.md"): b"candidate"}, expected_files={Path("me.md"): b"old"})

    assert target.read_bytes() == b"candidate"
    versions = list((tmp_path / ".ntrp" / "versions").glob("*/displaced/0000"))
    assert len(versions) == 1
    assert versions[0].read_bytes() == b"external-late"


def test_external_replacement_after_install_is_not_overwritten(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "me.md"
    target.write_bytes(b"old")
    journal = VaultJournal(tmp_path)

    def replace_after_install(point: str) -> None:
        if point == "after_installed:0":
            replacement = tmp_path / "external.tmp"
            replacement.write_bytes(b"external")
            os.replace(replacement, target)

    monkeypatch.setattr(journal, "_checkpoint", replace_after_install)

    with pytest.raises(ValueError, match="conflict"):
        journal.commit({Path("me.md"): b"candidate"}, expected_files={Path("me.md"): b"old"})

    assert target.read_bytes() == b"external"
    archived = next((tmp_path / ".ntrp" / "versions").iterdir())
    assert (archived / "staged" / "0000").read_bytes() == b"candidate"
    assert (archived / "displaced" / "0000").read_bytes() == b"old"


def test_external_replacement_in_move_link_gap_is_preserved(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "me.md"
    target.write_bytes(b"old")
    journal = VaultJournal(tmp_path)

    def replace_in_gap(point: str) -> None:
        if point == "after_displaced:0":
            replacement = tmp_path / "external.tmp"
            replacement.write_bytes(b"external")
            os.replace(replacement, target)

    monkeypatch.setattr(journal, "_checkpoint", replace_in_gap)

    with pytest.raises(ValueError, match="conflict"):
        journal.commit({Path("me.md"): b"candidate"}, expected_files={Path("me.md"): b"old"})

    assert target.read_bytes() == b"external"
    archived = next((tmp_path / ".ntrp" / "versions").iterdir())
    assert (archived / "staged" / "0000").read_bytes() == b"candidate"
    assert (archived / "displaced" / "0000").read_bytes() == b"old"


def test_external_replacement_after_commit_decision_remains_visible(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "me.md"
    target.write_bytes(b"old")
    journal = VaultJournal(tmp_path)

    def replace_after_decision(point: str) -> None:
        if point == "after_decided_commit":
            replacement = tmp_path / "external.tmp"
            replacement.write_bytes(b"external")
            os.replace(replacement, target)

    monkeypatch.setattr(journal, "_checkpoint", replace_after_decision)

    revision = journal.commit({Path("me.md"): b"candidate"}, expected_files={Path("me.md"): b"old"})

    assert target.read_bytes() == b"external"
    assert journal.canonical_revision == revision
    archived = tmp_path / ".ntrp" / "versions" / revision
    assert (archived / "staged" / "0000").read_bytes() == b"candidate"
    assert (archived / "displaced" / "0000").read_bytes() == b"old"


@pytest.mark.parametrize(
    ("failpoint", "expected"),
    [
        ("after_move:0", (b"old", b"raw-old")),
        ("after_move_target_fsync:0", (b"old", b"raw-old")),
        ("after_move_displaced_fsync:0", (b"old", b"raw-old")),
        ("after_displaced:0", (b"old", b"raw-old")),
        ("after_link:0", (b"old", b"raw-old")),
        ("after_link_fsync:0", (b"old", b"raw-old")),
        ("after_installed:0", (b"old", b"raw-old")),
        ("before_decided_commit", (b"new", b"raw-new")),
        ("after_decided_commit", (b"new", b"raw-new")),
        ("before_revision_publish", (b"new", b"raw-new")),
        ("after_revision_published", (b"new", b"raw-new")),
    ],
)
def test_recovery_is_idempotent_at_publish_boundaries(
    tmp_path: Path, monkeypatch, failpoint: str, expected: tuple[bytes, bytes]
) -> None:
    _seed_pair(tmp_path)
    journal = VaultJournal(tmp_path)

    def inject(point: str) -> None:
        if point == failpoint:
            raise InjectedFailure(point)

    monkeypatch.setattr(journal, "_checkpoint", inject)
    with pytest.raises(InjectedFailure, match=failpoint):
        journal.commit({Path("me.md"): b"new", Path("raw/me.md"): b"raw-new"})

    recovered = VaultJournal(tmp_path).recover()

    assert len(recovered) == 1
    assert _read_pair(tmp_path) == expected
    assert VaultJournal(tmp_path).recover() == ()
    assert not (tmp_path / ".ntrp" / "journal").exists()


def test_conflict_recovery_is_idempotent_after_conflict_marker(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "me.md"
    target.write_bytes(b"old")
    journal = VaultJournal(tmp_path)

    def interrupt_conflict(point: str) -> None:
        if point == "after_displaced:0":
            replacement = tmp_path / "external.tmp"
            replacement.write_bytes(b"external")
            os.replace(replacement, target)
        if point == "after_conflict":
            raise InjectedFailure(point)

    monkeypatch.setattr(journal, "_checkpoint", interrupt_conflict)
    with pytest.raises(InjectedFailure, match="after_conflict"):
        journal.commit({Path("me.md"): b"candidate"}, expected_files={Path("me.md"): b"old"})

    assert target.read_bytes() == b"external"
    assert len(VaultJournal(tmp_path).recover()) == 1
    assert target.read_bytes() == b"external"
    assert VaultJournal(tmp_path).recover() == ()


def test_unsupported_hard_links_fail_closed_before_displacement(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "me.md"
    target.write_bytes(b"old")

    def unsupported(*args, **kwargs) -> None:
        raise OSError(errno.EXDEV, "cross-device link")

    monkeypatch.setattr(os, "link", unsupported)

    with pytest.raises(ValueError, match="hard links are unsupported"):
        VaultJournal(tmp_path).commit({Path("me.md"): b"candidate"})

    assert target.read_bytes() == b"old"
    archived = next((tmp_path / ".ntrp" / "versions").iterdir())
    assert (archived / "staged" / "0000").read_bytes() == b"candidate"


@pytest.mark.parametrize(
    ("failpoint", "expected"),
    [
        ("before_prepare_complete", (b"old", b"raw-old")),
        ("after_prepared", (b"old", b"raw-old")),
        ("after_replace:0", (b"old", b"raw-old")),
        ("after_committed", (b"new", b"raw-new")),
    ],
)
def test_recovery_never_leaves_a_mixed_file_set(tmp_path: Path, monkeypatch, failpoint: str, expected) -> None:
    _seed_pair(tmp_path)
    journal = VaultJournal(tmp_path)

    def inject(point: str) -> None:
        if point == failpoint:
            raise InjectedFailure(point)

    monkeypatch.setattr(journal, "_checkpoint", inject)
    with pytest.raises(InjectedFailure, match=failpoint):
        journal.commit({Path("me.md"): b"new", Path("raw/me.md"): b"raw-new"})

    recovered = VaultJournal(tmp_path).recover()

    assert len(recovered) == 1
    assert _read_pair(tmp_path) == expected
    assert not (tmp_path / ".ntrp" / "journal").exists()


def test_recovery_restores_all_backups_when_staged_content_is_invalid(tmp_path: Path, monkeypatch) -> None:
    _seed_pair(tmp_path)
    journal = VaultJournal(tmp_path)

    def inject(point: str) -> None:
        if point == "after_replace:0":
            raise InjectedFailure(point)

    monkeypatch.setattr(journal, "_checkpoint", inject)
    with pytest.raises(InjectedFailure):
        journal.commit({Path("me.md"): b"new", Path("raw/me.md"): b"raw-new"})

    commit_dir = next((tmp_path / ".ntrp" / "journal").iterdir())
    manifest = json.loads((commit_dir / "manifest.json").read_text(encoding="utf-8"))
    staged = commit_dir / manifest["files"][1]["staged"]
    staged.write_bytes(b"corrupt")

    VaultJournal(tmp_path).recover()

    assert _read_pair(tmp_path) == (b"old", b"raw-old")
    assert not (tmp_path / ".ntrp" / "journal").exists()


def test_projection_rollback_preserves_a_newer_canonical_revision(tmp_path: Path) -> None:
    (tmp_path / "me.md").write_bytes(b"old projection")
    journal = VaultJournal(tmp_path)
    previous = journal.commit({Path("canonical.md"): b"v1"})
    journal.prepare({Path("me.md"): b"new projection"}, _publish_revision=False)
    newer = "f" * 64
    assert newer != previous
    journal._publish_revision(newer)

    VaultJournal(tmp_path).recover(prefer_rollback=True)

    assert (tmp_path / "me.md").read_bytes() == b"old projection"
    assert VaultJournal(tmp_path).canonical_revision == newer
    assert not (tmp_path / ".ntrp" / "journal").exists()


def test_recovery_resumes_cleanup_after_a_completed_rollback(tmp_path: Path, monkeypatch) -> None:
    _seed_pair(tmp_path)
    journal = VaultJournal(tmp_path)

    def inject(point: str) -> None:
        if point == "after_replace:0":
            raise InjectedFailure(point)

    monkeypatch.setattr(journal, "_checkpoint", inject)
    with pytest.raises(InjectedFailure):
        journal.commit({Path("me.md"): b"new", Path("raw/me.md"): b"raw-new"})
    commit_dir = next((tmp_path / ".ntrp" / "journal").iterdir())
    manifest = json.loads((commit_dir / "manifest.json").read_text(encoding="utf-8"))
    (commit_dir / manifest["files"][1]["staged"]).write_bytes(b"corrupt")

    recovering = VaultJournal(tmp_path)

    archive_commit = recovering._archive_commit

    def interrupt_cleanup(path: Path) -> None:
        raise InjectedFailure("cleanup")

    monkeypatch.setattr(recovering, "_archive_commit", interrupt_cleanup)
    with pytest.raises(InjectedFailure, match="cleanup"):
        recovering.recover()

    assert (commit_dir / "markers" / "ROLLED_BACK").exists()
    monkeypatch.setattr(recovering, "_archive_commit", archive_commit)
    VaultJournal(tmp_path).recover()
    assert _read_pair(tmp_path) == (b"old", b"raw-old")
    assert not (tmp_path / ".ntrp" / "journal").exists()


def test_prepare_records_targets_artifacts_and_hashes_without_replacing_targets(tmp_path: Path) -> None:
    _seed_pair(tmp_path)

    prepared = VaultJournal(tmp_path).prepare({Path("me.md"): b"new", Path("raw/me.md"): b"raw-new"})
    manifest = json.loads((prepared.path / "manifest.json").read_text(encoding="utf-8"))

    assert _read_pair(tmp_path) == (b"old", b"raw-old")
    assert (prepared.path / "PREPARED").exists()
    assert prepared.manifest_hash == prepared.commit_id
    assert [row["target"] for row in manifest["files"]] == ["me.md", "raw/me.md"]
    assert all((prepared.path / row["staged"]).is_file() for row in manifest["files"])
    assert all((prepared.path / row["backup"]).is_file() for row in manifest["files"])
    assert all(len(row["sha256"]) == 64 for row in manifest["files"])


def test_migration_commit_atomically_allows_only_its_status_file(tmp_path: Path) -> None:
    (tmp_path / "me.md").write_bytes(b"old")
    journal = VaultJournal(tmp_path)

    journal.commit_migration(
        {Path("me.md"): b"new", Path(".ntrp/maintenance/migration-v2.json"): b'{"schema_version":2}\n'}
    )

    assert (tmp_path / "me.md").read_bytes() == b"new"
    assert (tmp_path / ".ntrp/maintenance/migration-v2.json").is_file()
    with pytest.raises(ValueError, match="metadata directory"):
        journal.commit_migration({Path(".ntrp/maintenance/other.json"): b"bad"})


def test_creating_target_ancestors_fsyncs_each_new_directory_parent(tmp_path: Path, monkeypatch) -> None:
    journal = VaultJournal(tmp_path)
    synced: list[Path] = []
    fsync_dir = journal._fsync_dir

    def capture(path: Path) -> None:
        synced.append(path)
        fsync_dir(path)

    monkeypatch.setattr(journal, "_fsync_dir", capture)
    journal.commit({Path("people/friends/alice.md"): b"Alice\n"})

    assert tmp_path in synced
    assert tmp_path / "people" in synced
    assert synced.index(tmp_path) < synced.index(tmp_path / "people")


def test_commit_rejects_intervening_change_to_expected_file(tmp_path: Path, monkeypatch) -> None:
    _seed_pair(tmp_path)
    journal = VaultJournal(tmp_path)
    prepare = journal.prepare

    def prepare_then_edit(files):
        prepared = prepare(files)
        (tmp_path / "me.md").write_bytes(b"external")
        return prepared

    monkeypatch.setattr(journal, "prepare", prepare_then_edit)

    with pytest.raises(ValueError, match="expected state changed"):
        journal.commit(
            {Path("me.md"): b"new", Path("raw/me.md"): b"raw-new"},
            expected_files={Path("me.md"): b"old"},
        )

    assert _read_pair(tmp_path) == (b"external", b"raw-old")
    assert not (tmp_path / ".ntrp" / "journal").exists()


@pytest.mark.parametrize("hostile", ["meta", "journal", "versions", "revision", "lock"])
def test_commit_rejects_symlinked_internal_journal_paths(tmp_path: Path, hostile: str) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    target = tmp_path / "me.md"
    target.write_bytes(b"old")

    if hostile == "meta":
        (tmp_path / ".ntrp").symlink_to(outside, target_is_directory=True)
    else:
        meta = tmp_path / ".ntrp"
        meta.mkdir()
        if hostile == "journal":
            (meta / "journal").symlink_to(outside, target_is_directory=True)
        elif hostile == "versions":
            (meta / "versions").symlink_to(outside, target_is_directory=True)
        elif hostile == "lock":
            (meta / "canonical.lock").symlink_to(outside / "lock")
        else:
            external_revision = outside / "revision"
            external_revision.write_text("sentinel", encoding="utf-8")
            (meta / "canonical-revision").symlink_to(external_revision)

    with pytest.raises(ValueError, match="symlink"):
        VaultJournal(tmp_path).commit({Path("me.md"): b"new"})

    assert target.read_bytes() == b"old"


def test_recovery_rejects_a_symlinked_commit_directory(tmp_path: Path) -> None:
    journal_root = tmp_path / ".ntrp" / "journal"
    journal_root.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (journal_root / "hostile").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        VaultJournal(tmp_path).recover()


def test_recovery_rejects_symlinked_metadata_inside_a_commit(tmp_path: Path) -> None:
    prepared = VaultJournal(tmp_path).prepare({Path("me.md"): b"new"})
    outside_manifest = tmp_path / "outside-manifest.json"
    shutil.copyfile(prepared.path / "manifest.json", outside_manifest)
    (prepared.path / "manifest.json").unlink()
    (prepared.path / "manifest.json").symlink_to(outside_manifest)

    with pytest.raises(ValueError, match="symlink"):
        VaultJournal(tmp_path).recover()


@pytest.mark.parametrize("artifact_dir", ["staged", "backups"])
def test_recovery_rejects_substituted_artifact_directories(tmp_path: Path, artifact_dir: str) -> None:
    (tmp_path / "me.md").write_bytes(b"old")
    prepared = VaultJournal(tmp_path).prepare({Path("me.md"): b"new"})
    original = prepared.path / artifact_dir
    outside = tmp_path / "outside" / artifact_dir
    outside.parent.mkdir()
    shutil.copytree(original, outside)
    outside_before = (outside / "0000").read_bytes()
    shutil.rmtree(original)
    original.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        VaultJournal(tmp_path).recover()

    assert (tmp_path / "me.md").read_bytes() == b"old"
    assert (outside / "0000").read_bytes() == outside_before
    assert prepared.path.exists()
    assert original.is_symlink()
