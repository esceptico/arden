import pytest

from ntrp.memory.ledger import parse_ledger_entry, render_ledger_entry
from ntrp.memory.pages import merge_split, parse_page, render_raw


def test_v2_entry_round_trips_all_evidence_and_unknown_metadata():
    raw = (
        "- 2026-07-12T14:23:41.582+04:00 ^rec-1 [fact] [imp:8] Concise replies.\n"
        '  <!-- ntrp:meta {"recorded_at":"2026-07-12T10:23:42.014Z",'
        '"sequence":42,"time_precision":"millisecond","scope":{"kind":"user"},'
        '"sources":[{"kind":"chat_message","ref":"s:m","role":"user",'
        '"occurred_at":"2026-07-12T14:23:41.582+04:00"}],"future":{"x":1}} -->'
    )
    entry = parse_ledger_entry(raw)
    assert entry.meta.sources[0].ref == "s:m"
    assert entry.meta.extra == {"future": {"x": 1}}
    assert parse_ledger_entry(render_ledger_entry(entry)) == entry


def test_date_only_legacy_line_keeps_day_precision():
    entry = parse_ledger_entry("- 2025-01-03 ^old [fact] Legacy fact (src:curator)")
    assert entry.occurred_at == "2025-01-03"
    assert entry.meta.time_precision == "day"


@pytest.mark.parametrize("field", ["id", "text", "kind", "occurred_at", "pinned", "imp", "entity"])
def test_v2_metadata_rejects_duplicate_authoritative_fields(field: str):
    raw = (
        "- 2026-07-12T14:23:41+04:00 ^rec-1 [fact] Concise replies.\n"
        '  <!-- ntrp:meta {"recorded_at":"2026-07-12T10:23:42Z","sequence":1,'
        f'"time_precision":"second","scope":{{"kind":"user"}},"sources":[],"{field}":"duplicate"}} -->'
    )
    with pytest.raises(ValueError, match="authoritative"):
        parse_ledger_entry(raw)


def test_v2_timestamp_requires_an_explicit_rfc3339_offset():
    raw = (
        "- 2026-07-12T14:23:41 ^rec-1 [fact] Concise replies.\n"
        '  <!-- ntrp:meta {"recorded_at":"2026-07-12T10:23:42Z","sequence":1,'
        '"time_precision":"second","scope":{"kind":"user"},"sources":[]} -->'
    )
    with pytest.raises(ValueError, match="RFC 3339"):
        parse_ledger_entry(raw)


def test_schema_v2_raw_page_round_trips_through_page_codec():
    raw = (
        "<!-- ntrp:records schema=2 page=topics/preferences.md -->\n"
        "- 2026-07-12T14:23:41.582+04:00 ^rec-1 [fact] Concise replies.\n"
        '  <!-- ntrp:meta {"recorded_at":"2026-07-12T10:23:42.014Z","sequence":42,'
        '"time_precision":"millisecond","scope":{"kind":"user"},"sources":[],"future":{"x":1}} -->\n'
    )
    page = merge_split(parse_page(""), raw)
    assert render_raw(page) == raw
