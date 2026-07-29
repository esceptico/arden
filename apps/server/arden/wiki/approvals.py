"""Durable approval coordination for snapshot-pinned wiki renames."""

import base64
import binascii
import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any
from uuid import uuid4

from arden.revisions.errors import RevisionConflictError
from arden.wiki.approval_store import WikiRenameApproval, WikiRenameApprovalStatus, WikiRenameApprovalStore
from arden.wiki.models import LinkReference, LinkStatus, RenamePlan, RenameRewrite
from arden.wiki.service import WikiService, WikiValidationError
from arden.wiki.wikilinks import WikilinkNode

_PLAN_SCHEMA_VERSION = 1
_PLAN_FIELDS = frozenset(
    {
        "base_head",
        "page_id",
        "expected_version",
        "old_path",
        "new_path",
        "old_title",
        "new_title",
        "moved_content",
        "redirect_page_id",
        "rewrite_links",
        "link_count",
        "page_count",
        "rewrites",
        "idempotency_key",
    }
)
_WIKILINK_NODE_FIELDS = frozenset(
    {
        "source_sha256",
        "source_size",
        "start",
        "end",
        "page_start",
        "page_end",
        "page",
        "fragment",
        "alias",
        "embed",
        "raw",
    }
)


class RenamePolicy(StrEnum):
    ALWAYS = "always"
    ASK = "ask"
    NEVER = "never"


class RenamePlanSerializationError(ValueError):
    """A persisted rename plan is malformed or not canonical."""


class CorruptWikiRenameApprovalError(ValueError):
    """A durable approval no longer agrees with its persisted plan."""


@dataclass(frozen=True, slots=True)
class WikiRenameCoordinatorResult:
    status: str
    approval: WikiRenameApproval | None = None
    commit_id: str | None = None
    replacement_approval_id: str | None = None


def serialize_rename_plan(plan: RenamePlan) -> str:
    """Return the only persisted representation accepted for a rename plan."""

    if not isinstance(plan, RenamePlan):
        raise TypeError("plan must be a RenamePlan")
    return _canonical_json({"version": _PLAN_SCHEMA_VERSION, "plan": _plan_value(plan)})


def deserialize_rename_plan(plan_json: str) -> RenamePlan:
    """Decode one exact canonical plan, rejecting unknown or missing fields."""

    if not isinstance(plan_json, str):
        raise RenamePlanSerializationError("plan_json must be a string")
    try:
        value = json.loads(plan_json, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, RenamePlanSerializationError) as exc:
        raise RenamePlanSerializationError("plan_json is not valid JSON") from exc
    if _canonical_json(value) != plan_json:
        raise RenamePlanSerializationError("plan_json is not canonical")
    _require_keys(value, {"version", "plan"}, "envelope")
    if value["version"] != _PLAN_SCHEMA_VERSION:
        raise RenamePlanSerializationError("unsupported rename plan schema version")
    return _decode_plan(value["plan"])


def rename_plan_fingerprint(plan_json: str) -> str:
    """Return the SHA-256 of a canonical serialized rename plan."""

    deserialize_rename_plan(plan_json)
    return hashlib.sha256(plan_json.encode("utf-8")).hexdigest()


