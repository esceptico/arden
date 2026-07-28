import pytest

from arden.memory.facts.completion_renderer import CompletionFactSynthesisRenderer
from arden.memory.facts.synthesis import SynthesisFact
from tests.conftest import completion_response


class _Client:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def completion(self, **kwargs):
        self.calls.append(kwargs)
        return completion_response("- Kept claim (fact:F001)")


@pytest.mark.asyncio
async def test_completion_renderer_exposes_only_pinned_opaque_tokens() -> None:
    client = _Client()
    renderer = CompletionFactSynthesisRenderer(client, "test-memory", reasoning_effort="medium")

    assert (
        await renderer.render(
            title="Alpha",
            facts=(SynthesisFact("F001", "Pinned fact", "chat"),),
        )
        == "- Kept claim (fact:F001)"
    )

    call = client.calls[0]
    assert call["model"] == "test-memory"
    assert call["reasoning_effort"] == "medium"
    assert "F001: Pinned fact" in call["messages"][1]["content"]
    assert "fact:F001" in call["messages"][0]["content"]
