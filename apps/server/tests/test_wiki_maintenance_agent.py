from datetime import UTC, datetime

import pytest

from arden.agent import Usage
from arden.automation.builtins import WIKI_MAINTENANCE_PROMPT
from arden.config import Config
from arden.constants import BUILTIN_WIKI_MAINTENANCE_ID
from arden.context.models import SessionState
from arden.operator.runner import RunResult
from arden.revisions.models import ResourceState, ResourceVersion
from arden.server.runtime.core import Runtime
from arden.tools.core.context import BackgroundTaskRegistry, IOBridge, RunContext, ToolContext, ToolExecution
from arden.tools.core.registry import ToolRegistry
from arden.tools.wiki_maintenance import (
    WIKI_MAINTENANCE_SERVICE,
    WikiMaintenanceReviewInput,
    wiki_maintenance_review_tool,
)
from arden.wiki.maintenance.agent import WikiMaintenanceReviewService
from arden.wiki.maintenance.runner import (
    WikiMaintenance,
    WikiMaintenanceConcernDraft,
    WikiMaintenanceDecision,
    WikiMaintenanceError,
    WikiMaintenanceMergeDraft,
    WikiMaintenancePreparedReport,
    WikiMaintenanceResult,
)
from arden.wiki.maintenance.store import WikiMaintenanceReviewConflictError
from arden.wiki.models import WikiPage, WikiPageRecord


def _report() -> WikiMaintenancePreparedReport:
    page = WikiPage(
        page_id="page",
        title="Dex",
        aliases=(),
        lifecycle="active",
        redirect_to=None,
        body=b"User note",
        metadata={},
    )
    record = WikiPageRecord(
        page=page,
        resource=ResourceVersion("page", "dex.md", "a" * 64, ResourceState.ACTIVE, "b" * 64),
        content=b"---\ntitle: Dex\n---\nUser note",
    )
    return WikiMaintenancePreparedReport(
        commit_id="c" * 64,
        base_head="d" * 64,
        evidence_fingerprint="e" * 64,
        replay_fingerprint="f" * 64,
        markdown="# Wiki maintenance review\n\nP001 only",
        page_tokens={"P001": record},
    )


def _merge_report() -> WikiMaintenancePreparedReport:
    canonical = WikiPageRecord(
        page=WikiPage(
            page_id="canonical",
            title="Canonical",
            aliases=(),
            lifecycle="active",
            redirect_to=None,
            body=b"Canonical note",
            metadata={},
        ),
        resource=ResourceVersion("canonical", "canonical.md", "a" * 64, ResourceState.ACTIVE, "b" * 64),
        content=b"---\ntitle: Canonical\n---\nCanonical note",
    )
    loser = WikiPageRecord(
        page=WikiPage(
            page_id="loser",
            title="Legacy",
            aliases=(),
            lifecycle="active",
            redirect_to=None,
            body=b"Legacy note",
            metadata={},
        ),
        resource=ResourceVersion("loser", "legacy.md", "c" * 64, ResourceState.ACTIVE, "d" * 64),
        content=b"---\ntitle: Legacy\n---\nLegacy note",
    )
    return WikiMaintenancePreparedReport(
        commit_id="c" * 64,
        base_head="d" * 64,
        evidence_fingerprint="e" * 64,
        replay_fingerprint="f" * 64,
        markdown="# Wiki maintenance review\n\nP001 and P002",
        page_tokens={"P001": canonical, "P002": loser},
    )


class _Maintenance:
    def __init__(self, reviewer, report: WikiMaintenancePreparedReport) -> None:
        self.reviewer = reviewer
        self.report = report

    def validate_prepared_decision(self, report, decision) -> None:
        if report != self.report:
            raise WikiMaintenanceError("unexpected report")
        if decision.outcome == "updates" and decision.updates[0].page_token != "P001":
            raise WikiMaintenanceError("maintenance output named an unknown page token")

    async def run(self) -> WikiMaintenanceResult:
        await self.reviewer(self.report)
        return WikiMaintenanceResult("a" * 64, "a" * 64, reviewed_commits=1, complete=True)


class _MergeValidationMaintenance(WikiMaintenance):
    def __init__(self, reviewer, report: WikiMaintenancePreparedReport) -> None:
        super().__init__(object(), object(), reviewer)
        self.report = report

    async def run(self) -> WikiMaintenanceResult:
        await self._reviewer(self.report)
        return WikiMaintenanceResult("a" * 64, "a" * 64, reviewed_commits=1, complete=True)


