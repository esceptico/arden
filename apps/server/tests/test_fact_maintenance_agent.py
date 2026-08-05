from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from arden.constants import BUILTIN_MEMORY_CONSOLIDATE_ID
from arden.context.models import SessionState
from arden.memory.facts.maintenance.agent import FactMaintenanceReviewService
from arden.memory.facts.maintenance.runner import (
    FactMaintenanceDecision,
    FactMaintenancePreparedCluster,
    FactMaintenanceResult,
)
from arden.memory.facts.maintenance.store import FactMaintenanceError
from arden.memory.facts.models import Fact
from arden.tools.core.background_tasks import BackgroundTaskRegistry
from arden.tools.core.context import IOBridge, RunContext, ToolContext, ToolExecution
from arden.tools.core.registry import ToolRegistry
from arden.tools.fact_maintenance import (
    FACT_MAINTENANCE_SERVICE,
    FactMaintenanceReviewInput,
    fact_maintenance_review_tool,
)


def _fact(fact_id: str) -> Fact:
    return Fact(
        fact_id=fact_id,
        text="A fact",
        normalized_text="a fact",
        kind="fact",
        labels=(),
        subjects=("Dex",),
        scope={"kind": "user", "key": None},
        lifecycle="durable",
        status="active",
        certainty="confirmed",
        evidence_class="direct",
        sources=(),
        created_at=datetime(2026, 7, 29, tzinfo=UTC),
        reviewed_at=None,
        review_at=None,
        review_basis=None,
        expires_at=None,
        successor_id=None,
        version="v1",
    )


def _cluster(target: Fact, *candidates: Fact) -> FactMaintenancePreparedCluster:
    facts = (target, *candidates)
    return FactMaintenancePreparedCluster(
        target_token="F000",
        markdown="# Canonical fact maintenance cluster\n\nF000 only",
        fact_tokens={f"F{index:03d}": fact for index, fact in enumerate(facts)},
        shared_subject_tokens=(),
        exact_text_tokens=(),
        semantic_tokens=(),
        wiki_page_tokens={"P000": "Dex"},
    )


def _default_cluster() -> FactMaintenancePreparedCluster:
    return _cluster(_fact("changed"))


class _Maintenance:
    def __init__(self, reviewer, cluster: FactMaintenancePreparedCluster) -> None:
        self.reviewer = reviewer
        self.cluster = cluster

    async def run(self) -> FactMaintenanceResult:
        await self.reviewer(self.cluster)
        return FactMaintenanceResult("revision", reviewed_clusters=1)


class _SequentialMaintenance:
    def __init__(self, reviewer, *clusters: FactMaintenancePreparedCluster) -> None:
        self.reviewer = reviewer
        self.clusters = clusters

    async def run(self) -> FactMaintenanceResult:
        for cluster in self.clusters:
            await self.reviewer(cluster)
        return FactMaintenanceResult("revision", reviewed_clusters=len(self.clusters))


def _execution(review: FactMaintenanceReviewService, automation_id: str | None) -> ToolExecution:
    context = ToolContext(
        session_state=SessionState(session_id="session", started_at=datetime(2026, 7, 29, tzinfo=UTC)),
        registry=ToolRegistry(),
        run=RunContext(run_id="run", automation_id=automation_id),
        io=IOBridge(),
        services={FACT_MAINTENANCE_SERVICE: review},
        area=None,
        background_tasks=BackgroundTaskRegistry(session_id="session"),
    )
    return ToolExecution(tool_id="tool", tool_name="fact_maintenance_review", ctx=context)


@pytest.mark.asyncio
async def test_agent_review_waits_for_a_valid_decision_and_completes() -> None:
    cluster = _default_cluster()
    review = FactMaintenanceReviewService.create(lambda reviewer: _Maintenance(reviewer, cluster))
    assert review is not None

    pending = await review.next()
    assert pending.cluster == cluster
    completed = await review.decide(FactMaintenanceDecision(outcome="no_change", reason="Weak evidence."))

    assert completed.result == FactMaintenanceResult("revision", reviewed_clusters=1)
    assert await review.require_completed() == completed.result


@pytest.mark.asyncio
async def test_agent_review_rejects_wrong_target_without_advancing() -> None:
    cluster = _default_cluster()
    review = FactMaintenanceReviewService.create(lambda reviewer: _Maintenance(reviewer, cluster))
    assert review is not None
    await review.next()

    with pytest.raises(FactMaintenanceError, match="changed fact"):
        await review.decide(
            FactMaintenanceDecision(
                outcome="amend_metadata",
                reason="Wrong target.",
                target_token="F001",
                labels=["x"],
            )
        )

    assert (await review.next()).cluster == cluster
    await review.aclose()


