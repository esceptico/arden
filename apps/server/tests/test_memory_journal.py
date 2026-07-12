import json
import shutil
from pathlib import Path

import pytest

from ntrp.memory.journal import VaultJournal


class InjectedFailure(RuntimeError):
    pass


def _seed_pair(root: Path) -> None:
    (root / "raw").mkdir(parents=True)
    (root / "me.md").write_bytes(b"old")
    (root / "raw" / "me.md").write_bytes(b"raw-old")


def _read_pair(root: Path) -> tuple[bytes, bytes]:
    return (root / "me.md").read_bytes(), (root / "raw" / "me.md").read_bytes()


@pytest.mark.parametrize(
    ("failpoint", "expected"),
    [
        ("before_prepare_complete", (b"old", b"raw-old")),
        ("after_prepared", (b"new", b"raw-new")),
        ("after_replace:0", (b"new", b"raw-new")),
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

    def interrupt_cleanup(path: Path) -> None:
        shutil.rmtree(path / "staged")
        shutil.rmtree(path / "backups")
        raise InjectedFailure("cleanup")

    monkeypatch.setattr(recovering, "_remove_commit", interrupt_cleanup)
    with pytest.raises(InjectedFailure, match="cleanup"):
        recovering.recover()

    assert (commit_dir / "ROLLED_BACK").exists()
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


@pytest.mark.parametrize("hostile", ["meta", "journal", "revision"])
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