def _execution(review: WikiMaintenanceReviewService, automation_id: str | None) -> ToolExecution:
    context = ToolContext(
        session_state=SessionState(session_id="session", started_at=datetime(2026, 7, 29, tzinfo=UTC)),
        registry=ToolRegistry(),
        run=RunContext(run_id="run", automation_id=automation_id),
        io=IOBridge(),
        services={WIKI_MAINTENANCE_SERVICE: review},
        area=None,
        background_tasks=BackgroundTaskRegistry(session_id="session"),
    )
    return ToolExecution(tool_id="tool", tool_name="wiki_maintenance_review", ctx=context)


@pytest.mark.asyncio
async def test_agent_review_waits_for_a_valid_decision_and_completes() -> None:
    report = _report()
    review = WikiMaintenanceReviewService.create(lambda reviewer: _Maintenance(reviewer, report))
    assert review is not None

    pending = await review.next()
    assert pending.report == report
    completed = await review.decide(WikiMaintenanceDecision(outcome="no_change"))

    assert completed.result is not None and completed.result.complete
    assert await review.require_completed() == completed.result


@pytest.mark.asyncio
async def test_maintenance_tool_requires_the_trusted_automation() -> None:
    review = WikiMaintenanceReviewService.create(lambda reviewer: _Maintenance(reviewer, _report()))
    assert review is not None

    for automation_id in (None, "other-automation"):
        result = await wiki_maintenance_review_tool.execute(
            _execution(review, automation_id),
            action="next",
        )
        assert result.is_error
        assert result.outcome is not None and result.outcome.error is not None
        assert result.outcome.error.code == "forbidden"


@pytest.mark.asyncio
async def test_maintenance_tool_rejects_invalid_decision_without_advancing() -> None:
    report = _report()
    review = WikiMaintenanceReviewService.create(lambda reviewer: _Maintenance(reviewer, report))
    assert review is not None
    execution = _execution(review, BUILTIN_WIKI_MAINTENANCE_ID)

    pending = await wiki_maintenance_review_tool.execute(execution, action="next")
    assert not pending.is_error and pending.data == {"completed": False}
    rejected = await wiki_maintenance_review_tool.execute(
        execution,
        action="decide",
        decision={
            "outcome": "updates",
            "updates": [{"page_token": "P404", "title": "Dex", "aliases": [], "body": "User note"}],
        },
    )
    assert rejected.is_error
    assert rejected.outcome is not None and rejected.outcome.error is not None
    assert rejected.outcome.error.code == "invalid_maintenance_decision"
    still_pending = await wiki_maintenance_review_tool.execute(execution, action="next")
    assert still_pending.data == {"completed": False}
    await review.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("canonical_page_token", "loser_page_token", "error"),
    (
        ("P404", "P002", "unknown page token"),
        ("P001", "P001", "distinct page tokens"),
    ),
)
async def test_review_service_rejects_bad_merge_tokens_and_accepts_corrected_decision(
    canonical_page_token: str,
    loser_page_token: str,
    error: str,
) -> None:
    report = _merge_report()
    review = WikiMaintenanceReviewService.create(lambda reviewer: _MergeValidationMaintenance(reviewer, report))
    assert review is not None

    pending = await review.next()
    assert pending.report == report
    concern = WikiMaintenanceConcernDraft(
        key="duplicate-page", summary="Merge duplicate pages", proposal="Keep the canonical page."
    )
    with pytest.raises(WikiMaintenanceError, match=error):
        await review.decide(
            WikiMaintenanceDecision(
                outcome="needs_review",
                concern=concern,
                merge=WikiMaintenanceMergeDraft(
                    canonical_page_token=canonical_page_token,
                    loser_page_token=loser_page_token,
                ),
            )
        )

    assert review.accepted_decisions == 0
    assert (await review.next()).report == report
    completed = await review.decide(
        WikiMaintenanceDecision(
            outcome="needs_review",
            concern=concern,
            merge=WikiMaintenanceMergeDraft(canonical_page_token="P001", loser_page_token="P002"),
        )
    )

    assert completed.result is not None and completed.result.complete
    assert review.accepted_decisions == 1


