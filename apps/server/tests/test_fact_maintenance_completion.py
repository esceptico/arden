import pytest
from pydantic import ValidationError

from arden.memory.facts.maintenance.completion import review_fact_maintenance
from arden.memory.facts.maintenance.runner import FactMaintenanceDecision, FactMaintenancePreparedCluster
from tests.conftest import completion_response

_NO_CHANGE_WITH_EMPTY_LABELS = """{
    "outcome": "no_change",
    "reason": "Weak evidence.",
    "target_token": null,
    "kind": null,
    "labels": [],
    "subjects": null,
    "lifecycle": null,
    "evidence_class": null,
    "discard_token": null,
    "survivor_token": null,
    "old_topic": null,
    "canonical_page_token": null
}"""


class _Client:
    def __init__(self, content: str = _NO_CHANGE_WITH_EMPTY_LABELS) -> None:
        self.content = content
        self.calls: list[dict] = []

    async def completion(self, **kwargs):
        self.calls.append(kwargs)
        return completion_response(self.content)


@pytest.mark.asyncio
async def test_completion_function_uses_the_typed_opaque_cluster() -> None:
    client = _Client()
    cluster = FactMaintenancePreparedCluster(
        target_token="F000",
        markdown="# Canonical fact maintenance cluster\n\nF000 only",
        fact_tokens={},
        shared_subject_tokens=(),
        exact_text_tokens=(),
        semantic_tokens=(),
        wiki_page_tokens={"P000": "Canonical"},
    )

    decision = await review_fact_maintenance(client, "test-maintenance", cluster, reasoning_effort="medium")

    assert decision.outcome == "no_change"
    assert decision.labels == []
    call = client.calls[0]
    assert call["model"] == "test-maintenance"
    assert call["reasoning_effort"] == "medium"
    assert call["response_format"] is FactMaintenanceDecision
    assert call["messages"][1]["content"] == cluster.markdown
    assert "Never create or rewrite fact text" in call["messages"][0]["content"]
    assert "only with its opaque P### token" in call["messages"][0]["content"]
    assert "For no_change, leave every action field null" in call["messages"][0]["content"]


def test_decision_rejects_nonempty_labels_for_no_change() -> None:
    with pytest.raises(ValidationError, match="no_change must not include an amendment"):
        FactMaintenanceDecision(
            outcome="no_change",
            reason="Contradictory decision.",
            labels=["project"],
        )
