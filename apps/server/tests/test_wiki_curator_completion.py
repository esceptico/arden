from __future__ import annotations

from datetime import UTC, datetime

import pytest

from arden.memory.facts.models import Fact
from arden.wiki.curation.completion import CompletionWikiEditCuratorReviewer
from arden.wiki.curation.engine import WikiEditCuratorDecision, WikiEditCuratorEvidence
from tests.conftest import completion_response


class _Client:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def completion(self, **kwargs):
        self.calls.append(kwargs)
        return completion_response('{"outcome":"no_change","reason":"Formatting only.","corrections":[]}')


@pytest.mark.asyncio
async def test_completion_reviewer_uses_typed_semantic_decision() -> None:
    client = _Client()
    reviewer = CompletionWikiEditCuratorReviewer(client, "test-curator", reasoning_effort="medium")
    point = datetime(2026, 7, 28, 12, tzinfo=UTC)
    fact = Fact(
        fact_id="fact-one",
        text="The user prefers tea.",
        normalized_text="the user prefers tea.",
        kind="preference",
        labels=(),
        subjects=("User",),
        scope={"kind": "user", "key": None},
        lifecycle="durable",
        status="active",
        certainty="confirmed",
        evidence_class="direct",
        sources=({"kind": "test", "ref": "one"},),
        created_at=point,
        reviewed_at=None,
        review_at=None,
        review_basis=None,
        expires_at=None,
        successor_id=None,
        version="a" * 64,
    )
    evidence = WikiEditCuratorEvidence(
        commit_id="b" * 64,
        baseline_commit_id="c" * 64,
        source_revision="d" * 64,
        page_id="preferences",
        path="preferences.md",
        generated_baseline="The user prefers tea.",
        before_user_edit="The user prefers tea.",
        committed_user_edit="The  user prefers tea.",
        fact_tokens={"F000": fact},
    )

    assert (await reviewer.review(evidence)).outcome == "no_change"
    call = client.calls[0]
    assert call["model"] == "test-curator"
    assert call["reasoning_effort"] == "medium"
    assert call["response_format"] is WikiEditCuratorDecision
    assert "formatting" in call["messages"][0]["content"]
    assert '"token": "F000"' in call["messages"][1]["content"]
