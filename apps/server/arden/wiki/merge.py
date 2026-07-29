"""Snapshot-pinned duplicate-page merges without redirect resources."""

from hashlib import sha256

from arden.revisions.errors import RevisionConflictError
from arden.wiki.constants import WIKI_HEALTH_RESOURCE_ID
from arden.wiki.exceptions import GeneratedRegionConflictError, WikiValidationError
from arden.wiki.models import LinkStatus, PageMergePlan, PageMergeRewrite, WikiPageRecord, WikiSnapshot
from arden.wiki.pages import (
    create_page,
    extract_generated_region,
    extract_user_body,
    parse_page,
    update_generated_region,
)
from arden.wiki.provenance import parse_fact_provenance
from arden.wiki.rename import rename_target
from arden.wiki.snapshots import index, parse, reference, validate_prospective
from arden.wiki.wikilinks import parse_wikilinks, rewrite_page_targets

_PROVENANCE_FIELDS = ("generated_from_revision", "fact_citations")


def prepare_plan(
    snapshot: WikiSnapshot,
    *,
    canonical_page_id: str,
    canonical_expected_version: str,
    loser_page_id: str,
    loser_expected_version: str,
) -> PageMergePlan:
    if snapshot.head is None:
        raise WikiValidationError("page merge requires a committed wiki snapshot")
    if canonical_page_id == loser_page_id:
        raise WikiValidationError("page merge requires distinct canonical and loser pages")
    if WIKI_HEALTH_RESOURCE_ID in {canonical_page_id, loser_page_id}:
        raise WikiValidationError("health page is backend-managed")

    page_index = index(snapshot, strict_names=False)
    canonical = _require_active(page_index.pages.get(canonical_page_id), "canonical")
    loser = _require_active(page_index.pages.get(loser_page_id), "loser")
    _require_version(canonical, canonical_expected_version)
    _require_version(loser, loser_expected_version)

    canonical_body, canonical_references = _rewrite_loser_links(
        page_index, canonical, loser, canonical.resource.path, canonical.page.title
    )
    loser_body, loser_references = _rewrite_loser_links(
        page_index, loser, loser, canonical.resource.path, canonical.page.title
    )
    canonical = _with_body(canonical, canonical_body)
    loser = _with_body(loser, loser_body)
    merged_body, metadata = _merge_generated_content(canonical, loser)
    merged_body = _append_unique_user_body(canonical, merged_body, loser)
    aliases = _merged_aliases(canonical, loser)
    canonical_content = create_page(
        page_id=canonical.page.page_id,
        title=canonical.page.title,
        aliases=aliases,
        lifecycle=canonical.page.lifecycle,
        redirect_to=canonical.page.redirect_to,
        metadata=metadata,
        body=merged_body,
    ).to_bytes()

    rewrites: list[PageMergeRewrite] = []
    link_count = len(canonical_references) + len(loser_references)
    page_count = 1 if canonical_references or loser_references else 0
    for source in snapshot.pages:
        if source.page.page_id in {canonical_page_id, loser_page_id}:
            continue
        rewritten_body, references = _rewrite_loser_links(
            page_index, source, loser, canonical.resource.path, canonical.page.title
        )
        if not references:
            continue
        link_count += len(references)
        page_count += 1
        prefix_size = len(source.content) - len(source.page.body)
        rewrites.append(
            PageMergeRewrite(
                resource_id=source.page.page_id,
                expected_version=source.resource.version_id,
                content=source.content[:prefix_size] + rewritten_body,
                replacements=references,
            )
        )

    replacements = {canonical_page_id: canonical_content, **{item.resource_id: item.content for item in rewrites}}
    prospective = tuple(
        _replacement_record(record, replacements.get(record.page.page_id))
        for record in snapshot.pages
        if record.page.page_id != loser_page_id
    )
    validate_prospective(snapshot, prospective)
    return PageMergePlan(
        base_head=snapshot.head,
        canonical_page_id=canonical_page_id,
        canonical_expected_version=canonical_expected_version,
        canonical_title=canonical.page.title,
        loser_page_id=loser_page_id,
        loser_expected_version=loser_expected_version,
        loser_title=loser.page.title,
        canonical_content=canonical_content,
        rewrites=tuple(rewrites),
        link_count=link_count,
        page_count=page_count,
        idempotency_key=_key(
            snapshot.head,
            canonical_page_id,
            canonical_expected_version,
            loser_page_id,
            loser_expected_version,
        ),
    )


def _require_active(record: WikiPageRecord | None, role: str) -> WikiPageRecord:
    if record is None or record.page.lifecycle != "active":
        raise KeyError(f"unknown active {role} wiki page")
    return record


def _require_version(record: WikiPageRecord, expected_version: str) -> None:
    if record.resource.version_id != expected_version:
        raise RevisionConflictError(f"resource {record.page.page_id} changed: expected {expected_version}")


def _rewrite_loser_links(page_index, source, loser, canonical_path: str, canonical_title: str):
    references = tuple(
        item
        for node in parse_wikilinks(source.page.body.decode("utf-8"))
        if (item := reference(page_index, source.page.page_id, node)).status is LinkStatus.RESOLVED
        and item.target_page_id == loser.page.page_id
    )
    if not references:
        return source.page.body, ()
    targets = {item.node: rename_target(item.node, canonical_path, canonical_title) for item in references}
    return rewrite_page_targets(source.page.body.decode("utf-8"), targets).encode(), references


