import json

from arden.agent.types.tool_presentation import tool_presentation
from arden.events.sse import ToolCallStartEvent


def test_known_tools_map_to_icon_and_noun():
    assert tool_presentation("read_file", "_system") == ("file", "file")
    assert tool_presentation("read_email", "gmail") == ("mail", "email")
    assert tool_presentation("web_fetch", "web") == ("globe", "page")


def test_search_tools_carry_no_grouping_noun():
    # A search call's unit counts results, not calls — a collapsed run must
    # show "{label} · N", never "Searched 4 searches" / "Searched 4 emails".
    for name, source in [
        ("web_search", "web"),
        ("emails", "gmail"),
        ("slack_search", "slack"),
        ("calendar", "calendar"),
        ("search_transcripts", "_sessions"),
        ("memory_search", "_memory"),
        ("search_text", "_system"),
        ("tool_search", "_system"),
        ("search_google_drive", "google_drive"),
    ]:
        _, noun = tool_presentation(name, source)
        assert noun is None, name


def test_unlisted_tools_fall_back_to_source_icon_then_none():
    # Not in _BY_NAME, but the source gives a category icon.
    assert tool_presentation("slack_reactions", "slack") == ("slack", None)
    assert tool_presentation("some_gmail_tool", "gmail") == ("mail", None)
    # Uncategorized → no icon (client renders a neutral dot).
    assert tool_presentation("frobnicate", "user") == (None, None)
    assert tool_presentation("frobnicate", None) == (None, None)


def test_tool_call_start_event_carries_hints_on_the_wire():
    # The hints must survive asdict() serialization so the desktop app receives
    # them in the TOOL_CALL_START payload.
    event = ToolCallStartEvent(
        tool_call_id="t1",
        tool_call_name="emails",
        display_name="Emails",
        icon="mail",
        noun="email",
        source="gmail",
    )
    data = json.loads(event.to_sse()["data"])
    assert data["icon"] == "mail"
    assert data["noun"] == "email"
    assert data["source"] == "gmail"


def test_hints_default_to_none_when_absent():
    event = ToolCallStartEvent(tool_call_id="t1", tool_call_name="frobnicate")
    data = json.loads(event.to_sse()["data"])
    assert data["icon"] is None
    assert data["noun"] is None
    assert data["source"] is None
