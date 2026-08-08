import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol

from arden.areas.agent import CUSTODIAN_ACTOR_PREFIX
from arden.automation.builtins import BUILTINS
from arden.constants import (
    BUILTIN_MEMORY_CONSOLIDATE_ID,
    BUILTIN_MEMORY_RETENTION_ID,
    BUILTIN_MEMORY_SYNTHESIZE_ID,
    BUILTIN_WIKI_MAINTENANCE_ID,
)
from arden.logging import get_logger
from arden.memory.facts.consumer_store import FactConsumerStore
from arden.memory.facts.ledger import FactLedger
from arden.memory.facts.maintenance.runner import CONSUMER_ID as FACT_MAINTENANCE_CONSUMER_ID
from arden.memory.facts.service import FactService
from arden.memory.facts.synthesis import CONSUMER_ID as FACT_SYNTHESIS_CONSUMER_ID
from arden.revisions import RevisionConflictError
from arden.server.runtime.automation import AutomationRuntime
from arden.server.stores import Stores
from arden.server.wiki_health import dangling_fact_citation_issues
from arden.wiki.constants import (
    AUTOMATIONS_PATH_PREFIX,
    README_FILENAME,
    WIKI_HEALTH_ORIGIN,
    WIKI_MAINTENANCE_ACTOR,
)
from arden.wiki.context import WikiPageIndexProjection
from arden.wiki.health import (
    WikiHealthIndex,
    WikiHealthInput,
    WikiHealthIssue,
    WikiHealthIssueCode,
    WikiHealthIssueOwner,
    WikiHealthProjector,
    WikiHealthWorker,
)
from arden.wiki.maintenance.store import WikiMaintenanceStore
from arden.wiki.models import WikiChangesReport
from arden.wiki.service import WIKI_RENAME_ORIGIN, WikiService

_logger = get_logger(__name__)
_HEALTH_PHASE_IDS = frozenset(
    {
        BUILTIN_MEMORY_CONSOLIDATE_ID,
        BUILTIN_MEMORY_RETENTION_ID,
        BUILTIN_MEMORY_SYNTHESIZE_ID,
        BUILTIN_WIKI_MAINTENANCE_ID,
    }
)
_HEALTH_PHASES = tuple(spec for spec in BUILTINS if spec.task_id in _HEALTH_PHASE_IDS)


class WikiProjectionHost(Protocol):
    stores: Stores | None
    automation: AutomationRuntime | None
    wiki_service: WikiService | None
    wiki_page_projection: WikiPageIndexProjection | None
    fact_service: FactService | None
    _fact_ledger: FactLedger | None
    _fact_consumer_store: FactConsumerStore | None
    _wiki_maintenance_store: WikiMaintenanceStore | None

    async def project_wiki_health(self) -> str | None: ...

    async def project_wiki_state(self) -> None: ...


