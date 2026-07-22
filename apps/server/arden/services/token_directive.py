import re

# A turn-scoped output-token budget directive, Claude-Code style: the user puts
# "+500k" (or "+1.5m") in their message to cap the turn's output-token spend.
# The "+" must start the message or follow whitespace (so "2+5" / "a+b" don't
# match) and a k/m unit is required (so a stray "+5" can't set a 5-token ceiling
# that instantly halts the run). Returns the ceiling in tokens, or None.
_DIRECTIVE = re.compile(r"(?:^|\s)\+(\d+(?:\.\d+)?)([km])\b", re.IGNORECASE)

_UNIT = {"k": 1_000, "m": 1_000_000}


def parse_token_budget(text: str) -> int | None:
    if not text:
        return None
    match = _DIRECTIVE.search(text)
    if not match:
        return None
    return int(float(match.group(1)) * _UNIT[match.group(2).lower()])
