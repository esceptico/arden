"""Revision-safe page edits with exact, atomic reconciliation events."""

from __future__ import annotations

import asyncio
import base64
import difflib
import inspect
import json
import logging
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Literal
from uuid import uuid4
from weakref import WeakValueDictionary
from zoneinfo import ZoneInfo

from ntrp.memory.artifacts import ArtifactMemoryStore
from ntrp.memory.file_store import CanonicalFileRole, FilePageStore
from ntrp.memory.models import SourceRef
from ntrp.memory.page_events import (
    AppliedPageOperation,
    PageEditAnalysis,
    PageEditDecision,
    PageEditEvent,
    PageEditPreview,
    PageEditQuestion,
    page_revision,
    parse_page_edit_events,
    render_page_edit_event,
    unified_patch,
)
from ntrp.memory.reconciler import RecordOperation, validate_operations

_logger = logging.getLogger(__name__)
_VAULT_APPLY_LOCKS: WeakValueDictionary[Path, asyncio.Lock] = WeakValueDictionary()


class StalePageRevisionError(ValueError):
    pass


class PreviewExpiredError(ValueError):
    pass


class ReconciliationPendingError(ValueError):
    pass


class PageEditService:
    def __init__(
        self,
        vault: Path,
        store: FilePageStore,
        *,
        reconciler,
        timezone: str | tzinfo | None = None,
        now=None,
        preview_ttl: timedelta = timedelta(hours=1),
    ) -> None:
        self._vault = Path(vault)
        self._store = store
        self._resources = ArtifactMemoryStore(self._vault)
        self._reconciler = reconciler
        self._timezone = ZoneInfo(timezone) if isinstance(timezone, str) else timezone
        self._now = now or (lambda: datetime.now().astimezone())
        self._preview_ttl = preview_ttl
        self._apply_lock = _VAULT_APPLY_LOCKS.setdefault(self._vault.resolve(), asyncio.Lock())

    async def preview(
        self,
        *,
        path: str,
        base_revision: str,
        content: bytes,
        actor: str,
        origin: Literal["desktop", "external", "agent", "synthesis"] = "desktop",
    ) -> PageEditPreview:
        safe_path = self._validate_page_path(path)
        if not actor.strip():
            raise ValueError("page edit actor is required")
        base = self._resources.read_resource_bytes(safe_path)
        if page_revision(base) != base_revision:
            raise StalePageRevisionError(safe_path)
        patch = unified_patch(base, content)
        analysis = _analyze(safe_path, base, content, patch)
        answer = await self._reconcile(analysis)
        pending = answer is None
        operations = tuple(answer or ())
        source = self._source(uuid4().hex, actor, self._local_now())
        if operations:
            records = tuple(await self._store.list(limit=None, scopes=None))
            operations = validate_operations(operations, records, source)
        preview_id = uuid4().hex
        questions = tuple(
            PageEditQuestion(id=f"operation:{index}", operation_index=index, question=operation.question or "")
            for index, operation in enumerate(operations)
            if operation.op == "ASK"
        )
        preview = PageEditPreview(
            id=preview_id,
            path=safe_path,
            base_revision=base_revision,
            result_revision=page_revision(content),
            patch=patch,
            operations=operations,
            questions=questions,
            analysis_pending=pending,
        )
        created = self._local_now()
        envelope = {
            "preview": preview.model_dump(mode="json"),
            "content": base64.b64encode(content).decode("ascii"),
            "analysis": analysis.model_dump(mode="json"),
            "actor": actor,
            "origin": origin,
            "created_at": created.isoformat(),
            "expires_at": (created + self._preview_ttl).isoformat(),
        }
        self._resources.write_page_edit_preview(
            self._preview_rel(preview_id),
            (json.dumps(envelope, sort_keys=True, separators=(",", ":")) + "\n").encode(),
        )
        return preview

    async def apply(
        self,
        preview_id: str,
        *,
        decisions: dict[str, PageEditDecision | str],
        save_as_pending: bool = False,
    ) -> PageEditEvent:
        async with self._apply_lock:
            return await self._apply(preview_id, decisions=decisions, save_as_pending=save_as_pending)

    async def _apply(
        self,
        preview_id: str,
        *,
        decisions: dict[str, PageEditDecision | str],
        save_as_pending: bool,
    ) -> PageEditEvent:
        existing = next((event for event in self.history() if event.id == preview_id), None)
        if existing is not None:
            self._resources.delete_page_edit_preview(self._preview_rel(preview_id))
            return existing
        envelope = self._load_preview(preview_id)
        preview = PageEditPreview.model_validate(envelope["preview"])
        candidate = base64.b64decode(envelope["content"], validate=True)
        current = self._resources.read_resource_bytes(preview.path)
        if page_revision(current) != preview.base_revision:
            raise StalePageRevisionError(preview.path)
        if preview.analysis_pending and not save_as_pending:
            raise ReconciliationPendingError("page edit analysis is unavailable; explicit pending save is required")
        occurred = self._local_now()
        source = self._source(preview.id, str(envelope["actor"]), occurred)
        operations = () if preview.analysis_pending else self._resolve(preview.operations, preview.questions, decisions)
        if operations:
            records = tuple(await self._store.list(limit=None, scopes=None))
            operations = validate_operations(operations, records, source)
        event = PageEditEvent(
            id=preview.id,
            occurred_at=_milliseconds(occurred),
            sequence=self._next_sequence(),
            actor=str(envelope["actor"]),
            origin=envelope["origin"],
            path=preview.path,
            base_revision=preview.base_revision,
            result_revision=preview.result_revision,
            patch=preview.patch,
            operations=tuple(AppliedPageOperation.from_operation(operation, (source,)) for operation in operations),
            reconciliation="pending" if preview.analysis_pending else "applied",
            analysis=PageEditAnalysis.model_validate(envelope["analysis"]),
        )
        planned = self._store.plan_operations(operations, source, batch_key=f"page-edit:{event.id}") if operations else {}
        event_rel, event_bytes = self._event_file(event)
        caller_files = {
            Path(preview.path): candidate,
            event_rel: event_bytes,
        }
        caller_roles = {
            Path(preview.path): CanonicalFileRole.USER_PAGE,
            event_rel: CanonicalFileRole.EVENT,
        }
        files = dict(planned)
        files.update(self._store._validate_caller_files(caller_files, caller_roles))
        self._commit(files)
        self._resources.delete_page_edit_preview(self._preview_rel(preview.id))
        return event

    async def retry(
        self,
        event_id: str,
        *,
        decisions: dict[str, PageEditDecision | str] | None = None,
    ) -> PageEditEvent:
        async with self._apply_lock:
            return await self._retry(event_id, decisions=decisions)

    async def _retry(
        self,
        event_id: str,
        *,
        decisions: dict[str, PageEditDecision | str] | None,
    ) -> PageEditEvent:
        events = self.history()
        pending = next((event for event in events if event.id == event_id), None)
        if pending is None or pending.reconciliation != "pending" or pending.analysis is None:
            raise ValueError("pending page edit event not found")
        if any(event.reconciles_event_id == event_id for event in events):
            raise ValueError("page edit event already reconciled")
        answer = await self._reconcile(pending.analysis)
        if answer is None:
            raise ReconciliationPendingError("page edit analysis remains unavailable")
        raw_operations = tuple(answer)
        questions = tuple(
            PageEditQuestion(id=f"operation:{index}", operation_index=index, question=operation.question or "")
            for index, operation in enumerate(raw_operations)
            if operation.op == "ASK"
        )
        operations = self._resolve(raw_operations, questions, decisions or {})
        occurred = self._local_now()
        source = self._source(pending.id, pending.actor, occurred)
        if operations:
            records = tuple(await self._store.list(limit=None, scopes=None))
            operations = validate_operations(operations, records, source)
        event = PageEditEvent(
            id=uuid4().hex,
            occurred_at=_milliseconds(occurred),
            sequence=self._next_sequence(),
            actor=pending.actor,
            origin="synthesis",
            path=pending.path,
            base_revision=pending.base_revision,
            result_revision=pending.result_revision,
            patch=pending.patch,
            operations=tuple(AppliedPageOperation.from_operation(operation, (source,)) for operation in operations),
            reconciliation="applied",
            analysis=pending.analysis,
            reconciles_event_id=pending.id,
        )
        planned = self._store.plan_operations(operations, source, batch_key=f"page-edit-retry:{pending.id}") if operations else {}
        event_rel, event_bytes = self._event_file(event)
        caller = self._store._validate_caller_files(
            {event_rel: event_bytes},
            {event_rel: CanonicalFileRole.EVENT},
        )
        files = dict(planned)
        files.update(caller)
        self._commit(files)
        return event

    def history(self, *, path: str | None = None) -> tuple[PageEditEvent, ...]:
        events: list[PageEditEvent] = []
        for rel in self._resources.page_edit_event_resources():
            raw = self._resources.read_resource_bytes(rel, page_edit_internal=True)
            events.extend(parse_page_edit_events(raw))
        if path is not None:
            events = [event for event in events if event.path == path]
        return tuple(sorted(events, key=lambda event: (_parse_time(event.occurred_at), event.sequence)))

    async def _reconcile(self, analysis: PageEditAnalysis):
        call = getattr(self._reconciler, "reconcile_page_edit", self._reconciler)
        try:
            result = call(analysis)
            return await result if inspect.isawaitable(result) else result
        except Exception:
            _logger.warning("page edit reconciliation unavailable", exc_info=True)
            return None

    def _resolve(
        self,
        operations: tuple[RecordOperation, ...],
        questions: tuple[PageEditQuestion, ...],
        decisions: dict[str, PageEditDecision | str],
    ) -> tuple[RecordOperation, ...]:
        by_index = {question.operation_index: question for question in questions}
        resolved: list[RecordOperation] = []
        for index, operation in enumerate(operations):
            if operation.op != "ASK":
                resolved.append(operation)
                continue
            question = by_index[index]
            if question.id not in decisions:
                raise ValueError(f"decision required for {question.id}")
            raw = decisions[question.id]
            decision = PageEditDecision(choice=raw) if isinstance(raw, str) else raw
            if decision.choice == "Note only":
                resolved.append(RecordOperation.noop())
            else:
                if not decision.target_ids:
                    raise ValueError("Forget memory decision requires target ids")
                resolved.append(RecordOperation.retract(*decision.target_ids))
        return tuple(resolved)

    def _event_file(self, event: PageEditEvent) -> tuple[Path, bytes]:
        day = datetime.fromisoformat(event.occurred_at).date().isoformat()
        rel = Path("raw") / "events" / f"{day}.md"
        try:
            existing = self._resources.read_resource_bytes(rel.as_posix(), page_edit_internal=True)
        except FileNotFoundError:
            existing = f"# Page edit events {day}\n\n".encode()
        return rel, existing + render_page_edit_event(event).encode()

    def _commit(self, files: dict[Path, bytes]) -> None:
        try:
            self._store._journal.commit(files)
        except Exception:
            self._store._journal.recover(prefer_rollback=True)
            self._store._reload_canonical_state()
            raise
        self._store._reload_canonical_state()
        self._store._notify_post_canonical_commit()

    def _load_preview(self, preview_id: str) -> dict:
        rel = self._preview_rel(preview_id)
        try:
            raw = self._resources.read_resource_bytes(rel, page_edit_internal=True)
        except FileNotFoundError as exc:
            raise ValueError("page edit preview not found") from exc
        envelope = json.loads(raw)
        expires = datetime.fromisoformat(envelope["expires_at"])
        if self._local_now().astimezone(UTC) > expires.astimezone(UTC):
            self._resources.delete_page_edit_preview(rel)
            raise PreviewExpiredError(preview_id)
        return envelope

    def _next_sequence(self) -> int:
        return max((event.sequence for event in self.history()), default=0) + 1

    def _source(self, event_id: str, actor: str, occurred: datetime) -> SourceRef:
        timestamp = _milliseconds(occurred)
        return SourceRef(
            "page_edit",
            f"page_edit:{event_id}",
            captured_at=timestamp,
            occurred_at=timestamp,
            time_precision="millisecond",
            role=actor,
        )

    def _local_now(self) -> datetime:
        value = self._now()
        if value.tzinfo is None:
            raise ValueError("page edit clock must return an offset-aware datetime")
        return value.astimezone(self._timezone) if self._timezone is not None else value

    @staticmethod
    def _preview_rel(preview_id: str) -> str:
        if not preview_id or any(char not in "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-_" for char in preview_id):
            raise ValueError("invalid page edit preview id")
        return f".ntrp/maintenance/page-edit-previews/{preview_id}.json"

    @staticmethod
    def _validate_page_path(path: str) -> str:
        safe = Path(path)
        if safe.is_absolute() or not safe.parts or any(part in {"", ".", ".."} for part in safe.parts):
            raise FileNotFoundError(path)
        if safe.parts[0] in {"raw", ".ntrp", ".index", ".maintenance"} or safe.suffix.casefold() != ".md":
            raise FileNotFoundError(path)
        return safe.as_posix()