class WikiRenameApprovalCoordinator:
    """Policy boundary around :class:`WikiService` and durable approvals."""

    def __init__(self, service: WikiService, store: WikiRenameApprovalStore) -> None:
        self.service = service
        self.store = store

    async def request_rename(
        self,
        *,
        request_key: str,
        page_id: str,
        new_path: str,
        new_title: str,
        expected_version: str,
        base_head: str | None,
        policy: RenamePolicy | str = RenamePolicy.ASK,
        apply_plan: Callable[[RenamePlan], Awaitable[object]] | None = None,
    ) -> WikiRenameCoordinatorResult:
        """Prepare and either commit or durably request a rename approval."""

        selected = RenamePolicy(policy)
        rewrite_links = selected is not RenamePolicy.NEVER
        plan = self.service.prepare_rename(
            page_id,
            new_path=new_path,
            new_title=new_title,
            expected_version=expected_version,
            base_head=base_head,
            rewrite_links=rewrite_links,
        )
        if selected is not RenamePolicy.ASK:
            commit = await self._apply(plan, apply_plan)
            return WikiRenameCoordinatorResult(status="committed", commit_id=commit.commit_id)
        approval = await self._create_pending(request_key, plan, generation=0)
        return WikiRenameCoordinatorResult(status="pending", approval=approval)

    async def accept(
        self,
        approval_id: str,
        *,
        apply_plan: Callable[[RenamePlan], Awaitable[object]] | None = None,
    ) -> WikiRenameCoordinatorResult:
        """Apply a verified pending plan, or replace it after a head conflict."""

        approval = await self._required_approval(approval_id)
        if approval.status is WikiRenameApprovalStatus.ACCEPTED:
            return WikiRenameCoordinatorResult(status="accepted", approval=approval, commit_id=approval.commit_id)
        if approval.status not in {WikiRenameApprovalStatus.PENDING, WikiRenameApprovalStatus.APPLYING}:
            return WikiRenameCoordinatorResult(status=approval.status, approval=approval)
        plan = _verified_plan(approval)
        if approval.status is WikiRenameApprovalStatus.PENDING:
            claimed = await self.store.claim_accept(approval_id)
            if not claimed:
                current = await self._required_approval(approval_id)
                return WikiRenameCoordinatorResult(
                    status=current.status,
                    approval=current,
                    commit_id=current.commit_id,
                    replacement_approval_id=current.replacement_approval_id,
                )
            approval = await self._required_approval(approval_id)
            if approval.status is not WikiRenameApprovalStatus.APPLYING:
                raise CorruptWikiRenameApprovalError("accepted rename claim was not persisted")
        try:
            commit = await self._apply(plan, apply_plan)
        except RevisionConflictError as error:
            if self.service.repository.head == plan.base_head:
                await self.store.release_accept(
                    approval.approval_id,
                    resolution=f"managed wiki files changed outside revision history: {error}",
                )
                current = await self._required_approval(approval.approval_id)
                return WikiRenameCoordinatorResult(status=current.status, approval=current)
            try:
                replacement = await self._replace_after_conflict(approval, plan)
            except (KeyError, RevisionConflictError, WikiValidationError) as error:
                await self.store.release_accept(
                    approval.approval_id,
                    resolution=f"stale plan could not be regenerated: {error}",
                )
                current = await self._required_approval(approval.approval_id)
                if current.replacement_approval_id:
                    replacement = await self._required_approval(current.replacement_approval_id)
                    return WikiRenameCoordinatorResult(
                        status="pending",
                        approval=replacement,
                        replacement_approval_id=replacement.approval_id,
                    )
                return WikiRenameCoordinatorResult(
                    status=current.status,
                    approval=current,
                    commit_id=current.commit_id,
                )
            return WikiRenameCoordinatorResult(
                status="pending",
                approval=replacement,
                replacement_approval_id=replacement.approval_id,
            )
        accepted = await self.store.accept(approval.approval_id, commit_id=commit.commit_id, resolution="applied")
        if not accepted:
            current = await self._required_approval(approval.approval_id)
            if current.status is WikiRenameApprovalStatus.ACCEPTED and current.commit_id == commit.commit_id:
                return WikiRenameCoordinatorResult(status="accepted", approval=current, commit_id=commit.commit_id)
            raise CorruptWikiRenameApprovalError("approval was resolved while its rename was committed")
        current = await self._required_approval(approval.approval_id)
        return WikiRenameCoordinatorResult(status="accepted", approval=current, commit_id=commit.commit_id)

    async def reject(self, approval_id: str, *, resolution: str | None = None) -> WikiRenameCoordinatorResult:
        """Terminally reject a pending approval without committing it."""

        approval = await self._required_approval(approval_id)
        if approval.status is WikiRenameApprovalStatus.PENDING:
            await self.store.reject(approval_id, resolution=resolution)
            approval = await self._required_approval(approval_id)
        return WikiRenameCoordinatorResult(status=approval.status, approval=approval)

    async def list_pending(self) -> list[WikiRenameApproval]:
        return await self.store.list_pending()

    async def _create_pending(self, request_key: str, plan: RenamePlan, *, generation: int) -> WikiRenameApproval:
        plan_json = serialize_rename_plan(plan)
        return await self.store.create_pending(
            approval_id=str(uuid4()),
            request_key=request_key,
            request_fingerprint=rename_plan_fingerprint(plan_json),
            generation=generation,
            base_head=plan.base_head,
            plan_json=plan_json,
            old_path=plan.old_path,
            new_path=plan.new_path,
            link_count=plan.link_count,
            page_count=plan.page_count,
        )

    async def _replace_after_conflict(self, approval: WikiRenameApproval, plan: RenamePlan) -> WikiRenameApproval:
        current = self.service.read_page(plan.page_id)
        fresh = self.service.prepare_rename(
            plan.page_id,
            new_path=plan.new_path,
            new_title=plan.new_title,
            expected_version=current.resource.version_id,
            base_head=self.service.repository.head,
            rewrite_links=plan.rewrite_links,
        )
        plan_json = serialize_rename_plan(fresh)
        replacement = await self.store.replace_pending(
            approval.approval_id,
            approval_id=str(uuid4()),
            request_key=approval.request_key,
            request_fingerprint=rename_plan_fingerprint(plan_json),
            generation=approval.generation + 1,
            base_head=fresh.base_head,
            plan_json=plan_json,
            old_path=fresh.old_path,
            new_path=fresh.new_path,
            link_count=fresh.link_count,
            page_count=fresh.page_count,
            resolution="base head changed",
        )
        if replacement is None:
            current = await self._required_approval(approval.approval_id)
            if current.replacement_approval_id:
                replacement = await self._required_approval(current.replacement_approval_id)
            else:
                raise CorruptWikiRenameApprovalError("approval changed while replacing its stale plan")
        return replacement

    async def _required_approval(self, approval_id: str) -> WikiRenameApproval:
        approval = await self.store.get(approval_id)
        if approval is None:
            raise KeyError(f"unknown wiki rename approval: {approval_id}")
        return approval

    async def _apply(
        self,
        plan: RenamePlan,
        apply_plan: Callable[[RenamePlan], Awaitable[object]] | None,
    ):
        if apply_plan is None:
            return self.service.apply_rename(plan)
        return await apply_plan(plan)


