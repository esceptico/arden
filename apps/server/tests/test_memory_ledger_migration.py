from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from ntrp.memory.file_store import FilePageStore
from ntrp.memory.journal import VaultJournal
from ntrp.memory.migrate_ledger_v2 import VaultMigrationError, migrate_vault_to_v2, validate_vault
from ntrp.memory.pages import merge_split, parse_page
from ntrp.server.runtime.knowledge import KnowledgeRuntime


def _legacy_page(root: Path, rel: str, *lines: str) -> None:
    page = root / rel
    raw = root / "raw" / rel
    page.parent.mkdir(parents=True, exist_ok=True)
    raw.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(f"# {page.stem.title()}\n", encoding="utf-8")
    raw.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_clean_legacy_vault_is_backed_up_staged_validated_and_committed(tmp_path: Path) -> None:
    legacy = "- 2025-01-03 ^old [fact] [imp:7] (src:curator) Legacy fact"
    _legacy_page(tmp_path, "me.md", legacy)

    report = migrate_vault_to_v2(tmp_path)

    assert report.migrated is True
    assert report.migrated_pages == 1
    assert report.migrated_records == 1
    assert report.backup_path is not None
    backup = Path(report.backup_path)
    assert (backup / "me.md").read_text(encoding="utf-8") == "# Me\n"
    assert (backup / "raw" / "me.md").read_text(encoding="utf-8") == legacy + "\n"
    raw = (tmp_path / "raw" / "me.md").read_text(encoding="utf-8")
    assert raw.startswith("<!-- ntrp:records schema=2 page=me.md -->\n")
    entry = merge_split(parse_page(""), raw).lines[0]
    assert entry.occurred_at == "2025-01-03"
    assert entry.meta.recorded_at == "2025-01-03T00:00:00Z"
    assert entry.meta.time_precision == "day"
    assert validate_vault(tmp_path).healthy
    assert not (tmp_path / ".ntrp" / "maintenance" / "migration-v2").exists()


def test_legacy_record_without_a_known_time_migrates_with_unknown_precision(tmp_path: Path) -> None:
    _legacy_page(tmp_path, "me.md", "- unknown ^old [fact] (src:curator) Timeless fact")

    migrate_vault_to_v2(tmp_path)

    raw = (tmp_path / "raw" / "me.md").read_text(encoding="utf-8")
    entry = merge_split(parse_page(""), raw).lines[0]
    assert entry.occurred_at is None
    assert entry.meta.time_precision == "unknown"
    assert entry.meta.sources[0].occurred_at is None
    assert entry.meta.sources[0].time_precision == "unknown"


def test_identical_duplicate_ids_collapse_but_conflicts_receive_stable_new_ids(tmp_path: Path) -> None:
    same = "- 2025-01-03 ^same [fact] (src:user) Same fact"
    _legacy_page(tmp_path, "me.md", same, "- 2025-01-04 ^conflict [fact] (src:user) First")
    _legacy_page(tmp_path, "topics/x.md", same, "- 2025-01-04 ^conflict [fact] (src:user) Second")

    report = migrate_vault_to_v2(tmp_path)

    entries = [
        line
        for rel in ("me.md", "topics/x.md")
        for line in merge_split(
            parse_page(""), (tmp_path / "raw" / rel).read_text(encoding="utf-8")
        ).lines
    ]
    ids = [entry.id for entry in entries]
    assert ids.count("same") == 1
    assert ids.count("conflict") == 1
    assert len(ids) == len(set(ids)) == 3
    assert report.collapsed_duplicates == 1
    assert report.reassigned_duplicates == 1


def test_staging_validates_legacy_changes_against_existing_v2_pages(tmp_path: Path) -> None:
    _legacy_page(tmp_path, "me.md", "- 2025-01-03 ^shared [fact] (src:user) Legacy")
    visible = tmp_path / "topics" / "v2.md"
    raw = tmp_path / "raw" / "topics" / "v2.md"
    visible.parent.mkdir(parents=True)
    raw.parent.mkdir(parents=True)
    visible.write_text("# V2\n", encoding="utf-8")
    raw.write_text(
        "<!-- ntrp:records schema=2 page=topics/v2.md -->\n"
        "- 2026-07-12T10:23:41Z ^shared [fact] Existing.\n"
        '  <!-- ntrp:meta {"recorded_at":"2026-07-12T10:23:42Z","sequence":1,'
        '"time_precision":"second","scope":{"kind":"user"},'
        '"sources":[{"kind":"chat","ref":"s:m"}]} -->\n',
        encoding="utf-8",
    )
    before = (tmp_path / "raw" / "me.md").read_bytes()

    with pytest.raises(VaultMigrationError, match="duplicate"):
        migrate_vault_to_v2(tmp_path)

    assert (tmp_path / "raw" / "me.md").read_bytes() == before
    assert not (tmp_path / ".ntrp" / "backups").exists()


def test_malformed_legacy_line_fails_closed_with_exact_path_and_reason(tmp_path: Path) -> None:
    _legacy_page(tmp_path, "me.md", "- definitely not a memory record")
    before = (tmp_path / "raw" / "me.md").read_bytes()

    with pytest.raises(VaultMigrationError) as error:
        migrate_vault_to_v2(tmp_path)

    assert error.value.path == Path("raw/me.md")
    assert error.value.reason == "line 1: invalid legacy ledger line"
    assert str(error.value) == "raw/me.md: line 1: invalid legacy ledger line"
    assert (tmp_path / "raw" / "me.md").read_bytes() == before
    assert not (tmp_path / ".ntrp" / "backups").exists()


