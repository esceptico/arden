"""Shared validation for stored automation tool scopes."""

from collections.abc import Iterable
from difflib import get_close_matches

from arden.tools.core.scope import matches_scope


def validate_tool_scope(patterns: Iterable[str], registered_names: Iterable[str]) -> None:
    """Reject scope entries which cannot grant any registered tool."""
    names = tuple(registered_names)
    unmatched = [pattern for pattern in patterns if not any(matches_scope((pattern,), name) for name in names)]
    if not unmatched:
        return

    hints = []
    for pattern in unmatched:
        close = get_close_matches(pattern.rstrip("*"), names, n=3, cutoff=0.5)
        suggestion = f" (did you mean: {', '.join(close)}?)" if close else ""
        hints.append(f"'{pattern}'{suggestion}")
    raise ValueError(f"tool_scope patterns match no registered tool: {'; '.join(hints)}")


def validate_literal_tool_scope(patterns: Iterable[str], registered_names: Iterable[str]) -> None:
    """Validate a durable scope whose reviewed capabilities must not expand later."""
    values = tuple(patterns)
    wildcard = [pattern for pattern in values if pattern == "*" or pattern.endswith("*")]
    if wildcard:
        rendered = ", ".join(repr(pattern) for pattern in wildcard)
        raise ValueError(f"producer tool scopes require exact tool names, not wildcard patterns: {rendered}")
    validate_tool_scope(values, registered_names)