def _verified_plan(approval: WikiRenameApproval) -> RenamePlan:
    try:
        plan = deserialize_rename_plan(approval.plan_json)
        fingerprint = rename_plan_fingerprint(approval.plan_json)
    except RenamePlanSerializationError as exc:
        raise CorruptWikiRenameApprovalError("stored rename plan is corrupt") from exc
    if fingerprint != approval.request_fingerprint:
        raise CorruptWikiRenameApprovalError("stored rename plan fingerprint does not match")
    if (
        plan.base_head != approval.base_head
        or plan.old_path != approval.old_path
        or plan.new_path != approval.new_path
        or plan.link_count != approval.link_count
        or plan.page_count != approval.page_count
    ):
        raise CorruptWikiRenameApprovalError("stored rename plan metadata does not match")
    return plan


def _plan_value(plan: RenamePlan) -> dict[str, object]:
    return {
        "base_head": plan.base_head,
        "page_id": plan.page_id,
        "expected_version": plan.expected_version,
        "old_path": plan.old_path,
        "new_path": plan.new_path,
        "old_title": plan.old_title,
        "new_title": plan.new_title,
        "moved_content": _bytes_value(plan.moved_content),
        "redirect_page_id": plan.redirect_page_id,
        "rewrite_links": plan.rewrite_links,
        "link_count": plan.link_count,
        "page_count": plan.page_count,
        "rewrites": [_rewrite_value(rewrite) for rewrite in plan.rewrites],
        "idempotency_key": plan.idempotency_key,
    }


def _rewrite_value(rewrite: RenameRewrite) -> dict[str, object]:
    return {
        "resource_id": rewrite.resource_id,
        "expected_version": rewrite.expected_version,
        "content": _bytes_value(rewrite.content),
        "replacements": [_reference_value(reference) for reference in rewrite.replacements],
    }


def _reference_value(reference: LinkReference) -> dict[str, object]:
    return {
        "source_page_id": reference.source_page_id,
        "node": asdict(reference.node),
        "status": reference.status.value,
        "target_page_id": reference.target_page_id,
        "candidates": list(reference.candidates),
    }


