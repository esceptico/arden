from datetime import date

import pytest
from pydantic import ValidationError

from arden.agent.types.llm import CompletionResponse
from arden.agent.types.usage import Usage
from arden.memory.facts.completion_dream import CompletionFactDreamRenderer
from arden.memory.facts.dream import DreamEvidence, DreamInsight
from tests.helpers import MockCompletionClient, make_text_response


def _evidence() -> tuple[DreamEvidence, ...]:
    return (
        DreamEvidence("F001", "a", "a" * 64, "Alpha fact.", ("Alpha",), "chat:one"),
        DreamEvidence("F002", "b", "b" * 64, "Beta fact.", ("Beta",), "chat:two"),
    )


@pytest.mark.asyncio
async def test_completion_dream_parses_exact_structured_output() -> None:
    client = MockCompletionClient(
        [
            make_text_response(
                '{"insights":[{"claim":"The domains reinforce each other.","fact_tokens":["F001","F002"]}]}'
            )
        ]
    )
    renderer = CompletionFactDreamRenderer(client, "test-memory", reasoning_effort="medium")

    result = await renderer.render(month=date(2026, 7, 1), evidence=_evidence())

    assert result == (DreamInsight("The domains reinforce each other.", ("F001", "F002")),)
    assert client.calls[0]["reasoning_effort"] == "medium"
    prompt = client.calls[0]["messages"][1]["content"]
    assert "F001 [Alpha] Alpha fact." in prompt
    assert "Month: 2026-07" in prompt


@pytest.mark.asyncio
async def test_completion_dream_rejects_extra_output_fields() -> None:
    client = MockCompletionClient([make_text_response('{"insights":[],"commentary":"not part of the contract"}')])
    renderer = CompletionFactDreamRenderer(client, "test-memory")

    with pytest.raises(ValidationError):
        await renderer.render(month=date(2026, 7, 1), evidence=_evidence())


@pytest.mark.asyncio
async def test_completion_dream_rejects_missing_content() -> None:
    client = MockCompletionClient([CompletionResponse(choices=[], usage=Usage(), model="test-memory")])

    with pytest.raises(ValueError, match="returned no content"):
        await CompletionFactDreamRenderer(client, "test-memory").render(
            month=date(2026, 7, 1),
            evidence=_evidence(),
        )
