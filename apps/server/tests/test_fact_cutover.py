import json
from datetime import UTC, datetime

import pytest

from arden.memory.facts.cutover import (
    LEDGER_DIRECTORY,
    MARKER_NAME,
    FactCutoverError,
    fact_cutover_content,
    load_fact_cutover,
)

MIGRATED_AT = datetime(2026, 7, 28, 12, 30, tzinfo=UTC)


def test_absent_fact_cutover_keeps_legacy_mode(tmp_path) -> None:
    assert load_fact_cutover(tmp_path) is None


def test_canonical_fact_cutover_loads_fixed_ledger(tmp_path) -> None:
    (tmp_path / MARKER_NAME).write_bytes(fact_cutover_content(MIGRATED_AT))

    cutover = load_fact_cutover(tmp_path)

    assert cutover is not None
    assert cutover.migrated_at == MIGRATED_AT
    assert LEDGER_DIRECTORY == "facts"


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"schema_version": 2, "mode": "fact-ledger", "ledger_root": "facts", "migrated_at": "2026-07-28T12:30:00Z"},
        {
            "schema_version": 1,
            "mode": "legacy",
            "ledger_root": "facts",
            "migrated_at": "2026-07-28T12:30:00Z",
        },
        {
            "schema_version": 1,
            "mode": "fact-ledger",
            "ledger_root": "../facts",
            "migrated_at": "2026-07-28T12:30:00Z",
        },
        {
            "schema_version": 1,
            "mode": "fact-ledger",
            "ledger_root": "facts",
            "migrated_at": "2026-07-28T12:30:00+04:00",
        },
    ],
)
def test_present_invalid_cutover_never_falls_back(tmp_path, value) -> None:
    (tmp_path / MARKER_NAME).write_text(json.dumps(value), encoding="utf-8")

    with pytest.raises(FactCutoverError):
        load_fact_cutover(tmp_path)


def test_noncanonical_or_duplicate_cutover_fails_closed(tmp_path) -> None:
    marker = tmp_path / MARKER_NAME
    marker.write_text(
        '{"schema_version":1,"schema_version":1,"mode":"fact-ledger",'
        '"ledger_root":"facts","migrated_at":"2026-07-28T12:30:00Z"}\n',
        encoding="utf-8",
    )
    with pytest.raises(FactCutoverError):
        load_fact_cutover(tmp_path)

    marker.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "mode": "fact-ledger",
                "ledger_root": "facts",
                "migrated_at": "2026-07-28T12:30:00Z",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    with pytest.raises(FactCutoverError, match="canonical"):
        load_fact_cutover(tmp_path)


def test_symlink_cutover_fails_closed(tmp_path) -> None:
    target = tmp_path / "target.json"
    target.write_bytes(fact_cutover_content(MIGRATED_AT))
    (tmp_path / MARKER_NAME).symlink_to(target)

    with pytest.raises(FactCutoverError, match="regular file"):
        load_fact_cutover(tmp_path)
