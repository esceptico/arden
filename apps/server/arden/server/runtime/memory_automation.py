import asyncio
from collections.abc import Mapping
from typing import Protocol

from arden.config import Config
from arden.constants import BUILTIN_MEMORY_RETENTION_ID
from arden.llm.router import get_completion_client
from arden.memory.facts.capture.runner import CaptureTurn, FactCapture
from arden.memory.facts.capture.store import SessionConsumerWatermarkStore
from arden.memory.facts.completion_renderer import CompletionFactSynthesisRenderer
from arden.memory.facts.consumer_store import FactConsumerStore
from arden.memory.facts.index import FactIndexProjection
from arden.memory.facts.ledger import FactLedger
from arden.memory.facts.maintenance.runner import FactMaintenance, FactMaintenanceReviewer
from arden.memory.facts.service import FactService
from arden.memory.facts.synthesis import CONSUMER_ID as FACT_SYNTHESIS_CONSUMER_ID
from arden.memory.facts.synthesis import FactSynthesis
from arden.revisions import CollectionReport
from arden.server.runtime.knowledge import KnowledgeRuntime
from arden.server.stores import Stores
from arden.wiki.maintenance.runner import WikiMaintenance, WikiMaintenanceReviewer
from arden.wiki.maintenance.store import WikiMaintenanceStore
from arden.wiki.service import WikiService


class MemoryAutomationHost(Protocol):
    config: Config
    knowledge: KnowledgeRuntime
    stores: Stores | None
    fact_service: FactService | None
    fact_index_projection: FactIndexProjection | None
    wiki_service: WikiService | None
    _fact_ledger: FactLedger | None
    _fact_consumer_store: FactConsumerStore | None
    _session_watermark_store: SessionConsumerWatermarkStore | None
    _wiki_maintenance_store: WikiMaintenanceStore | None


class MemoryAutomationRuntime:
    """Build canonical memory workers from initialized runtime services."""

    def __init__(self, host: MemoryAutomationHost) -> None:
        self._host = host

    async def record_retention_checkpoint(self) -> None:
        host = self._host
        if host._fact_ledger is None or host.fact_service is None or host._fact_consumer_store is None:
            return
        revision, evaluated_at, due_reviews = await asyncio.to_thread(host._fact_ledger.due_review_snapshot)
        if due_reviews or await host.fact_service.revision() != revision:
            return
        await host._fact_consumer_store.record_retention_checkpoint(
            BUILTIN_MEMORY_RETENTION_ID,
            revision=revision,
            evaluated_at=evaluated_at,
        )

    def fact_synthesis(self) -> FactSynthesis | None:
        host = self._host
        model = host.config.memory_model
        if host._fact_ledger is None or host._fact_consumer_store is None or host.wiki_service is None or not model:
            return None
        return FactSynthesis(
            host._fact_ledger,
            host._fact_consumer_store,
            host.wiki_service,
            CompletionFactSynthesisRenderer(
                get_completion_client(model),
                model,
                reasoning_effort=host.knowledge._memory_reasoning_effort(model),
            ),
        )

    def fact_capture(self) -> FactCapture | None:
        host = self._host
        if (
            host.fact_service is None
            or host._session_watermark_store is None
            or host.stores is None
            or not host.config.memory_model
        ):
            return None
        sessions = host.stores.sessions.store

        async def eligible_sessions() -> list[tuple[str, int]]:
            return await sessions.list_capture_eligible_sessions()

        async def messages_after(session_id: str, after_seq: int, limit: int) -> list[CaptureTurn]:
            rows = await sessions.list_transcript_messages_after(session_id, after_seq, limit)
            return [_capture_turn(row) for row in rows]

        async def latest_messages(session_id: str, limit: int) -> list[CaptureTurn]:
            rows = await sessions.list_latest_transcript_messages(session_id, limit)
            return [_capture_turn(row) for row in rows]

        return FactCapture(
            watermarks=host._session_watermark_store,
            facts=host.fact_service,
            eligible_sessions=eligible_sessions,
            messages_after=messages_after,
            latest_messages=latest_messages,
        )

    def fact_maintenance(self, reviewer: FactMaintenanceReviewer) -> FactMaintenance | None:
        host = self._host
        model = host.config.memory_model
        if (
            host.fact_service is None
            or host._fact_consumer_store is None
            or host.wiki_service is None
            or host._wiki_maintenance_store is None
            or not model
        ):
            return None
        return FactMaintenance(
            host.fact_service,
            host._fact_consumer_store,
            reviewer,
            wiki=host.wiki_service,
            candidate_provider=host.fact_index_projection,
        )

    def wiki_maintenance(self, reviewer: WikiMaintenanceReviewer) -> WikiMaintenance | None:
        host = self._host
        if host._wiki_maintenance_store is None or host.wiki_service is None or not host.config.memory_model:
            return None
        return WikiMaintenance(host._wiki_maintenance_store, host.wiki_service, reviewer)

    async def synthesis_is_current(self) -> bool:
        host = self._host
        if host.fact_service is None or host._fact_consumer_store is None:
            return False
        before = await host.fact_service.revision()
        watermark = await host._fact_consumer_store.get(FACT_SYNTHESIS_CONSUMER_ID)
        after = await host.fact_service.revision()
        if before != after:
            return False
        if before is None:
            return watermark is None
        return watermark is not None and watermark.revision == before

    def collect_managed_history(self) -> tuple[CollectionReport, CollectionReport]:
        host = self._host
        if host._fact_ledger is None or host.wiki_service is None:
            raise RuntimeError("Managed-history collection requires canonical facts and wiki")
        reports: list[CollectionReport] = []
        errors: list[Exception] = []
        failed: list[str] = []
        for name, collect in (
            ("facts", host._fact_ledger.collect_history),
            ("wiki", host.wiki_service.repository.collect),
        ):
            try:
                reports.append(collect())
            except Exception as error:
                failed.append(name)
                errors.append(error)
        if errors:
            raise ExceptionGroup(f"Managed-history collection failed: {', '.join(failed)}", errors)
        facts, wiki = reports
        return facts, wiki


def _capture_turn(row: Mapping[str, object]) -> CaptureTurn:
    seq = row.get("seq")
    role = row.get("role")
    text = row.get("text")
    message_id = row.get("message_id")
    created_at = row.get("created_at")
    if isinstance(seq, bool) or not isinstance(seq, int):
        raise TypeError("Capture turn seq must be an integer")
    for name, value in (
        ("role", role),
        ("text", text),
        ("message_id", message_id),
        ("created_at", created_at),
    ):
        if not isinstance(value, str):
            raise TypeError(f"Capture turn {name} must be a string")
    return CaptureTurn(seq=seq, role=role, text=text, message_id=message_id, created_at=created_at)
