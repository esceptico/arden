"""Canonical markers used in Arden-managed Markdown."""

import re

CURRENT_NAMESPACE = "arden"

RECORDS_V2_RE = re.compile(rf"^<!-- {CURRENT_NAMESPACE}:records schema=2(?: [^>]*)? -->$")
RECORDS_VERSION_RE = re.compile(rf"^<!-- {CURRENT_NAMESPACE}:records schema=(?P<version>\d+)(?: [^>]*)? -->$")
META_RE = re.compile(rf"^  <!-- {CURRENT_NAMESPACE}:meta (?P<meta>\{{.*\}}) -->$")
META_PREFIXES = (f"<!-- {CURRENT_NAMESPACE}:meta ",)

INDEX_START = f"<!-- {CURRENT_NAMESPACE}:index:start -->"
INDEX_END = f"<!-- {CURRENT_NAMESPACE}:index:end -->"
INDEX_MARKER_PAIRS = ((INDEX_START, INDEX_END),)
PATH_RE = re.compile(rf"^(?P<line>- .*) <!-- {CURRENT_NAMESPACE}:path=(?P<path>\S+) -->$")

PAGE_EDIT_EVENT_MARKER = f"<!-- {CURRENT_NAMESPACE}:page-edit-event -->"
PAGE_EDIT_EVENT_MARKERS = frozenset((PAGE_EDIT_EVENT_MARKER,))


def has_managed_index(content: str) -> bool:
    return any(start in content and end in content for start, end in INDEX_MARKER_PAIRS)
