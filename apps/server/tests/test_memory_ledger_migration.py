from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from ntrp.memory.file_store import FilePageStore
from ntrp.memory.journal import VaultJournal
from ntrp.memory.ledger import LedgerEntry, LedgerMeta, render_ledger_entry
from ntrp.memory.migrate_ledger_v2 import VaultMigrationError, migrate_vault_to_v2, validate_vault
from ntrp.memory.models import Kind, SourceRef
from ntrp.memory.pages import SENTINEL, merge_split, parse_page
from ntrp.memory.records import RecordStore
from ntrp.server.runtime.knowledge import KnowledgeRuntime


def _legacy_page(root: Path, rel: str, *lines: str) -> None:
    page = root / rel
    raw = root / "raw" / rel
    page.parent.mkdir(parents=True, exist_ok=True)
    raw.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(f"# {page.stem.title()}\n", encoding="utf-8")
    raw.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _v2_entry(record_id: str, *, scope: str, key: str, supersedes=(), sources=()) -> LedgerEntry:
    return LedgerEntry(
        id=record_id, text=record_id, kind=Kind.FACT, occurred_at="2026-07-12T10:00:00Z",
        meta=LedgerMeta(recorded_at="2026-07-12T10:00:01Z", sequence=1, time_precision="second",
                        scope_kind=scope, scope_key=key, sources=tuple(sources), supersedes=tuple(supersedes)),
    )


def _v2_page(root: Path, rel: str, entries: list[LedgerEntry], *, cites: list[str] | None = None) -> None:
    page = root / rel
    raw = root / "raw" / rel
    page.parent.mkdir(parents=True, exist_ok=True)
    raw.parent.mkdir(parents=True, exist_ok=True)
    page.write_text(f"# {page.stem}\n", encoding="utf-8")
    fm = f"---\nprose_cites: {json.dumps(cites)}\n---\n\n" if cites is not None else ""
    raw.write_text(fm + f"<!-- ntrp:records schema=2 page={rel} -->\n" + "\n".join(render_ledger_entry(e) for e in entries) + "\n", encoding="utf-8")


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
    assert entry.meta.recorded_at.startswith("2026-07-12T")
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


def test_global_remap_resolves_legacy_conflict_with_existing_v2_page(tmp_path: Path) -> None:
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
    report = migrate_vault_to_v2(tmp_path)

    health = validate_vault(tmp_path)
    assert health.healthy
    assert report.reassigned_duplicates == 1
    ids = {
        entry.id
        for rel in ("me.md", "topics/v2.md")
        for entry in merge_split(parse_page(""), (tmp_path / "raw" / rel).read_text(encoding="utf-8")).lines
    }
    assert len(ids) == 2 and "shared" in ids


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

    monkeypatch.setattr(VaultJournal, "commit_migration", fail_commit)
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
            self.canonical_revision = "revision-1"

        def attach_scorer(self, scorer) -> None:
            pass

        async def open(self) -> None:
            events.append("store:open")

        async def count_active(self) -> int:
            return 1

        def _ledger_entries(self):
            return ()

        def commit_generated_projection(self, rel, content, expected, expected_revision) -> None:
            raise AssertionError("empty daily projection must not write")

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
        memory_timezone="Asia/Yerevan",
    )
    runtime = KnowledgeRuntime.__new__(KnowledgeRuntime)
    runtime.config = config
    runtime.search_index = None
    runtime._record_store = None
    runtime._consolidate = None
    runtime.memory_curator = None
    runtime._artifact_refresh_task = None

    await runtime._init_memory(SimpleNamespace(sessions=None))
    await runtime._vault_index.wait_idle()
    await runtime._link_index.wait_idle()

    assert events[:5] == ["recover", "migrate", "validate", "store:init", "store:open"]
    await runtime._vault_index.close()
    await runtime._link_index.close()
    await runtime._daily_projection.close()


@pytest.mark.asyncio
async def test_migrated_page_move_preserves_identity_scope_evidence_and_active_state(tmp_path: Path) -> None:
    visible = tmp_path / "topics" / "area.md"
    raw = tmp_path / "raw" / "topics" / "area.md"
    visible.parent.mkdir(parents=True)
    raw.parent.mkdir(parents=True)
    visible.write_text("---\ntitle: Area\n---\n# Area\n", encoding="utf-8")
    raw.write_text("---\nscope_key: a1\n---\n- 2025-01-03 ^fact [fact] (src:chat) Area fact\n", encoding="utf-8")
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