def _append_unique_user_body(canonical: WikiPageRecord, canonical_body: bytes, loser: WikiPageRecord) -> bytes:
    loser_user_body = extract_user_body(loser.content, expected_page_id=loser.page.page_id)
    canonical_content = canonical.page.with_body(canonical_body).to_bytes()
    canonical_user_body = extract_user_body(canonical_content, expected_page_id=canonical.page.page_id)
    if not loser_user_body.strip() or loser_user_body in canonical_user_body:
        return canonical_body
    prefix = canonical_body if not canonical_body or canonical_body.endswith(b"\n") else canonical_body + b"\n"
    suffix = b"" if loser_user_body.endswith(b"\n") else b"\n"
    return prefix + f"\n## Merged from {loser.page.title}\n\n".encode() + loser_user_body + suffix


def _merge_generated_content(
    canonical: WikiPageRecord,
    loser: WikiPageRecord,
) -> tuple[bytes, dict[str, object]]:
    canonical_generated = extract_generated_region(
        canonical.content,
        expected_page_id=canonical.page.page_id,
    )
    loser_generated = extract_generated_region(
        loser.content,
        expected_page_id=loser.page.page_id,
    )
    metadata = _merged_metadata(canonical, loser)
    loser_has_generated_state = loser_generated is not None or any(
        field in loser.page.metadata for field in _PROVENANCE_FIELDS
    )
    if not loser_has_generated_state:
        return canonical.page.body, metadata

    canonical_has_generated_state = canonical_generated is not None or any(
        field in canonical.page.metadata for field in _PROVENANCE_FIELDS
    )
    if not canonical_has_generated_state:
        for field in _PROVENANCE_FIELDS:
            if field in loser.page.metadata:
                metadata[field] = loser.page.metadata[field]
        return _replace_generated(canonical, loser_generated, metadata), metadata

    canonical_provenance = parse_fact_provenance(canonical.page.metadata, canonical.resource.path)
    loser_provenance = parse_fact_provenance(loser.page.metadata, loser.resource.path)
    if (
        canonical_provenance.warnings
        or loser_provenance.warnings
        or canonical_provenance.generated_from_revision is None
        or loser_provenance.generated_from_revision is None
        or canonical_provenance.generated_from_revision != loser_provenance.generated_from_revision
    ):
        raise GeneratedRegionConflictError("page merge requires matching valid generated provenance")

    metadata["generated_from_revision"] = canonical_provenance.generated_from_revision
    if "fact_citations" in canonical.page.metadata or "fact_citations" in loser.page.metadata:
        citations = []
        versions_by_fact: dict[str, str] = {}
        for citation in (*canonical_provenance.citations, *loser_provenance.citations):
            prior = versions_by_fact.get(citation.fact_id)
            if prior is not None and prior != citation.version:
                raise GeneratedRegionConflictError(
                    f"page merge has conflicting citation versions for fact {citation.fact_id}"
                )
            if prior is None:
                versions_by_fact[citation.fact_id] = citation.version
                citations.append(citation)
        metadata["fact_citations"] = [
            {"fact_id": citation.fact_id, "version": citation.version} for citation in citations
        ]
    merged_generated = _unique_generated_content(canonical_generated, loser_generated)
    return _replace_generated(canonical, merged_generated, metadata), metadata


def _replace_generated(
    canonical: WikiPageRecord,
    generated: bytes | None,
    metadata: dict[str, object],
) -> bytes:
    if generated is None:
        return canonical.page.body
    content = update_generated_region(
        canonical.content,
        expected_page_id=canonical.page.page_id,
        generated=generated,
        metadata=metadata,
    )
    return parse_page(content, expected_page_id=canonical.page.page_id).body


def _unique_generated_content(
    canonical: bytes | None,
    loser: bytes | None,
) -> bytes | None:
    if loser is None or loser == canonical:
        return canonical
    if canonical is None:
        return loser
    if loser == b"":
        return canonical
    if loser and loser in canonical:
        return canonical
    separator = b"" if not canonical or canonical.endswith(b"\n\n") else b"\n"
    return canonical + separator + loser


def _merged_metadata(
    canonical: WikiPageRecord,
    loser: WikiPageRecord,
) -> dict[str, object]:
    metadata = dict(canonical.page.metadata)
    for key, value in loser.page.metadata.items():
        if key in _PROVENANCE_FIELDS:
            continue
        if key not in metadata:
            metadata[key] = value
            continue
        if metadata[key] != value:
            raise WikiValidationError(f"page merge metadata conflict: {key}")
    return metadata


def _merged_aliases(canonical: WikiPageRecord, loser: WikiPageRecord) -> tuple[str, ...]:
    aliases: list[str] = []
    seen = {canonical.page.title.strip().casefold()}
    loser_path = loser.resource.path
    loser_stem = loser_path[:-3] if loser_path.casefold().endswith(".md") else loser_path
    for value in (
        *canonical.page.aliases,
        loser.page.title,
        *loser.page.aliases,
        loser_path,
        loser_stem,
    ):
        normalized = value.strip().casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        aliases.append(value)
    return tuple(aliases)


def _replacement_record(record: WikiPageRecord, content: bytes | None) -> WikiPageRecord:
    if content is None:
        return record
    return WikiPageRecord(record.resource, parse(record.resource, content), content)


def _with_body(record: WikiPageRecord, body: bytes) -> WikiPageRecord:
    if body == record.page.body:
        return record
    page = record.page.with_body(body)
    content = page.to_bytes()
    return WikiPageRecord(record.resource, page, content)


def _key(
    base_head: str,
    canonical_page_id: str,
    canonical_expected_version: str,
    loser_page_id: str,
    loser_expected_version: str,
) -> str:
    value = (
        "page-merge",
        base_head,
        canonical_page_id,
        canonical_expected_version,
        loser_page_id,
        loser_expected_version,
    )
    return "wiki-" + sha256(repr(value).encode()).hexdigest()
