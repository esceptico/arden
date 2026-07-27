"""Revision-safe page edits with exact, atomic reconciliation events."""

from __future__ import annotations

import asyncio
import base64
import difflib
import inspect
import json
import logging
from copy import deepcopy
from datetime import UTC, datetime, timedelta, tzinfo
from pathlib import Path
from typing import Literal
from uuid import uuid4
from weakref import WeakValueDictionary
from zoneinfo import ZoneInfo

from arden.memory.artifacts import ArtifactMemoryStore, is_reserved_managed_path
from arden.memory.file_store import CanonicalFileRole, FilePageStore, ObservedFileChange
from arden.memory.journal import JournalConflictError
from arden.memory.merge import synthesis_base_rel
from arden.memory.models import SourceRef
from arden.memory.page_events import (
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
from arden.memory.pages import render_raw
from arden.memory.reconciler import RecordOperation, validate_operations

_logger = logging.getLogger(__name__)
_VAULT_APPLY_LOCKS: WeakValueDictionary[Path, asyncio.Lock] = WeakValueDictionary()


class StalePageRevisionError(ValueError):
    def __init__(
        self,
        path: str,
        *,
        current_content: bytes,
        base_revision: str,
        candidate_revision: str,
        message: str | None = None,
    ) -> None:
        super().__init__(message or path)
        self.path = path
        self.current_content = current_content
        self.current_revision = page_revision(current_content)
        self.base_revision = base_revision
        self.candidate_revision = candidate_revision


class PreviewExpiredError(ValueError):
    pass


class PreviewNotFoundError(ValueError):
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

    @property
    def artifact_store(self) -> ArtifactMemoryStore:
        return self._resources

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
            raise StalePageRevisionError(
                safe_path,
                current_content=base,
                base_revision=base_revision,
                candidate_revision=page_revision(content),
            )
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

    async def create_page(self, *, path: str, actor: str) -> PageEditEvent:
        """Atomically create one EMPTY user page with its audit event."""
        async with self._apply_lock:
            safe_path = self._validate_page_path(path)
            if not actor.strip():
                raise ValueError("page edit actor is required")
            try:
                (self._vault / safe_path).lstat()
            except FileNotFoundError:
                pass
            else:
                raise FileExistsError(safe_path)
            occurred = self._local_now()
            event = PageEditEvent(
                id=uuid4().hex,
                occurred_at=_milliseconds(occurred),
                sequence=self._next_sequence(),
                actor=actor,
                origin="desktop",
                path=safe_path,
                base_revision=page_revision(b""),
                result_revision=page_revision(b""),
                patch=unified_patch(b"", b""),
                operations=(),
                reconciliation="applied",
            )
            event_rel, event_bytes, expected_event = self._event_file(event)
            files = self._store._validate_caller_files(
                {Path(safe_path): b"", event_rel: event_bytes},
                {Path(safe_path): CanonicalFileRole.USER_PAGE, event_rel: CanonicalFileRole.EVENT},
            )
            self._commit(
                files,
                expected_files={Path(safe_path): None, event_rel: expected_event},
                expected_revision=self._store.canonical_revision,
                engine_write=(safe_path, b"", "desktop", event.id),
            )
            return event

    async def ingest_external(self, change: ObservedFileChange) -> PageEditEvent | None:
        """Persist one observed page change before advancing its durable base."""
        if change.origin != "external":
            return None
        async with self._apply_lock:
            return await self._ingest_external(change)

    async def apply_synthesis_merge(
        self,
        *,
        path: str,
        base: bytes | None,
        result: bytes,
        source_revision: str,
        generated_base: bytes | None = None,
        prose_cites: tuple[str, ...] = (),
    ) -> PageEditEvent:
        """Atomically accept one exact generated page merge and its audit event."""
        async with self._apply_lock:
            safe_path = self._validate_page_path(path)
            if self._store.canonical_revision != source_revision:
                raise JournalConflictError("synthesis source revision is stale")
            try:
                current = self._resources.read_resource_bytes(safe_path)
            except FileNotFoundError:
                current = None
            if current != base:
                raise StalePageRevisionError(
                    safe_path,
                    current_content=current or b"",
                    base_revision=page_revision(base or b""),
                    candidate_revision=page_revision(result),
                )
            base_bytes = base or b""
            occurred = self._local_now()
            event = PageEditEvent(
                event_type="SYNTHESIS_MERGE",
                id=uuid4().hex,
                occurred_at=_milliseconds(occurred),
                sequence=self._next_sequence(),
                actor="synthesis",
                origin="synthesis",
                path=safe_path,
                base_revision=page_revision(base_bytes),
                result_revision=page_revision(result),
                patch=unified_patch(base_bytes, result),
                operations=(),
                reconciliation="applied",
                source_canonical_revision=source_revision,
            )
            event_rel, event_bytes, expected_event = self._event_file(event)
            caller = self._store._validate_caller_files(
                {Path(safe_path): result, event_rel: event_bytes},
                {Path(safe_path): CanonicalFileRole.USER_PAGE, event_rel: CanonicalFileRole.EVENT},
            )
            page_path = self._vault / safe_path
            raw_rel = Path("raw") / safe_path
            raw_before = self._store._safe_read_bytes(self._vault / raw_rel)
            page = deepcopy(self._store._pages[page_path])
            page.frontmatter["generated_from_revision"] = source_revision
            page.frontmatter.pop("prose_synced", None)
            page.frontmatter["prose_cites"] = sorted(prose_cites)
            files = {raw_rel: render_raw(page).encode(), **caller}
            self._commit(
                files,
                expected_files={Path(safe_path): base, raw_rel: raw_before, event_rel: expected_event},
                expected_revision=source_revision,
                engine_write=(safe_path, result, "synthesis", event.id),
                projection=True,
            )
            if generated_base is not None:
                try:
                    self._resources.write_synthesis_maintenance(
                        synthesis_base_rel(safe_path, source_revision),
                        generated_base,
                    )
                except Exception:
                    _logger.warning("accepted synthesis base persistence failed", exc_info=True)
            return event

    async def advance_synthesis_checkpoint(
        self,
        *,
        path: str,
        current: bytes,
        source_revision: str,
        generated_base: bytes,
        prose_cites: tuple[str, ...] = (),
    ) -> None:
        """Advance freshness without emitting an empty synthesis event."""
        async with self._apply_lock:
            safe_path = self._validate_page_path(path)
            if self._store.canonical_revision != source_revision:
                raise JournalConflictError("synthesis source revision is stale")
            if self._resources.read_resource_bytes(safe_path) != current:
                raise StalePageRevisionError(
                    safe_path,
                    current_content=self._resources.read_resource_bytes(safe_path),
                    base_revision=page_revision(current),
                    candidate_revision=page_revision(current),
                )
            page_path = self._vault / safe_path
            raw_rel = Path("raw") / safe_path
            raw_before = self._store._safe_read_bytes(self._vault / raw_rel)
            page = deepcopy(self._store._pages[page_path])
            page.frontmatter["generated_from_revision"] = source_revision
            page.frontmatter.pop("prose_synced", None)
            page.frontmatter["prose_cites"] = sorted(prose_cites)
            raw_result = render_raw(page).encode()
            if raw_result != raw_before:
                self._commit(
                    {raw_rel: raw_result},
                    expected_files={Path(safe_path): current, raw_rel: raw_before},
                    expected_revision=source_revision,
                    projection=True,
                )
            try:
                self._resources.write_synthesis_maintenance(
                    synthesis_base_rel(safe_path, source_revision),
                    generated_base,
                )
            except Exception:
                _logger.warning("synthesis checkpoint base persistence failed", exc_info=True)

    async def _ingest_external(self, change: ObservedFileChange) -> PageEditEvent | None:
        safe_path = self._validate_page_path(change.path)
        before = change.before or b""
        after = change.after or b""
        if page_revision(before) != change.base_revision or page_revision(after) != change.result_revision:
            raise ValueError("observed page change revisions do not match its bytes")
        patch = unified_patch(before, after)
        path_history = self.history(path=safe_path)
        existing = next(
            (
                event
                for event in path_history
                if event.origin == "external" and event.observation_id == change.observation_id
            ),
            None,
        )
        if existing is not None:
            self._store.acknowledge_observed_change(change)
            return existing
        try:
            current = self._resources.read_resource_bytes(safe_path)
        except FileNotFoundError:
            current = None
        if current != change.after:
            self._store.abandon_observed_change(change)
            raise StalePageRevisionError(
                safe_path,
                current_content=current or b"",
                base_revision=change.base_revision,
                candidate_revision=change.result_revision,
            )

        occurred = self._local_now()
        event_id = uuid4().hex
        event = PageEditEvent(
            id=event_id,
            occurred_at=_milliseconds(occurred),
            sequence=self._next_sequence(),
            actor="external:filesystem",
            origin="external",
            path=safe_path,
            base_revision=change.base_revision,
            result_revision=change.result_revision,
            patch=patch,
            operations=(),
            reconciliation="applied",
            observation_id=change.observation_id,
        )
        expected_revision = self._store.canonical_revision
        event_rel, event_bytes, expected_event = self._event_file(event)
        caller_roles = {event_rel: CanonicalFileRole.EVENT}
        caller_files = {event_rel: event_bytes}
        files = self._store._validate_caller_files(caller_files, caller_roles)
        try:
            self._commit(
                files,
                expected_files={Path(safe_path): change.after, event_rel: expected_event},
                expected_revision=expected_revision,
            )
        except JournalConflictError as exc:
            self._store.abandon_observed_change(change)
            try:
                latest = self._resources.read_resource_bytes(safe_path)
            except FileNotFoundError:
                latest = b""
            raise StalePageRevisionError(
                safe_path,
                current_content=latest,
                base_revision=change.base_revision,
                candidate_revision=change.result_revision,
                message=str(exc),
            ) from exc
        self._store.acknowledge_observed_change(change)
        return event

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
        expected_revision = self._store.canonical_revision
        envelope = self._load_preview(preview_id)
        preview, candidate, analysis, current = self._validate_persisted_preview(preview_id, envelope)
        unknown_decisions = set(decisions).difference(question.id for question in preview.questions)
        if unknown_decisions:
            raise ValueError(f"invalid decision id: {sorted(unknown_decisions)[0]}")
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
            analysis=analysis,
        )
        planned = (
            self._store.plan_operations(operations, source, batch_key=f"page-edit:{event.id}") if operations else {}
        )
        event_rel, event_bytes, expected_event = self._event_file(event)
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
        try:
            self._commit(
                files,
                expected_files={Path(preview.path): current, event_rel: expected_event},
                expected_revision=expected_revision,
                engine_write=(preview.path, candidate, envelope["origin"], event.id)
                if envelope["origin"] != "external"
                else None,
            )
        except JournalConflictError as exc:
            latest = self._resources.read_resource_bytes(preview.path)
            raise StalePageRevisionError(
                preview.path,
                current_content=latest,
                base_revision=preview.base_revision,
                candidate_revision=preview.result_revision,
                message=str(exc),
            ) from exc
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
        external_review = (
            pending is not None and pending.origin == "external" and pending.reconciliation == "needs_review"
        )
        if pending is None or (pending.reconciliation != "pending" and not external_review) or pending.analysis is None:
            raise ValueError("pending page edit event not found")
        try:
            self._validate_page_path(pending.path)
        except FileNotFoundError as exc:
            raise ValueError("managed wiki pages cannot be retried through page edits") from exc
        applied = next(
            (event for event in events if event.reconciles_event_id == event_id and event.reconciliation == "applied"),
            None,
        )
        if applied is not None:
            return applied
        review = (
            pending
            if external_review
            else next(
                (
                    event
                    for event in events
                    if event.reconciles_event_id == event_id and event.reconciliation == "needs_review"
                ),
                None,
            )
        )
        expected_revision = self._store.canonical_revision
        if review is not None:
            if not decisions:
                return review
            raw_operations = review.review_operations
            questions = review.questions
        else:
            answer = await self._reconcile(pending.analysis)
            if answer is None:
                raise ReconciliationPendingError("page edit analysis remains unavailable")
            raw_operations = tuple(answer)
            questions = tuple(
                PageEditQuestion(id=f"operation:{index}", operation_index=index, question=operation.question or "")
                for index, operation in enumerate(raw_operations)
                if operation.op == "ASK"
            )
            if questions and not decisions:
                return await self._persist_review(
                    pending,
                    raw_operations,
                    questions,
                    expected_revision=expected_revision,
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
            review_event_id=review.id if review is not None else None,
        )
        planned = (
            self._store.plan_operations(operations, source, batch_key=f"page-edit-retry:{pending.id}")
            if operations
            else {}
        )
        if pending.origin == "external":
            planned.pop(Path(pending.path), None)
        event_rel, event_bytes, expected_event = self._event_file(event)
        caller = self._store._validate_caller_files(
            {event_rel: event_bytes},
            {event_rel: CanonicalFileRole.EVENT},
        )
        files = dict(planned)
        files.update(caller)
        self._commit(
            files,
            expected_files={event_rel: expected_event},
            expected_revision=expected_revision,
        )
        return event

    async def _persist_review(
        self,
        pending: PageEditEvent,
        raw_operations: tuple[RecordOperation, ...],
        questions: tuple[PageEditQuestion, ...],
        *,
        expected_revision: str,
    ) -> PageEditEvent:
        occurred = self._local_now()
        source = self._source(pending.id, pending.actor, occurred)
        records = tuple(await self._store.list(limit=None, scopes=None))
        validated = validate_operations(raw_operations, records, source)
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
            operations=(),
            reconciliation="needs_review",
            analysis=pending.analysis,
            reconciles_event_id=pending.id,
            review_operations=validated,
            questions=questions,
        )
        event_rel, event_bytes, expected_event = self._event_file(event)
        caller = self._store._validate_caller_files(
            {event_rel: event_bytes},
            {event_rel: CanonicalFileRole.EVENT},
        )
        self._commit(
            dict(caller),
            expected_files={event_rel: expected_event},
            expected_revision=expected_revision,
        )
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
        unknown_decisions = set(decisions).difference(question.id for question in questions)
        if unknown_decisions:
            raise ValueError(f"invalid decision id: {sorted(unknown_decisions)[0]}")
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
                target_ids = decision.target_ids or operation.target_ids
                if not target_ids:
                    raise ValueError("Forget memory decision requires target ids")
                if not set(target_ids).issubset(operation.target_ids):
                    raise ValueError("Forget memory decision target is outside the reviewed memory")
                resolved.append(RecordOperation.retract(*target_ids))
        return tuple(resolved)

    def _event_file(self, event: PageEditEvent) -> tuple[Path, bytes, bytes | None]:
        day = datetime.fromisoformat(event.occurred_at).date().isoformat()
        rel = Path("raw") / "events" / f"{day}.md"
        try:
            existing = self._resources.read_resource_bytes(rel.as_posix(), page_edit_internal=True)
        except FileNotFoundError:
            expected = None
            existing = f"# Page edit events {day}\n\n".encode()
        else:
            expected = existing
        return rel, existing + render_page_edit_event(event).encode(), expected

    def _commit(
        self,
        files: dict[Path, bytes],
        *,
        expected_files: dict[Path, bytes | None],
        expected_revision: str,
        engine_write: tuple[str, bytes, Literal["desktop", "agent", "synthesis"], str] | None = None,
        projection: bool = False,
    ) -> None:
        if engine_write is not None:
            path, content, origin, event_id = engine_write
            self._store.register_engine_write_intent(
                path,
                content,
                origin=origin,
                event_id=event_id,
            )
        try:
            commit = self._store._journal.commit_projection if projection else self._store._journal.commit
            commit(
                files,
                expected_files=expected_files,
                expected_revision=expected_revision,
            )
        except Exception:
            self._store._journal.recover(prefer_rollback=True)
            self._store._reload_canonical_state()
            raise
        self._store._reload_canonical_state()
        self._store._notify_post_canonical_commit()

    def _validate_persisted_preview(
        self,
        requested_id: str,
        envelope: dict,
    ) -> tuple[PageEditPreview, bytes, PageEditAnalysis, bytes]:
        try:
            preview = PageEditPreview.model_validate(envelope["preview"])
            candidate = base64.b64decode(envelope["content"], validate=True)
            persisted_analysis = PageEditAnalysis.model_validate(envelope["analysis"])
        except Exception as exc:
            raise ValueError("invalid persisted preview payload") from exc
        if preview.id != requested_id:
            raise ValueError("invalid persisted preview id")
        self._validate_page_path(preview.path)
        if page_revision(candidate) != preview.result_revision:
            raise ValueError("invalid persisted preview candidate revision")
        current = self._resources.read_resource_bytes(preview.path)
        if page_revision(current) != preview.base_revision:
            raise StalePageRevisionError(
                preview.path,
                current_content=current,
                base_revision=preview.base_revision,
                candidate_revision=preview.result_revision,
            )
        patch = unified_patch(current, candidate)
        if patch != preview.patch:
            raise ValueError("invalid persisted preview patch")
        analysis = _analyze(preview.path, current, candidate, patch)
        if analysis != persisted_analysis:
            raise ValueError("invalid persisted preview analysis")
        return preview, candidate, analysis, current

    def _load_preview(self, preview_id: str) -> dict:
        rel = self._preview_rel(preview_id)
        try:
            raw = self._resources.read_resource_bytes(rel, page_edit_internal=True)
        except FileNotFoundError as exc:
            raise PreviewNotFoundError(preview_id) from exc
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
        if not preview_id or any(
            char not in "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-_" for char in preview_id
        ):
            raise ValueError("invalid page edit preview id")
        return f".arden/maintenance/page-edit-previews/{preview_id}.json"

    @staticmethod
    def _validate_page_path(path: str) -> str:
        safe = Path(path)
        if safe.is_absolute() or not safe.parts or any(part in {"", ".", ".."} for part in safe.parts):
            raise FileNotFoundError(path)
        if (
            safe.parts[0] in {"raw", ".arden", ".index", ".maintenance"}
            or is_reserved_managed_path(safe)
            or safe.suffix.casefold() != ".md"
        ):
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
    "PreviewNotFoundError",
    "ReconciliationPendingError",
    "StalePageRevisionError",
]