@pytest.mark.asyncio
async def test_maintenance_tool_requires_the_trusted_automation() -> None:
    review = FactMaintenanceReviewService.create(lambda reviewer: _Maintenance(reviewer, _default_cluster()))
    assert review is not None

    for automation_id in (None, "other-automation"):
        result = await fact_maintenance_review_tool.execute(
            _execution(review, automation_id),
            action="next",
        )
        assert result.is_error
        assert result.outcome is not None and result.outcome.error is not None
        assert result.outcome.error.code == "forbidden"


@pytest.mark.asyncio
async def test_maintenance_tool_rejects_invalid_decision_without_advancing() -> None:
    cluster = _default_cluster()
    review = FactMaintenanceReviewService.create(lambda reviewer: _Maintenance(reviewer, cluster))
    assert review is not None
    execution = _execution(review, BUILTIN_MEMORY_CONSOLIDATE_ID)

    pending = await fact_maintenance_review_tool.execute(execution, action="next")
    assert not pending.is_error and pending.data == {"completed": False, "target_token": "F000"}
    rejected = await fact_maintenance_review_tool.execute(
        execution,
        action="decide",
        decision={
            "outcome": "amend_metadata",
            "reason": "Wrong target.",
            "target_token": "F001",
            "labels": ["x"],
        },
    )
    assert rejected.is_error
    assert rejected.outcome is not None and rejected.outcome.error is not None
    assert rejected.outcome.error.code == "invalid_maintenance_decision"
    still_pending = await fact_maintenance_review_tool.execute(execution, action="next")
    assert still_pending.data == {"completed": False, "target_token": "F000"}
    await review.aclose()


@pytest.mark.asyncio
async def test_agent_rejects_duplicate_merge_chain_without_losing_the_current_cluster() -> None:
    first = _cluster(_fact("a"), _fact("b"))
    second = _cluster(_fact("b"), _fact("c"))
    review = FactMaintenanceReviewService.create(lambda reviewer: _SequentialMaintenance(reviewer, first, second))
    assert review is not None

    assert (await review.next()).cluster == first
    state = await review.decide(
        FactMaintenanceDecision(
            outcome="merge_duplicate",
            reason="A duplicates B.",
            discard_token="F000",
            survivor_token="F001",
        )
    )
    assert state.cluster == second

    with pytest.raises(FactMaintenanceError, match="merge chains"):
        await review.decide(
            FactMaintenanceDecision(
                outcome="merge_duplicate", reason="B duplicates C.", discard_token="F000", survivor_token="F001"
            )
        )

    assert review.accepted_decisions == 1
    assert (await review.next()).cluster == second
    await review.aclose()


@pytest.mark.asyncio
async def test_agent_rejects_amend_then_merge_conflict_without_losing_the_current_cluster() -> None:
    first = _cluster(_fact("a"))
    second = _cluster(_fact("b"), _fact("a"))
    review = FactMaintenanceReviewService.create(lambda reviewer: _SequentialMaintenance(reviewer, first, second))
    assert review is not None

    await review.next()
    state = await review.decide(
        FactMaintenanceDecision(
            outcome="amend_metadata",
            reason="Correct label.",
            target_token="F000",
            labels=["project"],
        )
    )
    assert state.cluster == second

    with pytest.raises(FactMaintenanceError, match="both amend and merge"):
        await review.decide(
            FactMaintenanceDecision(
                outcome="merge_duplicate", reason="B duplicates A.", discard_token="F000", survivor_token="F001"
            )
        )

    assert review.accepted_decisions == 1
    assert (await review.next()).cluster == second
    await review.aclose()


def test_decision_rejects_amendment_fields_for_no_change() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        FactMaintenanceDecision(
            outcome="no_change",
            reason="Contradictory decision.",
            labels=["project"],
        )


def test_decision_rejects_amendment_fields_for_topic_normalization() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        FactMaintenanceDecision(
            outcome="normalize_topic",
            reason="Contradictory decision.",
            labels=["project"],
            old_topic="Dex",
            canonical_page_token="P000",
        )


def test_decision_schema_exposes_mutually_exclusive_outcomes() -> None:
    schema = FactMaintenanceDecision.model_json_schema()

    assert schema["discriminator"]["propertyName"] == "outcome"
    mapping = schema["discriminator"]["mapping"]
    assert set(mapping) == {
        "no_change",
        "amend_metadata",
        "merge_duplicate",
        "normalize_topic",
    }
    no_change_name = mapping["no_change"].removeprefix("#/$defs/")
    no_change = schema["$defs"][no_change_name]
    assert no_change["additionalProperties"] is False
    assert "labels" not in no_change["properties"]


def test_review_input_schema_requires_a_complete_action_shape() -> None:
    schema = FactMaintenanceReviewInput.model_json_schema()

    assert schema["discriminator"]["propertyName"] == "action"
    next_name = schema["discriminator"]["mapping"]["next"].removeprefix("#/$defs/")
    assert "decision" not in schema["$defs"][next_name]["properties"]
    with pytest.raises(ValidationError, match="Field required"):
        FactMaintenanceReviewInput(action="decide")
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        FactMaintenanceReviewInput(action="next", decision={"outcome": "no_change", "reason": "None."})
