from arden.areas.projection import area_automation_match, parse_open_loops

PROSE = """# O-1A

## What we know
Stuff.

## Open loops
- **Assess case strength** — determine whether evidence is enough (from chat).
- **Find the right counsel** — identify an attorney (from chat).

## Related
- [[United States]]
"""


def test_areas_from_records_projection():
    from arden.areas.models import areas_from_records

    rows = [
        {"area_id": "p1", "name": "Health", "page_path": "topics/health.md", "autonomy": "observe"},
        {"area_id": "p2", "name": "Design", "page_path": None, "autonomy": None},
        {"area_id": "p3", "name": "Reading", "page_path": "topics/reading.md", "autonomy": None},
    ]
    areas = areas_from_records(rows)
    assert [(s.key, s.title, s.autonomy) for s in areas] == [
        ("p1", "Health", "observe"),
        ("p2", "Design", None),  # every durable container is a first-class Area
        ("p3", "Reading", None),
    ]


def test_parse_open_loops_extracts_bullets_until_next_heading():
    loops = parse_open_loops(PROSE)
    assert len(loops) == 2
    assert loops[0].startswith("Assess case strength")
    assert "(from chat)" not in loops[0]  # provenance suffix stripped


def test_parse_open_loops_missing_section_is_empty():
    assert parse_open_loops("# T\n\n## What we know\nx\n") == []


def test_parse_open_loops_indented_heading_terminates_section():
    prose = "# T\n\n## Open loops\n- Loop one.\n  ## Indented heading\n- Not a loop.\n"
    assert parse_open_loops(prose) == ["Loop one."]


def test_area_automation_match_exact_seeded_name():
    assert area_automation_match("area:o-1a", "o-1a")


def test_area_automation_match_colon_suffixed_sub_automation():
    assert area_automation_match("area:o-1a:daily", "o-1a")


def test_area_automation_match_rejects_other_key_prefix_collision():
    assert not area_automation_match("area:o-1a-other", "o-1a")


def test_area_automation_match_rejects_unrelated_name():
    assert not area_automation_match("some-other-automation", "o-1a")


def test_parse_related_extracts_wikilink_slugs():
    prose = "# O-1A\n\n## Open loops\n- Find counsel.\n\n## Related\n- [[United States]] — visa planning context.\n- [[Health]]\n"
    from arden.areas.projection import parse_related

    assert parse_related(prose) == ["united-states", "health"]


def test_parse_related_missing_section_is_empty():
    from arden.areas.projection import parse_related

    assert parse_related("# T\n\n## Open loops\n- x\n") == []
