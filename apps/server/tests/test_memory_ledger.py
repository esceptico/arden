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
    assert entry.meta.sources[0].captured_at is None


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


def test_v2_preserves_multiple_sources_and_nested_unknown_metadata_without_inventing_capture_time():
    raw = (
        "- 2026-07-12T14:23:41+04:00 ^rec-1 [fact] Evidence survives.\n"
        '  <!-- ntrp:meta {"recorded_at":"2026-07-12T10:23:42Z","sequence":2,'
        '"time_precision":"second","scope":{"kind":"area","key":"a1","future":{"x":1}},'
        '"sources":[{"kind":"chat_message","ref":"s:m1","future":{"rank":1}},'
        '{"kind":"chat_message","ref":"s:m2","captured_at":"2026-07-12T10:23:41Z",'
        '"future":{"rank":2}}]} -->'
    )

    entry = parse_ledger_entry(raw)

    assert len(entry.meta.sources) == 2
    assert entry.meta.scope_extra == {"future": {"x": 1}}
    assert entry.meta.sources[0].captured_at is None
    assert "captured_at" not in entry.meta.sources[0].to_dict()
    assert entry.meta.sources[0].extra == {"future": {"rank": 1}}
    assert entry.meta.sources[1].extra == {"future": {"rank": 2}}
    rendered = render_ledger_entry(entry)
    assert parse_ledger_entry(rendered) == entry


@pytest.mark.parametrize(
    "raw",
    [
        (
            "- 2026-07-12T14:23+04:00 ^rec-1 [fact] Missing seconds.\n"
            '  <!-- ntrp:meta {"recorded_at":"2026-07-12T10:23:42Z","sequence":1,'
            '"time_precision":"minute","scope":{"kind":"user"},"sources":[]} -->'
        ),
        (
            "- 2026-07-12T14:23:41+04:00 ^rec-1 [fact] Missing seconds.\n"
            '  <!-- ntrp:meta {"recorded_at":"2026-07-12T10:23Z","sequence":1,'
            '"time_precision":"second","scope":{"kind":"user"},"sources":[]} -->'
        ),
        (
            "- 2026-07-12T14:23:41+04:00 ^rec-1 [fact] Missing seconds.\n"
            '  <!-- ntrp:meta {"recorded_at":"2026-07-12T10:23:42Z","sequence":1,'
            '"time_precision":"second","scope":{"kind":"user"},"sources":['
            '{"kind":"chat_message","ref":"s:m","occurred_at":"2026-07-12T10:23Z"}]} -->'
        ),
        (
            "- 2026-07-12T14:23:41+04:00 ^rec-1 [fact] Missing seconds.\n"
            '  <!-- ntrp:meta {"recorded_at":"2026-07-12T10:23:42Z","sequence":1,'
            '"time_precision":"second","scope":{"kind":"user"},"sources":['
            '{"kind":"chat_message","ref":"s:m","captured_at":"2026-07-12T10:23Z"}]} -->'
        ),
    ],
)
def test_v2_timestamps_require_seconds(raw: str):
    with pytest.raises(ValueError, match="RFC 3339"):
        parse_ledger_entry(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "- 2025-02-30 ^old [fact] Impossible legacy date (src:curator)",
        (
            "- 2025-02-30 ^rec-1 [fact] Impossible v2 date.\n"
            '  <!-- ntrp:meta {"recorded_at":"2026-07-12T10:23:42Z","sequence":1,'
            '"time_precision":"day","scope":{"kind":"user"},"sources":[]} -->'
        ),
    ],
)
def test_ledger_rejects_invalid_calendar_dates(raw: str):
    with pytest.raises(ValueError, match="RFC 3339"):
        parse_ledger_entry(raw)


def test_schema_v2_header_must_be_the_first_nonempty_raw_line():
    raw = (
        "unknown preamble\n"
        "<!-- ntrp:records schema=2 page=topics/preferences.md -->\n"
        "- 2026-07-12T14:23:41+04:00 ^rec-1 [fact] Must not disappear.\n"
        '  <!-- ntrp:meta {"recorded_at":"2026-07-12T10:23:42Z","sequence":1,'
        '"time_precision":"second","scope":{"kind":"user"},"sources":[]} -->\n'
    )

    with pytest.raises(ValueError, match="first nonempty"):
        merge_split(parse_page(""), raw)
