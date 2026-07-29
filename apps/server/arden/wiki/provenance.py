"""Canonical parsing and freshness rules for fact-backed wiki pages."""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from arden.wiki.models import WikiChangeWarning, WikiFactCitation

_SHA256 = re.compile(r"[0-9a-f]{64}")


class FactPageFreshness(StrEnum):
    CURRENT = "current"
    STALE = "stale"
    INVALID = "invalid"


@dataclass(frozen=True, slots=True)
class WikiFactProvenance:
    generated_from_revision: str | None
    citations: tuple[WikiFactCitation, ...]
    warnings: tuple[WikiChangeWarning, ...]


def parse_fact_provenance(metadata: Mapping[str, object], path: str) -> WikiFactProvenance:
    warnings: list[WikiChangeWarning] = []
    generated = metadata.get("generated_from_revision")
    if "generated_from_revision" not in metadata:
        generated_from_revision = None
    elif isinstance(generated, str) and _SHA256.fullmatch(generated):
        generated_from_revision = generated
    else:
        generated_from_revision = None
        warnings.append(WikiChangeWarning("invalid_generated_from_revision", path, repr(generated)))

    if "fact_citations" not in metadata:
        return WikiFactProvenance(generated_from_revision, (), tuple(warnings))

    raw_citations = metadata["fact_citations"]
    if not isinstance(raw_citations, Sequence) or isinstance(raw_citations, str | bytes):
        warnings.append(WikiChangeWarning("invalid_fact_citations", path, repr(raw_citations)))
        return WikiFactProvenance(generated_from_revision, (), tuple(warnings))

    citations: list[WikiFactCitation] = []
    for index, item in enumerate(raw_citations):
        if (
            isinstance(item, Mapping)
            and isinstance(item.get("fact_id"), str)
            and item["fact_id"].strip()
            and "\0" not in item["fact_id"]
            and isinstance(item.get("version"), str)
            and _SHA256.fullmatch(item["version"])
        ):
            citations.append(WikiFactCitation(item["fact_id"], item["version"]))
            continue
        warnings.append(WikiChangeWarning("invalid_fact_citations", path, f"entry {index}: {item!r}"))
    return WikiFactProvenance(generated_from_revision, tuple(citations), tuple(warnings))


def fact_page_freshness(
    metadata: Mapping[str, object],
    fact_revision: str | None,
) -> FactPageFreshness:
    if "fact_citations" not in metadata:
        return FactPageFreshness.CURRENT
    provenance = parse_fact_provenance(metadata, "")
    if provenance.warnings or provenance.generated_from_revision is None:
        return FactPageFreshness.INVALID
    if fact_revision is not None and provenance.generated_from_revision != fact_revision:
        return FactPageFreshness.STALE
    return FactPageFreshness.CURRENT