class WikiProjectionRuntime:
    """Own derived wiki health, search, and automation projection flow."""

    def __init__(self, host: WikiProjectionHost) -> None:
        self._host = host
        self._health_lock = asyncio.Lock()
        self._projection_lock = asyncio.Lock()
        self._memory_event_lock = asyncio.Lock()
        self._memory_event_head: str | None = None
        self.head: str | None = None

    async def after_automation_finished(
        self,
        task_id: str,
        success: bool,
        record_retention_checkpoint: Callable[[], Awaitable[None]],
    ) -> None:
        wiki = self._host.wiki_service
        wiki_changed = wiki is not None and wiki.repository.head != self.head
        if task_id not in _HEALTH_PHASE_IDS and not wiki_changed:
            return
        if success and task_id == BUILTIN_MEMORY_RETENTION_ID:
            await record_retention_checkpoint()
        if wiki_changed:
            await self._host.project_wiki_state()
        else:
            await self._host.project_wiki_health()

    async def project_health(self) -> str | None:
        host = self._host
        if host.wiki_service is None:
            return None
        if (
            host._fact_ledger is None
            or host.fact_service is None
            or host._fact_consumer_store is None
            or host._wiki_maintenance_store is None
            or host.wiki_page_projection is None
            or host.stores is None
        ):
            raise RuntimeError("Wiki health projection dependencies are incomplete")

        async with self._health_lock:
            for attempt in range(2):
                try:
                    fact_revision, _evaluated_at, due_reviews = await asyncio.to_thread(
                        host._fact_ledger.due_review_snapshot
                    )
                    fact_maintenance = await host._fact_consumer_store.get(FACT_MAINTENANCE_CONSUMER_ID)
                    synthesis = await host._fact_consumer_store.get(FACT_SYNTHESIS_CONSUMER_ID)
                    retention = await host._fact_consumer_store.get_retention_checkpoint(BUILTIN_MEMORY_RETENTION_ID)
                    maintenance = await host._wiki_maintenance_store.get_watermark()
                    completed_runs = await host.stores.automations.latest_completed_runs(
                        tuple(spec.task_id for spec in _HEALTH_PHASES)
                    )
                    report = await asyncio.to_thread(host.wiki_service.changes_since, None, include_diffs=False)
                    citation_issues = await asyncio.to_thread(
                        dangling_fact_citation_issues,
                        host._fact_ledger,
                        report,
                    )
                    if await host.fact_service.revision() != fact_revision:
                        if attempt == 0:
                            continue
                        return self._superseded("fact ledger changed while reading it")

                    observed = _semantic_revision(report)
                    maintenance_revision, maintenance_revision_known = _semantic_revision_at(
                        report,
                        None if maintenance is None else maintenance.revision,
                    )
                    index_state = host.wiki_page_projection.last_state
                    index_revision, index_revision_known = _semantic_revision_at(report, index_state.wiki_head)
                    index_status = index_state.status
                    index_detail = index_state.detail
                    if not index_revision_known:
                        index_status = "error"
                        index_detail = "indexed wiki revision is not reachable from the current history"
                    if not maintenance_revision_known:
                        maintenance_revision = None

                    workers_by_id = {
                        BUILTIN_MEMORY_CONSOLIDATE_ID: WikiHealthWorker(
                            "Memory Maintenance",
                            completed_runs.get(BUILTIN_MEMORY_CONSOLIDATE_ID),
                            None if fact_maintenance is None else fact_maintenance.revision,
                            fact_revision,
                        ),
                        BUILTIN_MEMORY_RETENTION_ID: WikiHealthWorker(
                            "Memory Retention",
                            completed_runs.get(BUILTIN_MEMORY_RETENTION_ID),
                            None if retention is None else retention.revision,
                            fact_revision,
                            None if retention is None else retention.revision == fact_revision and not due_reviews,
                        ),
                        BUILTIN_MEMORY_SYNTHESIZE_ID: WikiHealthWorker(
                            "Memory Synthesis",
                            completed_runs.get(BUILTIN_MEMORY_SYNTHESIZE_ID),
                            None if synthesis is None else synthesis.revision,
                            fact_revision,
                        ),
                        BUILTIN_WIKI_MAINTENANCE_ID: WikiHealthWorker(
                            WIKI_MAINTENANCE_ACTOR,
                            completed_runs.get(BUILTIN_WIKI_MAINTENANCE_ID),
                            maintenance_revision,
                            observed,
                        ),
                    }
                    value = WikiHealthInput(
                        fact_ledger_revision=fact_revision,
                        wiki=report,
                        workers=tuple(workers_by_id[spec.task_id] for spec in _HEALTH_PHASES),
                        index=WikiHealthIndex(index_revision, index_status, index_detail),
                        issues=(
                            *(
                                WikiHealthIssue(
                                    WikiHealthIssueCode.FACT_REVIEW_DUE,
                                    item.fact.fact_id,
                                    f"review due at {item.due_at.isoformat()}",
                                    WikiHealthIssueOwner.RETENTION,
                                )
                                for item in due_reviews
                            ),
                            *citation_issues,
                        ),
                    )
                    result = await asyncio.to_thread(WikiHealthProjector(host.wiki_service).project, value)
                    if await host.fact_service.revision() != fact_revision:
                        if attempt == 0:
                            continue
                        return self._superseded("fact ledger changed while publishing")
                    return report.through_revision if result.commit is None else result.commit.commit_id
                except RevisionConflictError as error:
                    if attempt == 0:
                        continue
                    return self._superseded(str(error))
        return None

    async def enqueue_after_commit(self) -> bool:
        host = self._host
        if host.stores is None or host.wiki_service is None:
            raise RuntimeError("Managed wiki projection retry is unavailable")
        revision = host.wiki_service.repository.head
        if revision is None:
            raise RuntimeError("Committed wiki projection has no revision")
        await host.stores.outbox.enqueue_wiki_projection(revision)
        await self._emit_memory_change_through(revision)
        return True

    async def enqueue_startup(self) -> bool:
        host = self._host
        if host.stores is None or host.wiki_service is None:
            return False
        revision = host.wiki_service.repository.head
        if revision is None:
            return False
        return await host.stores.outbox.enqueue_wiki_projection(revision)

    async def project_change(self) -> None:
        host = self._host
        if host.wiki_service is None or host.wiki_page_projection is None or host._wiki_maintenance_store is None:
            raise RuntimeError("Managed wiki projection is unavailable")
        async with self._projection_lock:
            previous_head = self.head
            if host.wiki_service.repository.head == previous_head:
                return
            final_head = None
            for _attempt in range(3):
                await host.wiki_page_projection.sync(notify_state_change=False)
                await host.project_wiki_health()
                await host.wiki_page_projection.sync(notify_state_change=False)
                final_head = await host.project_wiki_health()
                if host.wiki_service.repository.head == final_head:
                    break
            else:
                _logger.info(
                    "Wiki projection superseded by concurrent writes; deferring to the queued projection",
                    previous_head=previous_head,
                    head=host.wiki_service.repository.head,
                )
                return

            if final_head == previous_head:
                return
            commits = await asyncio.to_thread(
                host.wiki_service.repository.history,
                start=final_head,
                stop_before=previous_head,
            )
            paths, source_areas_by_path, common_page_created = _changed_paths(commits)
            if paths and host.automation is not None:
                if final_head is None:
                    raise RuntimeError("Wiki projection produced changes without a revision")
                await self._emit_memory_change_through(final_head)
                await host.automation.notify_wiki_dependents(
                    sorted(paths),
                    source_areas_by_path=source_areas_by_path,
                )
                if common_page_created:
                    await host.automation.request_fact_synthesis()
            if final_head is not None:
                await host._wiki_maintenance_store.record_projection_revision(
                    expected_revision=previous_head,
                    revision=final_head,
                )
            self.head = final_head

    async def _emit_memory_change_through(self, revision: str) -> None:
        host = self._host
        if host.wiki_service is None or host.automation is None:
            return
        async with self._memory_event_lock:
            previous_head = self._memory_event_head if self._memory_event_head is not None else self.head
            if revision == previous_head:
                return
            commits = await asyncio.to_thread(
                host.wiki_service.repository.history,
                start=revision,
                stop_before=previous_head,
            )
            paths, _source_areas_by_path, _common_page_created = _changed_paths(commits)
            if paths:
                await host.automation.emit_memory_changed(sorted(paths), revision)
            self._memory_event_head = revision

    @staticmethod
    def _superseded(detail: str) -> None:
        _logger.info("Wiki health projection superseded by a concurrent write", detail=detail)
        return None


