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
async def test_generate_agent_name_sends_task_only(monkeypatch):
    fake = FakeLLM('{"name": "Summarize recent areas"}')
    monkeypatch.setattr(naming, "llm_client", fake)

    result = await naming.generate_agent_name(
        "test-model",
        "summarize recent areas",
    )

    assert result == "Summarize recent areas"
    assert fake.calls[0]["model"] == "test-model"
    assert fake.calls[0]["response_format"] is naming.NameOutput
    assert fake.calls[0]["messages"][1]["content"] == "Task:\nsummarize recent areas"


@pytest.mark.asyncio
async def test_generate_conversation_name_prompts_for_image_only_session(monkeypatch):
    fake = FakeLLM('{"name": "Image review"}')
    monkeypatch.setattr(naming, "llm_client", fake)

    result = await naming.generate_conversation_name("test-model", "", has_images=True)

    assert result == "Image review"
    assert fake.calls[0]["messages"][1]["content"] == "First user message:\n[no text]\nThe user also attached images."


@pytest.mark.asyncio
async def test_generate_agent_name_falls_back_when_model_fails(monkeypatch):
    class FailingLLM:
        async def complete(self, model, messages, **kwargs):
            raise RuntimeError("boom")

    monkeypatch.setattr(naming, "llm_client", FailingLLM())

    result = await naming.generate_agent_name("test-model", "inspect eval harness")

    assert result == "Agent"


@pytest.mark.asyncio
async def test_generate_agent_name_does_not_postprocess_model_output(monkeypatch):
    fake = FakeLLM('{"name": "Research eval test harness"}')
    monkeypatch.setattr(naming, "llm_client", fake)

    result = await naming.generate_agent_name("test-model", "inspect eval harness")

    assert result == "Research eval test harness"
