from __future__ import annotations

import pytest

from arden.wiki import CompletionWikiMaintenanceReviewer, WikiMaintenanceDecision, WikiMaintenancePreparedReport
from tests.conftest import completion_response


class _Client:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def completion(self, **kwargs):
        self.calls.append(kwargs)
        return completion_response('{"outcome":"no_change","updates":[],"concern":null}')


@pytest.mark.asyncio
async def test_completion_reviewer_uses_structured_opaque_report_only() -> None:
    client = _Client()
    reviewer = CompletionWikiMaintenanceReviewer(client, "test-maintenance", reasoning_effort="medium")
    report = WikiMaintenancePreparedReport(
        commit_id="a" * 64,
        base_head="a" * 64,
        evidence_fingerprint="b" * 64,
        replay_fingerprint="c" * 64,
        markdown="# Wiki maintenance review\n\nP001 only",
        page_tokens={},
    )

    assert (await reviewer.review(report)).outcome == "no_change"
    call = client.calls[0]
    assert call["model"] == "test-maintenance"
    assert call["reasoning_effort"] == "medium"
    assert call["response_format"] is WikiMaintenanceDecision
    assert call["messages"][1]["content"] == report.markdown
    assert "User edits are authoritative" in call["messages"][0]["content"]
