import pytest

import arden.core.naming as naming
from tests.helpers import make_text_response


class FakeLLM:
    def __init__(self, content: str):
        self.content = content
        self.calls = []

    async def complete(self, model, messages, **kwargs):
        self.calls.append({"model": model, "messages": messages, **kwargs})
        return make_text_response(self.content, model=model)


@pytest.mark.asyncio
async def test_generate_conversation_name_uses_session_prompt(monkeypatch):
    fake = FakeLLM('{"name": "Session naming prompts"}')
    monkeypatch.setattr(naming, "llm_client", fake)

    result = await naming.generate_conversation_name(
        "test-model",
        "please review research agent session naming prompts",
    )

    assert result == "Session naming prompts"
    assert fake.calls[0]["model"] == "test-model"
    assert fake.calls[0]["response_format"] is naming.NameOutput
    assert "research agent session naming prompts" in fake.calls[0]["messages"][1]["content"]


@pytest.mark.asyncio
async def test_generate_conversation_name_prompts_for_image_only_session(monkeypatch):
    fake = FakeLLM('{"name": "Image review"}')
    monkeypatch.setattr(naming, "llm_client", fake)

    result = await naming.generate_conversation_name("test-model", "", has_images=True)

    assert result == "Image review"
    assert fake.calls[0]["messages"][1]["content"] == "First user message:\n[no text]\nThe user also attached images."