def test_raw_frontmatter_wins_and_survives_migration(tmp_path: Path) -> None:
    (tmp_path / "topics").mkdir()
    (tmp_path / "raw" / "topics").mkdir(parents=True)
    (tmp_path / "topics/area.md").write_text("---\ntitle: Area\nscope_key: wrong\n---\n# Area\n", encoding="utf-8")
    (tmp_path / "raw/topics/area.md").write_text(
        '---\nscope_key: a1\nentity_labels: ["Area"]\nmeta_labels: ["important"]\n---\n'
        "- 2025-01-03 ^fact [fact] [pin] (src:chat) Area fact\n",
        encoding="utf-8",
    )

    migrate_vault_to_v2(tmp_path)
    raw = (tmp_path / "raw/topics/area.md").read_text(encoding="utf-8")
    page = merge_split(parse_page(""), raw)

    assert page.frontmatter["scope_key"] == "a1"
    assert page.frontmatter["entity_labels"] == ["Area"]
    assert page.frontmatter["meta_labels"] == ["important"]
    assert page.lines[0].pinned
    assert page.lines[0].meta.scope_key == "a1"


def test_v2_raw_and_legacy_visible_timeline_for_same_page_are_unioned(tmp_path: Path) -> None:
    (tmp_path / "raw").mkdir()
    (tmp_path / "me.md").write_text(
        "# Me\n\n" + SENTINEL + "\n- 2025-01-03 ^legacy [fact] (src:user) Legacy\n",
        encoding="utf-8",
    )
    (tmp_path / "raw/me.md").write_text(
        "<!-- ntrp:records schema=2 page=me.md -->\n"
        "- 2026-07-12T10:23:41Z ^existing [fact] Existing.\n"
        '  <!-- ntrp:meta {"recorded_at":"2026-07-12T10:23:42Z","sequence":9,'
        '"time_precision":"second","scope":{"kind":"user"},'
        '"sources":[{"kind":"chat","ref":"s:m"}]} -->\n', encoding="utf-8")

    migrate_vault_to_v2(tmp_path)
    page = merge_split(parse_page(""), (tmp_path / "raw/me.md").read_text(encoding="utf-8"))

    assert {entry.id for entry in page.lines} == {"existing", "legacy"}


@pytest.mark.asyncio
async def test_runtime_converts_nonempty_sqlite_import_to_healthy_v2_before_ready(tmp_path: Path) -> None:
    db = tmp_path / "memory.db"
    legacy = RecordStore(db)
    await legacy.open()
    await legacy.add("Imported fact", source_ref=None)
    await legacy.close()
    config = SimpleNamespace(
        memory=True,
        memory_model=None,
        memory_artifacts_dir=tmp_path / "vault",
        memory_db_path=db,
        memory_timezone="Asia/Yerevan",
    )
    runtime = KnowledgeRuntime.__new__(KnowledgeRuntime)
    runtime.config = config
    runtime.search_index = None
    runtime._record_store = None
    runtime._consolidate = None
    runtime.memory_curator = None
    runtime._artifact_refresh_task = None

    await runtime._init_memory(SimpleNamespace(sessions=None))

    health = validate_vault(config.memory_artifacts_dir)
    assert health.schema_version == 2
    assert health.healthy
    assert await runtime._record_store.count_active() == 1
    await runtime._vault_index.close()
    await runtime._link_index.close()
    await runtime._daily_projection.close()
    await runtime._record_store.close()


@pytest.mark.parametrize("name", ["maintenance", "backups"])
def test_internal_parent_symlink_never_writes_or_deletes_outside(tmp_path: Path, name: str) -> None:
    _legacy_page(tmp_path, "me.md", "- 2025-01-03 ^old [fact] (src:user) Fact")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("keep", encoding="utf-8")
    meta = tmp_path / ".ntrp"
    meta.mkdir()
    (meta / name).symlink_to(outside, target_is_directory=True)
    before = (tmp_path / "raw/me.md").read_bytes()

    with pytest.raises(VaultMigrationError, match="symlink"):
        migrate_vault_to_v2(tmp_path)

    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert (tmp_path / "raw/me.md").read_bytes() == before


