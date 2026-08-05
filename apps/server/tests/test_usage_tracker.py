import pytest

from arden.agent import CompletionResponse, Usage
from arden.core.usage_tracker import UsageTracker


@pytest.mark.asyncio
async def test_usage_tracker_rejects_unknown_model_without_partial_totals():
    tracker = UsageTracker()
    response = CompletionResponse(
        choices=[],
        usage=Usage(prompt_tokens=100, completion_tokens=10),
        model="missing/model",
    )

    with pytest.raises(ValueError, match="Unknown model: missing/model"):
        await tracker.track(response)

    assert tracker.usage == Usage()
    assert tracker.cost == 0
    assert tracker.own_cost == 0


@pytest.mark.asyncio
async def test_usage_tracker_accepts_registered_zero_price_model():
    tracker = UsageTracker()
    usage = Usage(prompt_tokens=100, completion_tokens=10)

    await tracker.track(
        CompletionResponse(
            choices=[],
            usage=usage,
            model="openai-codex/gpt-5.6-sol",
        )
    )

    assert tracker.usage == usage
    assert tracker.cost == 0
    assert tracker.own_cost == 0
