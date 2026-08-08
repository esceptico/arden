from types import SimpleNamespace

import pytest
from google.genai import types

from arden.agent import FinishReason, ProviderResponseError
from arden.llm.gemini import GeminiClient
from arden.llm.openai import _map_finish_reason


def test_gemini_rejects_response_without_candidates() -> None:
    response = SimpleNamespace(usage_metadata=None, candidates=[])

    with pytest.raises(ProviderResponseError, match="no candidates"):
        GeminiClient(api_key="test")._parse_response(response, "gemini-test")


def test_gemini_maps_safety_to_explicit_content_filter() -> None:
    assert GeminiClient(api_key="test")._map_finish_reason(types.FinishReason.SAFETY) == FinishReason.CONTENT_FILTER


def test_gemini_rejects_unknown_terminal_reason() -> None:
    with pytest.raises(ProviderResponseError, match="RECITATION"):
        GeminiClient(api_key="test")._map_finish_reason("RECITATION")


@pytest.mark.parametrize("reason", [None, "future_reason"])
def test_openai_rejects_missing_or_unknown_terminal_reason(reason: str | None) -> None:
    with pytest.raises(ProviderResponseError):
        _map_finish_reason(reason)
