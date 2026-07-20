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
_BY_NAME: dict[str, tuple[str, str | None]] = {
    # System / files
    "read_file": ("file", "file"),
    "write_file": ("file-plus", "file"),
    "edit_file": ("edit", "file"),
    "list_files": ("folder", None),
    "find_files": ("search", None),
    "search_text": ("search", "match"),
    "bash": ("terminal", "command"),
    "current_time": ("clock", None),
    "render_html": ("image", None),
    "load_tools": ("wrench", None),
    "tool_search": ("search", "tool"),
    "notify": ("bell", None),
    "update_todos": ("list", None),
    # Web
    "web_search": ("globe", "search"),
    "web_fetch": ("globe", "page"),
    # Gmail
    "emails": ("mail", "email"),
    "read_email": ("mail", "email"),
    "send_email": ("mail", None),
    # Slack
    "slack_search": ("slack", "message"),
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
    "calendar": ("calendar", "event"),
    "create_calendar_event": ("calendar", None),
    "edit_calendar_event": ("calendar", None),
    "delete_calendar_event": ("calendar", None),
    # Google Drive
    "search_google_drive": ("search", "file"),
    "read_google_doc": ("file", "document"),
    "read_google_sheet": ("table", "spreadsheet"),
    "create_google_doc": ("file-plus", None),
    "edit_google_doc": ("edit", None),
    "create_google_sheet": ("table", None),
    "update_google_sheet": ("table", None),
    "append_google_sheet_rows": ("table", None),
    # Memory
    "memory_search": ("brain", "record"),
    "recall": ("brain", None),
    "remember": ("brain", None),
    "forget": ("brain", None),
    "memory_read": ("brain", None),
    "memory_patch": ("brain", None),
    "memory_tree": ("brain", None),
    "memory_rebuild": ("brain", None),
    # Sessions
    "search_transcripts": ("history", "transcript"),
    "read_session": ("history", None),
    "list_recent_sessions": ("history", None),
}

# Integration source → default icon, for tools without an explicit entry.
_BY_SOURCE: dict[str, str] = {
    "gmail": "mail",
    "slack": "slack",
    "calendar": "calendar",
    "google_drive": "folder",
    "web": "globe",
    "_memory": "brain",
    "_sessions": "history",
}


def tool_presentation(name: str, source: str | None) -> tuple[str | None, str | None]:
    """Return (icon, noun) for a tool. icon may be None (the client falls back
    to its own heuristic / a neutral dot)."""
    if name in _BY_NAME:
        return _BY_NAME[name]
    icon = _BY_SOURCE.get(source) if source else None
    return (icon, None)
