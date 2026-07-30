from collections.abc import Sequence

# Allowlist-only tool scoping (learned from dex's toolset design: no
# denylist — narrow the allowlist instead; one mental model). Grammar:
#   '*'        → everything
#   'read:*'   → every read-only tool (the read floor)
#   'recall'   → exact name
#   'slack_*'  → prefix wildcard
# A scope is a hard outer gate: it filters the pool AFTER every other
# selection (capabilities, action class, extras) so a scoped run can never
# widen past its author's declaration.

# The read floor used to be applied to every scoped run unconditionally, which
# made "this run can read anything" an invisible property of the executor
# rather than a decision anyone wrote down. It is a grant like any other now:
# a run that should see every read-only tool says so in its own allowlist.
READ_FLOOR = "read:*"


def matches_scope(patterns: Sequence[str], name: str) -> bool:
    for pattern in patterns:
        if pattern == "*" or pattern == name:
            return True
        if pattern.endswith("*") and name.startswith(pattern[:-1]):
            return True
    return False


def expand_scope(patterns: Sequence[str], read_only_names: Sequence[str]) -> tuple[str, ...]:
    """Resolve `read:*` into the concrete read-only names.

    An automation scoped to its one write tool still needs to look around
    (list sessions, read files) to execute its own prompt — it just has to
    declare that now instead of receiving it silently.
    """
    if READ_FLOOR not in patterns:
        return tuple(dict.fromkeys(patterns))
    granted = (p for p in patterns if p != READ_FLOOR)
    return tuple(dict.fromkeys((*granted, *read_only_names)))
