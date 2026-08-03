"""UI rendering hints for tools — the source of truth for how the desktop app
draws a tool call (icon + grouping unit). Kept here, next to the tool/agent
types, so the knowledge lives with the tools rather than being re-guessed in the
frontend. Values are semantic, library-agnostic keys (`"file"`, `"mail"`, …);
the client maps them to its own icon set.

These are additive presentation hints only — they never affect tool behavior or
the stream's output shape.
"""

# Exact tool name → (icon, grouping noun). `noun` is the singular unit used for
# collapsed runs ("Read 4 files"); None means a run shows "{label} · N".
# A noun is only valid when one CALL is one unit (read_file → one file). Search
# tools stay None: their would-be noun counts results, so a collapsed run of N
# calls would miscount ("Searched 4 emails") or stutter ("Searched 4 searches").
_BY_NAME: dict[str, tuple[str, str | None]] = {
    # System / files
    "file_read": ("file", "file"),
    "file_write": ("file-plus", "file"),
    "file_edit": ("edit", "file"),
    "file_list": ("folder", None),
    "file_find": ("search", None),
    "file_search_text": ("search", None),
    "bash": ("terminal", "command"),
    "current_time": ("clock", None),
    "render_html": ("image", None),
    "load_tools": ("wrench", None),
    "tool_search": ("search", None),
    "notify": ("bell", None),
    "todo_update": ("list", None),
    # Web
    "web_search": ("globe", None),
    "web_fetch": ("globe", "page"),
    # Gmail
    "email_search": ("mail", None),
    "email_read": ("mail", "email"),
    "email_send": ("mail", None),
    # Slack
    "slack_search": ("slack", None),
    "slack_channel": ("slack", None),
    "slack_channels": ("slack", None),
    "slack_dm": ("slack", None),
    "slack_dms": ("slack", None),
    "slack_thread": ("slack", None),
    "slack_user": ("slack", None),
    "slack_users": ("slack", None),
    "slack_file": ("image", None),
    "slack_post_message": ("slack", None),
    "slack_post_blocks": ("slack", None),
    # Calendar
    "calendar_search": ("calendar", None),
    "calendar_create_event": ("calendar", None),
    "calendar_edit_event": ("calendar", None),
    "calendar_delete_event": ("calendar", None),
    # Google Drive
    "drive_search": ("search", None),
    "drive_read_doc": ("file", "document"),
    "drive_read_sheet": ("table", "spreadsheet"),
    "drive_create_doc": ("file-plus", None),
    "drive_edit_doc": ("edit", None),
    "drive_create_sheet": ("table", None),
    "drive_update_sheet": ("table", None),
    "drive_append_sheet_rows": ("table", None),
    # Sessions
    "session_search_transcripts": ("history", None),
    "session_read": ("history", None),
    "session_list": ("history", None),
}

# Integration source → default icon, for tools without an explicit entry.
_BY_SOURCE: dict[str, str] = {
    "gmail": "mail",
    "slack": "slack",
    "calendar": "calendar",
    "google_drive": "folder",
    "web": "globe",
    "_facts": "brain",
    "_wiki": "brain",
    "_fact_maintenance": "brain",
    "_wiki_maintenance": "brain",
    "_sessions": "history",
}


def tool_presentation(name: str, source: str | None) -> tuple[str | None, str | None]:
    """Return (icon, noun) for a tool. icon may be None (the client falls back
    to its own heuristic / a neutral dot)."""
    if name in _BY_NAME:
        return _BY_NAME[name]
    icon = _BY_SOURCE.get(source) if source else None
    return (icon, None)
