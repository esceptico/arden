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
