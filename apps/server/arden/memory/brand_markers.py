"""Current write markers plus read-only compatibility with pre-Arden vaults."""

import re

CURRENT_NAMESPACE = "arden"
LEGACY_NAMESPACE = "ntrp"

RECORDS_V2_RE = re.compile(rf"^<!-- (?:{CURRENT_NAMESPACE}|{LEGACY_NAMESPACE}):records schema=2(?: [^>]*)? -->$")
RECORDS_VERSION_RE = re.compile(
    rf"^<!-- (?:{CURRENT_NAMESPACE}|{LEGACY_NAMESPACE}):records schema=(?P<version>\d+)(?: [^>]*)? -->$"
)
META_RE = re.compile(rf"^  <!-- (?:{CURRENT_NAMESPACE}|{LEGACY_NAMESPACE}):meta (?P<meta>\{{.*\}}) -->$")
META_PREFIXES = tuple(f"<!-- {namespace}:meta " for namespace in (CURRENT_NAMESPACE, LEGACY_NAMESPACE))

INDEX_START = f"<!-- {CURRENT_NAMESPACE}:index:start -->"
INDEX_END = f"<!-- {CURRENT_NAMESPACE}:index:end -->"
INDEX_MARKER_PAIRS = tuple(
    (f"<!-- {namespace}:index:start -->", f"<!-- {namespace}:index:end -->")
    for namespace in (CURRENT_NAMESPACE, LEGACY_NAMESPACE)
)
PATH_RE = re.compile(rf"^(?P<line>- .*) <!-- (?:{CURRENT_NAMESPACE}|{LEGACY_NAMESPACE}):path=(?P<path>\S+) -->$")

PAGE_EDIT_EVENT_MARKER = f"<!-- {CURRENT_NAMESPACE}:page-edit-event -->"
PAGE_EDIT_EVENT_MARKERS = frozenset(
    f"<!-- {namespace}:page-edit-event -->" for namespace in (CURRENT_NAMESPACE, LEGACY_NAMESPACE)
)


def has_managed_index(content: str) -> bool:
    return any(start in content and end in content for start, end in INDEX_MARKER_PAIRS)
