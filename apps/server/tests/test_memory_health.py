from __future__ import annotations

from typing import TYPE_CHECKING

from arden.memory.health import initialize_empty_vault, validate_vault
from arden.memory.models import now_iso, source_time

if TYPE_CHECKING:
    from pathlib import Path


def _write_raw(root: Path, rel: str, body: str) -> None:
    path = root / "raw" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_canonical_now_uses_exact_millisecond_precision() -> None:
    value = now_iso()

    assert len(value.split(".", 1)[1].split("+", 1)[0]) == 3


def test_source_time_derives_canonical_precision() -> None:
    assert source_time(None) == (None, "unknown")
    assert source_time("2026-07-12") == ("2026-07-12", "day")
    assert source_time("2026-07-12T10:00:00Z") == ("2026-07-12T10:00:00+00:00", "second")
    assert source_time("2026-07-12T10:00:00.1Z") == ("2026-07-12T10:00:00.100+00:00", "millisecond")
    assert source_time("2026-07-12T10:00:00.123456+00:00") == (
        "2026-07-12T10:00:00.123+00:00",
        "millisecond",
    )


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
        '  <!-- arden:meta {"recorded_at":"2026-07-12T10:23:42Z","sequence":1,'
        f'"time_precision":"{precision}","scope":{scope},"sources":{sources}{extra}}} -->\n'
    )


def test_health_reports_every_ledger_safety_field(tmp_path: Path) -> None:
    header = "<!-- arden:records schema=2 page={} -->\n"
    _write_raw(
        tmp_path,
        "me.md",
        header.format("me.md") + _entry("dup", precision="day", sources="[]", extra=',"supersedes":["missing"]'),
    )
    _write_raw(
        tmp_path,
        "topics/x.md",
        header.format("topics/x.md") + _entry("dup", scope='{"kind":"bogus"}'),
    )
    _write_raw(
        tmp_path,
        "topics/bad.md",
        header.format("topics/bad.md") + "- 2026-07-12T10:23:41Z ^bad [fact] Bad.\n  <!-- arden:meta {not-json} -->\n",
    )
    (tmp_path / ".arden" / "journal" / "interrupted").mkdir(parents=True)

    health = validate_vault(tmp_path)

    assert health.schema_version == 2
    assert health.duplicate_ids == ("dup",)
    assert health.invalid_relationship_targets == ("raw/me.md: dup -> missing",)
    assert health.malformed_metadata == ("raw/topics/bad.md: record bad: invalid schema-v2 metadata JSON",)
    assert health.missing_evidence == ("raw/me.md: dup",)
    assert health.invalid_scope == ("raw/topics/x.md: dup: bogus",)
    assert health.timestamp_precision_violations == ("raw/me.md: dup: day precision requires a date-only occurred_at",)
    assert health.interrupted_journals == (".arden/journal/interrupted",)
    assert health.healthy is False


def test_unknown_occurrence_requires_unknown_precision(tmp_path: Path) -> None:
    _write_raw(
        tmp_path,
        "me.md",
        "<!-- arden:records schema=2 page=me.md -->\n" + _entry("unknown", occurred="unknown", precision="day"),
    )

    health = validate_vault(tmp_path)

    assert health.timestamp_precision_violations == (
        "raw/me.md: unknown: absent occurred_at requires unknown precision",
    )


def test_valid_unknown_occurrence_is_healthy(tmp_path: Path) -> None:
    _write_raw(
        tmp_path,
        "me.md",
        "<!-- arden:records schema=2 page=me.md -->\n" + _entry("unknown", occurred="unknown", precision="unknown"),
    )

    assert validate_vault(tmp_path).healthy


def test_missing_evidence_blocks_an_otherwise_valid_vault(tmp_path: Path) -> None:
    _write_raw(
        tmp_path,
        "me.md",
        "<!-- arden:records schema=2 page=me.md -->\n" + _entry("one", sources="[]"),
    )

    health = validate_vault(tmp_path)

    assert health.missing_evidence == ("raw/me.md: one",)
    assert not health.healthy


def test_health_blocks_future_schema_and_invalid_scope_keys(tmp_path: Path) -> None:
    _write_raw(tmp_path, "future.md", "<!-- arden:records schema=3 page=future.md -->\n")
    assert not validate_vault(tmp_path).healthy

    _write_raw(
        tmp_path,
        "future.md",
        "<!-- arden:records schema=2 page=future.md -->\n" + _entry("area", scope='{"kind":"area"}'),
    )
    health = validate_vault(tmp_path)
    assert health.invalid_scope == ("raw/future.md: area: area requires a scope key",)


def test_health_rejects_symlinked_raw_root(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "raw").symlink_to(outside, target_is_directory=True)
    assert not validate_vault(tmp_path).healthy


def test_health_preserves_mixed_schema_versions(tmp_path: Path) -> None:
    _write_raw(tmp_path, "two.md", "<!-- arden:records schema=2 page=two.md -->\n")
    _write_raw(tmp_path, "three.md", "<!-- arden:records schema=3 page=three.md -->\n")
    health = validate_vault(tmp_path)
    assert health.schema_versions == (2, 3)
    assert not health.healthy
    assert health.first_error == "unsupported schema versions: [2, 3]"


def test_empty_vault_initializes_without_migration_state(tmp_path: Path) -> None:
    initialize_empty_vault(tmp_path)

    assert validate_vault(tmp_path).healthy
    assert (tmp_path / "raw").is_dir()
    assert (tmp_path / ".arden").is_dir()
    assert not (tmp_path / ".arden/maintenance/migration-v2.json").exists()
