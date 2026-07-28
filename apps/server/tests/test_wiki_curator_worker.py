from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from arden.memory.facts.service import FactPlanPreview
from arden.wiki.curator import WikiEditCuratorResult
from arden.wiki.curator_queue import (
    WikiEditCuratorJobOutcome,
    WikiEditCuratorJobStatus,
    WikiEditCuratorQueueStore,
)
from arden.wiki.curator_worker import SYSTEM_OWNER_ID, WikiEditCuratorWorker

if TYPE_CHECKING:
    from pathlib import Path

    from arden.memory.facts.service import FactPrincipal

pytestmark = pytest.mark.asyncio
SCOPES = frozenset({("user", None), ("area", "general")})


class _Curator:
    def __init__(self, responses: list[WikiEditCuratorResult | Exception]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, FactPrincipal]] = []

    async def reconcile(self, commit_id: str, principal: FactPrincipal) -> WikiEditCuratorResult:
        self.calls.append((commit_id, principal))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _Facts:
    def __init__(self) -> None:
        self.commit_calls: list[tuple[FactPrincipal, str]] = []

    async def known_scopes(self):
        return SCOPES

    async def commit(self, principal: FactPrincipal, plan_id: str):
        self.commit_calls.append((principal, plan_id))
        return object()


def _commit(character: str) -> str:
    return character * 64


def _result(commit_id: str, outcome: str, *, plan_id: str | None = None) -> WikiEditCuratorResult:
    plan = None if plan_id is None else FactPlanPreview(plan_id, (), {}, {}, "preview")
    return WikiEditCuratorResult(
        commit_id=commit_id,
        outcome=outcome,
        reason=f"{outcome} result",
        plan=plan,
    )


async def _done(store: WikiEditCuratorQueueStore, commit_id: str):
    for _attempt in range(200):
        job = await store.get(commit_id)
        if job is not None and job.status is WikiEditCuratorJobStatus.DONE:
            return job
        await asyncio.sleep(0.01)
    raise AssertionError("wiki edit curator job did not finish")


async def test_queue_enqueue_is_idempotent_and_durable(tmp_path: Path) -> None:
    path = tmp_path / "memory.sqlite"
    store = await WikiEditCuratorQueueStore.open(path)
    commit_id = _commit("a")
    try:
        assert await store.enqueue(commit_id) is True
        assert await store.enqueue(commit_id) is False
    finally:
        await store.close()

    reopened = await WikiEditCuratorQueueStore.open(path)
    try:
        job = await reopened.get(commit_id)
        assert job is not None
        assert job.status is WikiEditCuratorJobStatus.PENDING
        assert job.attempts == 0
    finally:
        await reopened.close()


async def test_worker_drains_startup_and_new_enqueues_with_system_scopes(tmp_path: Path) -> None:
    store = await WikiEditCuratorQueueStore.open(tmp_path / "memory.sqlite")
    startup_commit = _commit("b")
    new_commit = _commit("c")
    curator = _Curator(
        [
            _result(startup_commit, "no_change"),
            _result(new_commit, "ignored"),
        ]
    )
    facts = _Facts()
    worker = WikiEditCuratorWorker(store, curator, facts, poll_interval=30)
    try:
        await store.enqueue(startup_commit)
        worker.start()
        startup = await _done(store, startup_commit)

        assert await worker.enqueue_user_commit(new_commit) is True
        new = await _done(store, new_commit)

        assert startup.outcome is WikiEditCuratorJobOutcome.NO_CHANGE
        assert new.outcome is WikiEditCuratorJobOutcome.IGNORED
        assert all(principal.owner_id == SYSTEM_OWNER_ID for _commit_id, principal in curator.calls)
        assert all(principal.readable_scopes == SCOPES for _commit_id, principal in curator.calls)
        assert all(principal.writable_scopes == SCOPES for _commit_id, principal in curator.calls)
        assert facts.commit_calls == []
    finally:
        await worker.stop()
        await store.close()


async def test_worker_retries_failures_without_losing_job(tmp_path: Path) -> None:
    store = await WikiEditCuratorQueueStore.open(tmp_path / "memory.sqlite")
    commit_id = _commit("d")
    curator = _Curator(
        [
            RuntimeError("review unavailable"),
            _result(commit_id, "no_change"),
        ]
    )
    worker = WikiEditCuratorWorker(
        store,
        curator,
        _Facts(),
        poll_interval=0.01,
        retry_base_seconds=0,
        retry_max_seconds=0,
    )
    try:
        worker.start()
        await worker.enqueue_user_commit(commit_id)
        job = await _done(store, commit_id)

        assert job.outcome is WikiEditCuratorJobOutcome.NO_CHANGE
        assert job.attempts == 2
        assert job.last_error is None
        assert len(curator.calls) == 2
    finally:
        await worker.stop()
        await store.close()


async def test_crash_after_fact_commit_reuses_attached_plan(tmp_path: Path) -> None:
    store = await WikiEditCuratorQueueStore.open(tmp_path / "memory.sqlite")
    commit_id = _commit("e")
    plan_id = "plan-one"
    curator = _Curator([_result(commit_id, "planned", plan_id=plan_id)])
    facts = _Facts()
    worker = WikiEditCuratorWorker(
        store,
        curator,
        facts,
        poll_interval=0.01,
        retry_base_seconds=0,
        retry_max_seconds=0,
    )
    complete = store.complete
    fail_once = True

    async def _crash_after_commit(commit_id, outcome, *, plan_id=None):
        nonlocal fail_once
        if fail_once and outcome is WikiEditCuratorJobOutcome.COMMITTED:
            fail_once = False
            raise RuntimeError("process stopped after fact commit")
        return await complete(commit_id, outcome, plan_id=plan_id)

    store.complete = _crash_after_commit  # type: ignore[method-assign]
    try:
        worker.start()
        await worker.enqueue_user_commit(commit_id)
        job = await _done(store, commit_id)

        assert job.outcome is WikiEditCuratorJobOutcome.COMMITTED
        assert job.plan_id == plan_id
        assert job.attempts == 2
        assert len(curator.calls) == 1
        assert [called_plan for _principal, called_plan in facts.commit_calls] == [plan_id, plan_id]
    finally:
        await worker.stop()
        await store.close()


async def test_stop_releases_in_flight_job_for_next_start(tmp_path: Path) -> None:
    store = await WikiEditCuratorQueueStore.open(tmp_path / "memory.sqlite")
    commit_id = _commit("f")
    entered = asyncio.Event()

    class _BlockingCurator:
        async def reconcile(self, commit_id: str, principal: FactPrincipal) -> WikiEditCuratorResult:
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    worker = WikiEditCuratorWorker(store, _BlockingCurator(), _Facts(), poll_interval=30)
    try:
        await store.enqueue(commit_id)
        worker.start()
        await asyncio.wait_for(entered.wait(), timeout=1)
        await worker.stop()

        job = await store.get(commit_id)
        assert job is not None
        assert job.status is WikiEditCuratorJobStatus.PENDING
        assert job.outcome is None
    finally:
        await worker.stop()
        await store.close()