def _milliseconds(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds")


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


def _markdown_blocks(content: bytes) -> tuple[str, ...]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("memory pages must be UTF-8") from exc
    blocks: list[str] = []
    current: list[str] = []
    in_frontmatter = text.startswith("---\n")
    in_fence = False
    for index, line in enumerate(text.splitlines()):
        stripped = line.strip()
        if in_frontmatter:
            if index > 0 and stripped == "---":
                in_frontmatter = False
            continue
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
        if not stripped and not in_fence:
            if current:
                block = "\n".join(current).strip()
                if block:
                    blocks.append(block)
                current = []
            continue
        if stripped.startswith("#") and stripped.lstrip("#").startswith(" "):
            if current:
                blocks.append("\n".join(current).strip())
                current = []
            continue
        if stripped.startswith("<!--") and stripped.endswith("-->"):
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current).strip())
    return tuple(blocks)


def _analyze(path: str, base: bytes, result: bytes, patch: str) -> PageEditAnalysis:
    before = _markdown_blocks(base)
    after = _markdown_blocks(result)
    matcher = difflib.SequenceMatcher(a=before, b=after, autojunk=False)
    changed_before: list[str] = []
    changed_after: list[str] = []
    before_context: set[int] = set()
    after_context: set[int] = set()
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        changed_before.extend(before[i1:i2])
        changed_after.extend(after[j1:j2])
        before_context.update(range(max(0, i1 - 1), min(len(before), i2 + 1)))
        after_context.update(range(max(0, j1 - 1), min(len(after), j2 + 1)))
    return PageEditAnalysis(
        path=path,
        before=tuple(before[index] for index in sorted(before_context)),
        after=tuple(after[index] for index in sorted(after_context)),
        changed_before=tuple(changed_before),
        changed_after=tuple(changed_after),
        patch=patch,
    )


__all__ = [
    "PageEditService",
    "PreviewExpiredError",
    "ReconciliationPendingError",
    "StalePageRevisionError",
]
