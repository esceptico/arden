"""Durable duplicate-page review construction for Wiki Maintenance."""

import asyncio
from hashlib import sha256

from arden.revisions.errors import RevisionConflictError
from arden.wiki.constants import TOPIC_PAGE_COLLISION_EVIDENCE_PREFIX
from arden.wiki.exceptions import WikiValidationError
from arden.wiki.maintenance.proposals import WikiMaintenanceError, fingerprint
from arden.wiki.maintenance.store import WikiMaintenanceReview, WikiMaintenanceReviewInput, WikiMaintenanceStore
from arden.wiki.models import PageMergePlan, WikiPageRecord
from arden.wiki.service import WikiService


def page_collision_review(plan: PageMergePlan) -> WikiMaintenanceReviewInput:
    replay_fingerprint = fingerprint(
        {
            "kind": "topic-page-collision",
            "canonical_page_id": plan.canonical_page_id,
            "canonical_expected_version": plan.canonical_expected_version,
            "loser_page_id": plan.loser_page_id,
            "loser_expected_version": plan.loser_expected_version,
        }
    )
    reason = f"wiki maintenance {plan.base_head} {replay_fingerprint}"
    proposal = {
        "kind": "page_merge",
        "reason": reason,
        "replay_fingerprint": replay_fingerprint,
        "summary": f"Merge {plan.loser_title} into {plan.canonical_title}.",
        "canonical_page_id": plan.canonical_page_id,
        "canonical_expected_version": plan.canonical_expected_version,
        "canonical_title": plan.canonical_title,
        "loser_page_id": plan.loser_page_id,
        "loser_expected_version": plan.loser_expected_version,
        "loser_title": plan.loser_title,
        "link_count": plan.link_count,
        "page_count": plan.page_count,
        "redirect_count": 0,
    }
    return WikiMaintenanceReviewInput(
        blocking_commit_id=plan.base_head,
        evidence_key=page_collision_evidence_key(plan.canonical_page_id, plan.loser_page_id),
        evidence_fingerprint=fingerprint({"base_head": plan.base_head, "proposal": proposal}),
        summary=f"Duplicate pages need a canonical merge: {plan.canonical_title} and {plan.loser_title}.",
        proposal_json=proposal,
    )


def manual_page_collision_review(
    *,
    base_head: str,
    canonical: WikiPageRecord,
    loser: WikiPageRecord,
    error: WikiValidationError,
) -> WikiMaintenanceReviewInput:
    replay_fingerprint = fingerprint(
        {
            "kind": "topic-page-collision-manual",
            "canonical_page_id": canonical.page.page_id,
            "canonical_version": canonical.resource.version_id,
            "loser_page_id": loser.page.page_id,
            "loser_version": loser.resource.version_id,
            "error": str(error),
        }
    )
    return WikiMaintenanceReviewInput(
        blocking_commit_id=base_head,
        evidence_key=page_collision_evidence_key(canonical.page.page_id, loser.page.page_id),
        evidence_fingerprint=fingerprint({"base_head": base_head, "replay_fingerprint": replay_fingerprint}),
        summary=f"Duplicate pages need manual reconciliation: {canonical.page.title} and {loser.page.title}. {error}",
        proposal_json=None,
    )


def page_collision_evidence_key(canonical_page_id: str, loser_page_id: str) -> str:
    pair = sha256(f"{canonical_page_id}\0{loser_page_id}".encode()).hexdigest()[:32]
    return f"{TOPIC_PAGE_COLLISION_EVIDENCE_PREFIX}{pair}"


async def record_page_collision_review(
    store: WikiMaintenanceStore,
    wiki: WikiService,
    *,
    canonical_page_id: str,
    loser_page_id: str,
) -> WikiMaintenanceReview:
    """Persist deterministic page-collision evidence in the existing Ask queue."""

    base_head = wiki.repository.head
    if base_head is None:
        raise WikiMaintenanceError("page collision requires a committed wiki")
    try:
        plan = await asyncio.to_thread(
            wiki.prepare_current_maintenance_merge,
            canonical_page_id=canonical_page_id,
            loser_page_id=loser_page_id,
        )
    except WikiValidationError as error:
        records = {record.page.page_id: record for record in await asyncio.to_thread(wiki.readable_pages)}
        if wiki.repository.head != base_head:
            raise RevisionConflictError(f"current head changed: expected {base_head!r}, found {wiki.repository.head!r}")
        try:
            canonical = records[canonical_page_id]
            loser = records[loser_page_id]
        except KeyError as missing:
            raise KeyError("page collision changed before manual review") from missing
        return await store.record_page_collision_review(
            manual_page_collision_review(
                base_head=base_head,
                canonical=canonical,
                loser=loser,
                error=error,
            )
        )
    return await store.record_page_collision_review(page_collision_review(plan))
