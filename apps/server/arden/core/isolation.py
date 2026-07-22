from enum import StrEnum


class IsolationLevel(StrEnum):
    FULL = "full"  # New session state, no inherited context
    SHARED = "shared"  # Use parent's session state
