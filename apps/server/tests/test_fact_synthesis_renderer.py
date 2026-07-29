import pytest

from arden.agent.types.llm import CompletionResponse
from arden.agent.types.usage import Usage
from arden.memory.facts.completion_renderer import CompletionFactSynthesisRenderer
from arden.memory.facts.synthesis import SynthesisFact
from tests.conftest import completion_response


class _Client:
    def __init__(self, response: CompletionResponse | None = None) -> None:
        self.calls: list[dict] = []
        self.response = response or completion_response("- Kept claim (fact:F001)")

    async def completion(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


@pytest.mark.asyncio
async def test_completion_renderer_exposes_only_pinned_opaque_tokens() -> None:
    client = _Client()
    renderer = CompletionFactSynthesisRenderer(client, "test-memory", reasoning_effort="medium")

    assert (
        await renderer.render(
            title="Alpha",
            facts=(SynthesisFact("F001", "Pinned fact", "chat"),),
            existing_body="# Alpha\n\nAlready known.",
        )
        == "- Kept claim (fact:F001)"
    )

    call = client.calls[0]
    assert call["model"] == "test-memory"
    assert call["reasoning_effort"] == "medium"
    assert "F001: Pinned fact" in call["messages"][1]["content"]
    assert "Already known." in call["messages"][1]["content"]
    assert "fact:F001" in call["messages"][0]["content"]


@pytest.mark.asyncio
async def test_completion_renderer_rejects_missing_content() -> None:
    client = _Client(CompletionResponse(choices=[], usage=Usage(), model="test-memory"))

    with pytest.raises(ValueError, match="returned no content"):
        await CompletionFactSynthesisRenderer(client, "test-memory").render(
            title="Alpha",
            facts=(SynthesisFact("F001", "Pinned fact", "chat"),),
            existing_body="",
        )