def _decode_plan(value: Any) -> RenamePlan:
    _require_keys(value, _PLAN_FIELDS, "plan")
    return RenamePlan(
        base_head=_optional_string(value["base_head"], "plan.base_head"),
        page_id=_string(value["page_id"], "plan.page_id"),
        expected_version=_string(value["expected_version"], "plan.expected_version"),
        old_path=_string(value["old_path"], "plan.old_path"),
        new_path=_string(value["new_path"], "plan.new_path"),
        old_title=_string(value["old_title"], "plan.old_title"),
        new_title=_string(value["new_title"], "plan.new_title"),
        moved_content=_bytes(value["moved_content"], "plan.moved_content"),
        redirect_page_id=_string(value["redirect_page_id"], "plan.redirect_page_id"),
        rewrite_links=_bool(value["rewrite_links"], "plan.rewrite_links"),
        link_count=_nonnegative(value["link_count"], "plan.link_count"),
        page_count=_nonnegative(value["page_count"], "plan.page_count"),
        rewrites=tuple(
            _decode_rewrite(item, index) for index, item in enumerate(_list(value["rewrites"], "plan.rewrites"))
        ),
        idempotency_key=_string(value["idempotency_key"], "plan.idempotency_key"),
    )


def _decode_rewrite(value: Any, index: int) -> RenameRewrite:
    label = f"plan.rewrites[{index}]"
    _require_keys(value, {"resource_id", "expected_version", "content", "replacements"}, label)
    return RenameRewrite(
        resource_id=_string(value["resource_id"], f"{label}.resource_id"),
        expected_version=_string(value["expected_version"], f"{label}.expected_version"),
        content=_bytes(value["content"], f"{label}.content"),
        replacements=tuple(
            _decode_reference(item, f"{label}.replacements[{item_index}]")
            for item_index, item in enumerate(_list(value["replacements"], f"{label}.replacements"))
        ),
    )


def _decode_reference(value: Any, label: str) -> LinkReference:
    _require_keys(value, {"source_page_id", "node", "status", "target_page_id", "candidates"}, label)
    node_value = value["node"]
    _require_keys(node_value, _WIKILINK_NODE_FIELDS, f"{label}.node")
    try:
        status = LinkStatus(_string(value["status"], f"{label}.status"))
    except ValueError as exc:
        raise RenamePlanSerializationError(f"{label}.status is invalid") from exc
    return LinkReference(
        source_page_id=_string(value["source_page_id"], f"{label}.source_page_id"),
        node=WikilinkNode(
            source_sha256=_string(node_value["source_sha256"], f"{label}.node.source_sha256"),
            source_size=_nonnegative(node_value["source_size"], f"{label}.node.source_size"),
            start=_nonnegative(node_value["start"], f"{label}.node.start"),
            end=_nonnegative(node_value["end"], f"{label}.node.end"),
            page_start=_optional_nonnegative(node_value["page_start"], f"{label}.node.page_start"),
            page_end=_optional_nonnegative(node_value["page_end"], f"{label}.node.page_end"),
            page=_optional_string(node_value["page"], f"{label}.node.page"),
            fragment=_optional_string(node_value["fragment"], f"{label}.node.fragment"),
            alias=_optional_string(node_value["alias"], f"{label}.node.alias"),
            embed=_bool(node_value["embed"], f"{label}.node.embed"),
            raw=_string(node_value["raw"], f"{label}.node.raw"),
        ),
        status=status,
        target_page_id=_optional_string(value["target_page_id"], f"{label}.target_page_id"),
        candidates=tuple(
            _string(item, f"{label}.candidates") for item in _list(value["candidates"], f"{label}.candidates")
        ),
    )


def _bytes_value(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _bytes(value: Any, label: str) -> bytes:
    raw = _string(value, label)
    try:
        decoded = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise RenamePlanSerializationError(f"{label} is not base64") from exc
    if _bytes_value(decoded) != raw:
        raise RenamePlanSerializationError(f"{label} is not canonical base64")
    return decoded


def _canonical_json(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise RenamePlanSerializationError("plan contains invalid JSON values") from exc


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise RenamePlanSerializationError("plan_json contains duplicate keys")
        result[key] = value
    return result


def _require_keys(value: Any, keys: frozenset[str] | set[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) != keys:
        raise RenamePlanSerializationError(f"{label} has an invalid shape")


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise RenamePlanSerializationError(f"{label} must be a string")
    return value


def _optional_string(value: Any, label: str) -> str | None:
    return None if value is None else _string(value, label)


def _bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise RenamePlanSerializationError(f"{label} must be a boolean")
    return value


def _nonnegative(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise RenamePlanSerializationError(f"{label} must be a nonnegative integer")
    return value


def _optional_nonnegative(value: Any, label: str) -> int | None:
    return None if value is None else _nonnegative(value, label)


def _list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise RenamePlanSerializationError(f"{label} must be a list")
    return value