@pytest.mark.asyncio
async def test_runtime_uses_the_exact_wiki_agent_scope_and_rejects_early_exit(tmp_path, monkeypatch) -> None:
    config = Config(
        arden_dir=tmp_path,
        chat_model=None,
        embedding_model=None,
        model_roles={},
        web_search="none",
    )
    runtime = Runtime(config)
    await runtime.connect()
    try:
        assert runtime.automation is not None
        requests = []

        class _RuntimeMaintenance:
            def __init__(self, reviewer) -> None:
                self.reviewer = reviewer

            def validate_prepared_decision(self, report, decision) -> None:
                return None

            async def run(self) -> WikiMaintenanceResult:
                await self.reviewer(_report())
                return WikiMaintenanceResult("a" * 64, "a" * 64, reviewed_commits=1, complete=True)

        async def current() -> bool:
            return True

        async def run_review_agent(_deps, request):
            requests.append(request)
            review = runtime.automation.wiki_maintenance_review
            assert review is not None
            await review.next()
            await review.decide(WikiMaintenanceDecision(outcome="no_change"))
            return RunResult(run_id="wiki-maintenance-run", output=None, usage=Usage())

        monkeypatch.setattr(runtime.automation, "synthesis_is_current", current)
        monkeypatch.setattr(runtime.automation, "get_wiki_maintenance", lambda reviewer: _RuntimeMaintenance(reviewer))
        monkeypatch.setattr("arden.server.runtime.automation.run_agent", run_review_agent)

        result = await runtime.automation._run_wiki_maintenance(None)
        assert result.run_id == "wiki-maintenance-run"
        assert result.result == "wiki maintenance: current; reviewed 1; updated 0"
        assert requests[0].automation_id == BUILTIN_WIKI_MAINTENANCE_ID
        assert requests[0].tool_scope == ("wiki_maintenance_review",)

        async def stop_early(*_args, **_kwargs):
            return RunResult(run_id="wiki-maintenance-run", output=None, usage=Usage())

        monkeypatch.setattr("arden.server.runtime.automation.run_agent", stop_early)
        with pytest.raises(WikiMaintenanceReviewConflictError, match="before completing"):
            await runtime.automation._run_wiki_maintenance(None)
    finally:
        await runtime.close()


def test_runtime_prompt_and_tool_schema_expose_durable_page_merge_proposals() -> None:
    schema = WikiMaintenanceReviewInput.model_json_schema()
    serialized = str(schema)

    assert "canonical_page_token" in serialized
    assert "loser_page_token" in serialized
    assert "page merge" in WIKI_MAINTENANCE_PROMPT.lower()
    assert "zero redirects" in WIKI_MAINTENANCE_PROMPT
    assert "nested merge object" in WIKI_MAINTENANCE_PROMPT
    assert "nested merge object" in wiki_maintenance_review_tool.description


@pytest.mark.asyncio
async def test_runtime_reschedules_after_two_reload_passes(tmp_path, monkeypatch) -> None:
    config = Config(
        arden_dir=tmp_path,
        chat_model=None,
        embedding_model=None,
        model_roles={},
        web_search="none",
    )
    runtime = Runtime(config)
    await runtime.connect()
    try:
        assert runtime.automation is not None
        outcomes = [
            WikiMaintenanceResult("a" * 64, "a" * 64, reviewed_commits=1, reload_required=True),
            WikiMaintenanceResult("b" * 64, "b" * 64, reviewed_commits=1, reload_required=True),
        ]
        requests = []
        scheduled = []

        class _ReloadingMaintenance:
            def __init__(self, reviewer, outcome) -> None:
                self.reviewer = reviewer
                self.outcome = outcome

            def validate_prepared_decision(self, report, decision) -> None:
                return None

            async def run(self) -> WikiMaintenanceResult:
                return self.outcome

        async def current() -> bool:
            return True

        async def run_review_agent(_deps, request):
            requests.append(request)
            review = runtime.automation.wiki_maintenance_review
            assert review is not None
            await review.next()
            return RunResult(run_id=f"wiki-maintenance-run-{len(requests)}", output=None, usage=Usage())

        async def schedule(task_id, delay):
            scheduled.append((task_id, delay))
            return True

        monkeypatch.setattr(runtime.automation, "synthesis_is_current", current)
        monkeypatch.setattr(
            runtime.automation,
            "get_wiki_maintenance",
            lambda reviewer: _ReloadingMaintenance(reviewer, outcomes.pop(0)),
        )
        monkeypatch.setattr("arden.server.runtime.automation.run_agent", run_review_agent)
        monkeypatch.setattr(runtime.automation.scheduler, "request_delayed_run", schedule)

        result = await runtime.automation._run_wiki_maintenance(None)
        assert result.result == "wiki maintenance: fresh-feed continuation deferred; reviewed 2; updated 0"
        assert len(requests) == 2
        assert scheduled[0][0] == BUILTIN_WIKI_MAINTENANCE_ID
        assert scheduled[0][1].total_seconds() == 60
    finally:
        await runtime.close()
