from __future__ import annotations

from typing import TYPE_CHECKING

from ntrp.memory.artifacts import ArtifactMemoryStore
from ntrp.memory.file_store import FilePageStore
from ntrp.memory.migrate_ledger_v2 import validate_vault

if TYPE_CHECKING:
    from pathlib import Path


def _write_raw(root: Path, rel: str, body: str) -> None:
    path = root / "raw" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _entry(
    record_id: str,
    *,
    occurred: str = "2026-07-12T10:23:41Z",
    precision: str = "second",
    scope: str = '{"kind":"user"}',
    sources: str = '[{"kind":"chat","ref":"s:m"}]',
    extra: str = "",
) -> str:
    return (
        f"- {occurred} ^{record_id} [fact] Fact {record_id}.\n"
        '  <!-- ntrp:meta {"recorded_at":"2026-07-12T10:23:42Z","sequence":1,'
        f'"time_precision":"{precision}","scope":{scope},"sources":{sources}{extra}}} -->\n'
    )


def test_health_reports_every_ledger_safety_field(tmp_path: Path) -> None:
    header = "<!-- ntrp:records schema=2 page={} -->\n"
    _write_raw(
        tmp_path,
        "me.md",
        header.format("me.md")
        + _entry("dup", precision="day", sources="[]", extra=',"supersedes":["missing"]'),
    )
    _write_raw(
        tmp_path,
        "topics/x.md",
        header.format("topics/x.md") + _entry("dup", scope='{"kind":"bogus"}'),
    )
    _write_raw(
        tmp_path,
        "topics/bad.md",
        header.format("topics/bad.md")
        + "- 2026-07-12T10:23:41Z ^bad [fact] Bad.\n  <!-- ntrp:meta {not-json} -->\n",
    )
    (tmp_path / ".ntrp" / "journal" / "interrupted").mkdir(parents=True)
    (tmp_path / ".ntrp" / "maintenance" / "migration-v2" / "partial").mkdir(parents=True)

    health = validate_vault(tmp_path)

    assert health.schema_version == 2
    assert health.last_migration is None
    assert health.backup_path is None
    assert health.duplicate_ids == ("dup",)
    assert health.invalid_relationship_targets == ("raw/me.md: dup -> missing",)
    assert health.malformed_metadata == ("raw/topics/bad.md: record bad: invalid schema-v2 metadata JSON",)
    assert health.missing_evidence == ("raw/me.md: dup",)
    assert health.invalid_scope == ("raw/topics/x.md: dup: bogus",)
    assert health.timestamp_precision_violations == (
        "raw/me.md: dup: day precision requires a date-only occurred_at",
    )
    assert health.interrupted_journals == (
        ".ntrp/journal/interrupted",
        ".ntrp/maintenance/migration-v2/partial",
    )
    assert health.healthy is False


def test_unknown_occurrence_requires_unknown_precision(tmp_path: Path) -> None:
    _write_raw(
        tmp_path,
        "me.md",
        "<!-- ntrp:records schema=2 page=me.md -->\n"
        + _entry("unknown", occurred="unknown", precision="day"),
    )

    health = validate_vault(tmp_path)

    assert health.timestamp_precision_violations == (
        "raw/me.md: unknown: absent occurred_at requires unknown precision",
    )


def test_valid_unknown_occurrence_is_healthy(tmp_path: Path) -> None:
    _write_raw(
        tmp_path,
        "me.md",
        "<!-- ntrp:records schema=2 page=me.md -->\n"
        + _entry("unknown", occurred="unknown", precision="unknown"),
    )

    assert validate_vault(tmp_path).healthy


def test_artifact_store_exposes_exact_vault_health(tmp_path: Path) -> None:
    _write_raw(
        tmp_path,
        "me.md",
        "<!-- ntrp:records schema=2 page=me.md -->\n" + _entry("one"),
    )

    assert ArtifactMemoryStore(tmp_path).vault_health() == validate_vault(tmp_path)


def test_file_store_exposes_invalid_v2_health_without_serving_reads(tmp_path: Path) -> None:
    (tmp_path / "me.md").write_text("# Me\n", encoding="utf-8")
    _write_raw(
        tmp_path,
        "me.md",
        "<!-- ntrp:records schema=2 page=me.md -->\n"
        "- 2026-07-12T10:23:41Z ^bad [fact] Bad.\n",
    )

    health = FilePageStore(tmp_path).vault_health()

    assert health.malformed_metadata == (
        "raw/me.md: record bad: schema-v2 record is missing its metadata comment",
    )


def test_missing_evidence_blocks_an_otherwise_valid_vault(tmp_path: Path) -> None:
    _write_raw(
        tmp_path,
        "me.md",
        "<!-- ntrp:records schema=2 page=me.md -->\n" + _entry("one", sources="[]"),
    )

    health = validate_vault(tmp_path)

    assert health.missing_evidence == ("raw/me.md: one",)
    assert not health.healthy


def test_health_blocks_future_schema_and_invalid_scope_keys(tmp_path: Path) -> None:
    _write_raw(tmp_path, "future.md", "<!-- ntrp:records schema=3 page=future.md -->\n")
    assert not validate_vault(tmp_path).healthy

    _write_raw(tmp_path, "future.md", "<!-- ntrp:records schema=2 page=future.md -->\n" + _entry("area", scope='{"kind":"area"}'))
    health = validate_vault(tmp_path)
    assert health.invalid_scope == ("raw/future.md: area: area requires a scope key",)


def test_health_rejects_symlinked_raw_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "raw").symlink_to(outside, target_is_directory=True)
    assert not validate_vault(tmp_path).healthy