def test_interrupted_staging_is_discarded_before_a_safe_rerun(tmp_path: Path) -> None:
    _legacy_page(tmp_path, "me.md", "- 2025-01-03 ^old [fact] (src:user) Fact")
    stale = tmp_path / ".ntrp" / "maintenance" / "migration-v2" / "stale-run"
    stale.mkdir(parents=True)
    (stale / "partial").write_text("partial", encoding="utf-8")

    report = migrate_vault_to_v2(tmp_path)

    assert report.migrated
    assert not stale.exists()
    assert validate_vault(tmp_path).interrupted_journals == ()


def test_commit_failure_leaves_original_bytes_untouched_and_backup_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _legacy_page(tmp_path, "me.md", "- 2025-01-03 ^old [fact] (src:user) Fact")
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*.md")}

    def fail_commit(self: VaultJournal, files) -> str:
        backups = list((tmp_path / ".ntrp" / "backups").iterdir())
        assert len(backups) == 1
        assert (backups[0] / "raw" / "me.md").read_bytes() == before[Path("raw/me.md")]
        raise RuntimeError("injected canonical write failure")

    monkeypatch.setattr(VaultJournal, "commit", fail_commit)
    with pytest.raises(VaultMigrationError, match="journal commit: injected canonical write failure"):
        migrate_vault_to_v2(tmp_path)

    assert {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*.md") if ".ntrp" not in path.parts} == before


def test_second_run_is_idempotent_and_creates_no_second_backup(tmp_path: Path) -> None:
    _legacy_page(tmp_path, "me.md", "- 2025-01-03 ^old [fact] (src:user) Fact")
    first = migrate_vault_to_v2(tmp_path)
    raw_before = (tmp_path / "raw" / "me.md").read_bytes()

    second = migrate_vault_to_v2(tmp_path)

    assert second.migrated is False
    assert second.backup_path == first.backup_path
    assert (tmp_path / "raw" / "me.md").read_bytes() == raw_before
    assert len(list((tmp_path / ".ntrp" / "backups").iterdir())) == 1


@pytest.mark.asyncio
async def test_runtime_recovers_migrates_validates_before_constructing_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ntrp.memory.file_store as file_store_module
    import ntrp.memory.migrate_ledger_v2 as migration_module

    events: list[str] = []

    class FakeJournal:
        def __init__(self, root: Path):
            pass

        def recover(self) -> tuple[str, ...]:
            events.append("recover")
            return ()

    class FakeStore:
        def __init__(self, **kwargs):
            events.append("store:init")

        def attach_scorer(self, scorer) -> None:
            pass

        async def open(self) -> None:
            events.append("store:open")

        async def count_active(self) -> int:
            return 1

    monkeypatch.setattr("ntrp.memory.journal.VaultJournal", FakeJournal)
    monkeypatch.setattr(
        migration_module,
        "migrate_vault_to_v2",
        lambda root: events.append("migrate") or SimpleNamespace(migrated=False),
    )
    monkeypatch.setattr(
        migration_module,
        "validate_vault",
        lambda root: events.append("validate") or SimpleNamespace(healthy=True, first_error=None),
    )
    monkeypatch.setattr(file_store_module, "FilePageStore", FakeStore)
    config = SimpleNamespace(
        memory=True,
        memory_model=None,
        memory_artifacts_dir=tmp_path / "memory",
        memory_db_path=tmp_path / "memory.db",
    )
    runtime = KnowledgeRuntime.__new__(KnowledgeRuntime)
    runtime.config = config
    runtime.search_index = None
    runtime._record_store = None
    runtime._consolidate = None
    runtime.memory_curator = None
    runtime._artifact_refresh_task = None

    await runtime._init_memory(SimpleNamespace(sessions=None))

    assert events[:5] == ["recover", "migrate", "validate", "store:init", "store:open"]


@pytest.mark.asyncio
async def test_migrated_page_move_preserves_identity_scope_evidence_and_active_state(tmp_path: Path) -> None:
    visible = tmp_path / "topics" / "area.md"
    raw = tmp_path / "raw" / "topics" / "area.md"
    visible.parent.mkdir(parents=True)
    raw.parent.mkdir(parents=True)
    visible.write_text("---\ntitle: Area\nscope_key: a1\n---\n# Area\n", encoding="utf-8")
    raw.write_text("- 2025-01-03 ^fact [fact] (src:chat) Area fact\n", encoding="utf-8")
    migrate_vault_to_v2(tmp_path)
    (tmp_path / "notes").mkdir()
    (tmp_path / "raw" / "notes").mkdir()
    visible.rename(tmp_path / "notes" / "area.md")
    raw.rename(tmp_path / "raw" / "notes" / "area.md")

    store = FilePageStore(tmp_path)
    await store.open()
    record = await store.get("fact")

    assert record is not None
    assert record.id == "fact"
    assert (record.scope_kind, record.scope_key) == ("area", "a1")
    assert [(source.kind, source.ref) for source in record.sources] == [("chat", "fact")]
    assert [item.id for item in await store.list()] == ["fact"]
    await store.close()
