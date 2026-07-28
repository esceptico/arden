import pytest

from arden.memory.facts.maintenance.completion import review_fact_maintenance
from arden.memory.facts.maintenance.runner import FactMaintenanceDecision, FactMaintenancePreparedCluster
from tests.conftest import completion_response


class _Client:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def completion(self, **kwargs):
        self.calls.append(kwargs)
        return completion_response('{"outcome":"no_change","reason":"Weak evidence."}')


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

    assert (
        await review_fact_maintenance(client, "test-maintenance", cluster, reasoning_effort="medium")
    ).outcome == "no_change"
    call = client.calls[0]
    assert call["model"] == "test-maintenance"
    assert call["reasoning_effort"] == "medium"
    assert call["response_format"] is FactMaintenanceDecision
    assert call["messages"][1]["content"] == cluster.markdown
    assert "Never create or rewrite fact text" in call["messages"][0]["content"]
    assert "only with its opaque P### token" in call["messages"][0]["content"]