def _semantic_revision(report: WikiChangesReport) -> str | None:
    return next(
        (commit.commit_id for commit in reversed(report.commits) if commit.origin != WIKI_HEALTH_ORIGIN),
        None,
    )


def _semantic_revision_at(report: WikiChangesReport, raw_revision: str | None) -> tuple[str | None, bool]:
    if raw_revision is None:
        return None, True
    semantic: str | None = None
    for commit in report.commits:
        if commit.origin != WIKI_HEALTH_ORIGIN:
            semantic = commit.commit_id
        if commit.commit_id == raw_revision:
            return semantic, True
    return None, False


def _changed_paths(commits) -> tuple[set[str], dict[str, set[str]], bool]:
    paths: set[str] = set()
    attributed_paths: set[str] = set()
    source_areas_by_path: dict[str, set[str]] = {}
    common_page_created = False
    for commit in commits:
        if commit.origin == WIKI_HEALTH_ORIGIN:
            continue
        source_area_id = None
        if commit.origin == "area.page" and commit.actor.startswith(CUSTODIAN_ACTOR_PREFIX):
            candidate = commit.actor.removeprefix(CUSTODIAN_ACTOR_PREFIX)
            if candidate and ":" not in candidate:
                source_area_id = candidate
        for change in commit.changes:
            if (
                commit.origin not in {FACT_SYNTHESIS_CONSUMER_ID, WIKI_RENAME_ORIGIN}
                and change.before is None
                and change.after is not None
                and change.after.path.endswith(".md")
                and not change.after.path.startswith(AUTOMATIONS_PATH_PREFIX)
                and change.after.path.rsplit("/", 1)[-1] != README_FILENAME
            ):
                common_page_created = True
            for path in (
                None if change.before is None else change.before.path,
                None if change.after is None else change.after.path,
            ):
                if path is None:
                    continue
                paths.add(path)
                if path in attributed_paths:
                    continue
                attributed_paths.add(path)
                if source_area_id is not None:
                    source_areas_by_path.setdefault(path, set()).add(source_area_id)
    return paths, source_areas_by_path, common_page_created
