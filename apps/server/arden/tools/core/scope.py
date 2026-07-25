from collections.abc import Sequence

# Allowlist-only tool scoping (learned from dex's toolset design: no
# denylist — narrow the allowlist instead; one mental model). Grammar:
#   '*'        → everything
#   'recall'   → exact name
#   'slack_*'  → prefix wildcard
# A scope is a hard outer gate: it filters the pool AFTER every other
# selection (capabilities, action class, extras) so a scoped run can never
# widen past its author's declaration.


def matches_scope(patterns: Sequence[str], name: str) -> bool:
    for pattern in patterns:
        if pattern == "*" or pattern == name:
            return True
        if pattern.endswith("*") and name.startswith(pattern[:-1]):
            return True
    return False


def with_read_floor(patterns: Sequence[str], read_only_names: Sequence[str]) -> tuple[str, ...]:
    """A user-authored allowlist grants tools ON TOP of the always-available
    read-only floor. A scope bounds the dangerous surface, never the safe one —
    an automation scoped to its one write tool must still be able to look
    around (list sessions, read files) to execute its own prompt."""
    return tuple(dict.fromkeys((*patterns, *read_only_names)))
