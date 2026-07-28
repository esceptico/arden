"""Owned background worker for durable wiki-edit curation."""

import asyncio
from datetime import UTC, datetime, timedelta

from arden.logging import get_logger
from arden.memory.facts.service import FactPrincipal, FactService
from arden.wiki.curation.engine import WikiEditCurator
from arden.wiki.curation.queue import (
    WikiEditCuratorJob,
    WikiEditCuratorJobOutcome,
    WikiEditCuratorQueueStore,
)

SYSTEM_OWNER_ID = "system:wiki-edit-curator"
_MAX_ERROR_LENGTH = 4_000
_logger = get_logger(__name__)


class WikiEditCuratorWorker:
    """Drain explicit user edits, immediately committing authoritative plans."""

    def __init__(
        self,
        store: WikiEditCuratorQueueStore,
        curator: WikiEditCurator,
        facts: FactService,
        *,
        poll_interval: float = 1.0,
        retry_base_seconds: float = 1.0,
        retry_max_seconds: float = 300.0,
    ) -> None:
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        if retry_base_seconds < 0 or retry_max_seconds < retry_base_seconds:
            raise ValueError("retry delays must be nonnegative and ordered")
        self._store = store
        self._curator = curator
        self._facts = facts
        self._poll_interval = poll_interval
        self._retry_base_seconds = retry_base_seconds
        self._retry_max_seconds = retry_max_seconds
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        """Start one owned task; existing durable jobs are drained first."""

        if self.is_running:
            return
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        """Cancel and await the owned task, releasing any claimed job."""

        task = self._task
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        finally:
            self._task = None

    async def enqueue_user_commit(self, commit_id: str) -> bool:
        """Cheap durable ingress for the exact commit returned by a user edit."""

        inserted = await self._store.enqueue(commit_id)
        self._wake.set()
        return inserted

    async def process_once(self) -> bool:
        """Process at most one ready job; useful for deterministic ownership tests."""

        job = await self._store.claim_next()
        if job is None:
            return False
        try:
            await self._process(job)
        except asyncio.CancelledError:
            await asyncio.shield(self._store.release(job.commit_id))
            raise
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}".replace("\0", "\\0")[:_MAX_ERROR_LENGTH]
            retry_at = datetime.now(UTC) + timedelta(seconds=self._retry_delay(job.attempts))
            await self._store.retry(job.commit_id, error, available_at=retry_at)
            _logger.warning(
                "Wiki edit curator job failed; retrying",
                commit_id=job.commit_id,
                attempts=job.attempts,
                exc_info=True,
            )
        return True

    async def _process(self, job: WikiEditCuratorJob) -> None:
        scopes = await self._facts.known_scopes()
        principal = FactPrincipal(SYSTEM_OWNER_ID, scopes, scopes)
        if job.plan_id is not None:
            await self._facts.commit(principal, job.plan_id)
            await self._store.complete(
                job.commit_id,
                WikiEditCuratorJobOutcome.COMMITTED,
                plan_id=job.plan_id,
            )
            return

        result = await self._curator.reconcile(job.commit_id, principal)
        if result.outcome == "ignored":
            await self._store.complete(job.commit_id, WikiEditCuratorJobOutcome.IGNORED)
            return
        if result.outcome == "no_change":
            await self._store.complete(job.commit_id, WikiEditCuratorJobOutcome.NO_CHANGE)
            return
        if result.outcome != "planned" or result.plan is None:
            raise RuntimeError("wiki edit curator returned an invalid reconciliation result")

        plan_id = result.plan.plan_id
        await self._store.attach_plan(job.commit_id, plan_id)
        await self._facts.commit(principal, plan_id)
        await self._store.complete(
            job.commit_id,
            WikiEditCuratorJobOutcome.COMMITTED,
            plan_id=plan_id,
        )

    def _retry_delay(self, attempts: int) -> float:
        exponent = min(max(attempts - 1, 0), 30)
        delay = self._retry_base_seconds * (2**exponent)
        return min(delay, self._retry_max_seconds)

    async def _loop(self) -> None:
        needs_recovery = True
        while True:
            self._wake.clear()
            try:
                if needs_recovery:
                    await self._store.release_all_running()
                    needs_recovery = False
                processed = await self.process_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                needs_recovery = True
                _logger.exception("Wiki edit curator worker loop failed")
                processed = False
            if processed:
                continue
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=self._poll_interval)
            except TimeoutError:
                pass