def test_metadata_and_canonical_bytes_rollback_together(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _legacy_page(tmp_path, "me.md", "- 2025-01-03 ^old [fact] (src:user) Fact")
    prior = tmp_path / ".ntrp/maintenance/migration-v2.json"
    prior.parent.mkdir(parents=True)
    prior.write_text('{"schema_version":1}\n', encoding="utf-8")
    before_raw = (tmp_path / "raw/me.md").read_bytes()
    def fail(point: str) -> None:
        if point == "after_replace:0":
            raise RuntimeError("metadata crash")

    monkeypatch.setattr(VaultJournal, "_checkpoint", staticmethod(fail))
    with pytest.raises(VaultMigrationError, match="metadata crash"):
        migrate_vault_to_v2(tmp_path)

    assert prior.read_text(encoding="utf-8") == '{"schema_version":1}\n'
    assert (tmp_path / "raw/me.md").read_bytes() == before_raw


def test_invalid_utf8_reports_exact_raw_path(tmp_path: Path) -> None:
    (tmp_path / "raw").mkdir()
    (tmp_path / "raw/foo.md").write_bytes(b"\xff")
    with pytest.raises(VaultMigrationError) as error:
        migrate_vault_to_v2(tmp_path)
    assert error.value.path == Path("raw/foo.md")


def test_empty_visible_sentinel_with_v2_raw_is_idempotently_preserved(tmp_path: Path) -> None:
    (tmp_path / "raw").mkdir()
    (tmp_path / "me.md").write_text("# Me\n\n" + SENTINEL + "\n", encoding="utf-8")
    (tmp_path / "raw/me.md").write_text("<!-- ntrp:records schema=2 page=me.md -->\n", encoding="utf-8")
    before = (tmp_path / "me.md").read_bytes()
    assert not migrate_vault_to_v2(tmp_path).migrated
    assert not migrate_vault_to_v2(tmp_path).migrated
    assert (tmp_path / "me.md").read_bytes() == before


def test_occurrence_resolver_remaps_cross_page_scope_refs_without_redirecting_canonical_refs(tmp_path: Path) -> None:
    _legacy_page(tmp_path, "me.md", "- 2025-01-03 ^trigger [fact] (src:user) Trigger")
    canonical = _v2_entry("shared", scope="area", key="a1", sources=(SourceRef("chat", "m1"),))
    conflict = _v2_entry("shared", scope="area", key="b1", sources=(SourceRef("chat", "m2"),))
    canonical_ref = _v2_entry("keep-ref", scope="area", key="a1", supersedes=("shared",), sources=(SourceRef("record", "shared"),))
    scoped_ref = _v2_entry("move-ref", scope="area", key="b1", supersedes=("shared",), sources=(SourceRef("record", "shared"),))
    _v2_page(tmp_path, "topics/a.md", [canonical, canonical_ref])
    _v2_page(tmp_path, "topics/b.md", [conflict], cites=["shared"])
    _v2_page(tmp_path, "topics/c.md", [scoped_ref])

    migrate_vault_to_v2(tmp_path)
    pages = {
        rel: merge_split(parse_page(""), (tmp_path / "raw" / rel).read_text(encoding="utf-8"))
        for rel in ("topics/a.md", "topics/b.md", "topics/c.md")
    }
    remapped = pages["topics/b.md"].lines[0].id
    keep = next(e for e in pages["topics/a.md"].lines if e.id == "keep-ref")
    moved = pages["topics/c.md"].lines[0]
    assert remapped != "shared"
    assert keep.meta.supersedes == ("shared",) and keep.meta.sources[0].ref == "shared"
    assert moved.meta.supersedes == (remapped,) and moved.meta.sources[0].ref == remapped
    assert pages["topics/b.md"].frontmatter["prose_cites"] == [remapped]


def test_external_source_ref_collision_is_not_remapped(tmp_path: Path) -> None:
    _legacy_page(tmp_path, "me.md", "- 2025-01-03 ^trigger [fact] (src:user) Trigger")
    first = _v2_entry("same", scope="area", key="a1", sources=(SourceRef("chat", "same"),))
    second = _v2_entry("same", scope="area", key="a2", sources=(SourceRef("session", "same"),))
    _v2_page(tmp_path, "topics/a.md", [first])
    _v2_page(tmp_path, "topics/b.md", [second])
    migrate_vault_to_v2(tmp_path)
    migrated = merge_split(parse_page(""), (tmp_path / "raw/topics/b.md").read_text(encoding="utf-8")).lines[0]
    assert migrated.id != "same"
    assert migrated.meta.sources[0].ref == "same"


def test_conflicting_same_page_self_sources_follow_their_own_occurrences(tmp_path: Path) -> None:
    _legacy_page(tmp_path, "me.md", "- 2025-01-03 ^trigger [fact] (src:user) Trigger")
    one = _v2_entry("same", scope="area", key="a1", sources=(SourceRef("memory_record", "same"),))
    two = replace(one, text="different", meta=replace(one.meta, sequence=2))
    _v2_page(tmp_path, "topics/a.md", [one, two])
    migrate_vault_to_v2(tmp_path)
    entries = merge_split(parse_page(""), (tmp_path / "raw/topics/a.md").read_text(encoding="utf-8")).lines
    assert len(entries) == 2 and entries[0].id != entries[1].id
    assert [entry.meta.sources[0].ref for entry in entries] == [entry.id for entry in entries]


def test_explicit_path_record_references_normalize_to_bare_resolved_id(tmp_path: Path) -> None:
    _legacy_page(tmp_path, "me.md", "- 2025-01-03 ^trigger [fact] (src:user) Trigger")
    target = _v2_entry("target", scope="area", key="a1", sources=(SourceRef("chat", "m"),))
    ref = _v2_entry(
        "ref", scope="area", key="a1", supersedes=("topics/a.md#^target",),
        sources=(SourceRef("record", "raw/topics/a.md#^target"),),
    )
    _v2_page(tmp_path, "topics/a.md", [target])
    _v2_page(tmp_path, "topics/ref.md", [ref])
    migrate_vault_to_v2(tmp_path)
    migrated = merge_split(parse_page(""), (tmp_path / "raw/topics/ref.md").read_text(encoding="utf-8")).lines[0]
    assert migrated.meta.supersedes == ("target",)
    assert migrated.meta.sources[0].ref == "target"
