import re

FTS_QUERY_MAX_CHARS = 4_096
FTS_QUERY_MAX_TERMS = 64
FTS_TOKEN_MAX_CHARS = 96

_DQ = '"'
_TOKEN_RE = re.compile(r"[\w][\w'-]*", re.UNICODE)


def build_fts_or_query(
    query: str,
    *,
    max_terms: int = FTS_QUERY_MAX_TERMS,
    max_chars: int = FTS_QUERY_MAX_CHARS,
) -> str:
    """Build a bounded FTS5 OR query from arbitrary free text."""
    if max_terms <= 0 or max_chars <= 0:
        return ""

    seen: set[str] = set()
    terms: list[str] = []
    for match in _TOKEN_RE.finditer(query[:max_chars]):
        term = match.group(0).strip("_'-").lower()
        if not term:
            continue
        term = term[:FTS_TOKEN_MAX_CHARS]
        if term in seen:
            continue
        seen.add(term)
        terms.append(term)
        if len(terms) >= max_terms:
            break

    return " OR ".join(f"{_DQ}{term.replace(_DQ, _DQ + _DQ)}{_DQ}" for term in terms)
