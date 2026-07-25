DISPLAY_TITLE_ARG = "_display_title"
RESERVED_TOOL_ARGUMENTS = frozenset({DISPLAY_TITLE_ARG})


def split_tool_arguments(arguments: dict) -> tuple[str | None, dict]:
    raw_title = arguments.get(DISPLAY_TITLE_ARG)
    title = raw_title.strip() if isinstance(raw_title, str) and raw_title.strip() else None
    cleaned = {key: value for key, value in arguments.items() if key not in RESERVED_TOOL_ARGUMENTS}
    return title, cleaned
