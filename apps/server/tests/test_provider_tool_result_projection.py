from copy import deepcopy

from arden.llm.anthropic import AnthropicClient
from arden.llm.gemini import GeminiClient
from arden.llm.openai import OpenAIClient
from arden.llm.openai_responses import _convert_messages as convert_openai_messages


def test_openai_and_anthropic_project_bounded_tool_result_without_mutating_history():
    bounded = (
        "head preview\n... [content omitted] ...\ntail preview\n\n"
        "[Full tool result saved to /tmp/session/call-1.txt. Use file_read to retrieve it.]"
    )
    messages = [
        {"role": "system", "content": "system"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "wiki_read_page", "arguments": "{}"},
                }
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "call-1",
            "content": bounded,
            "data": {"raw_ref": "internal-only"},
            "outcome": {"status": "succeeded"},
        },
    ]
    original = deepcopy(messages)

    anthropic = AnthropicClient(api_key="test")._convert_messages(messages[1:])
    _instructions, openai = convert_openai_messages(messages)

    anthropic_result = next(
        block
        for message in anthropic
        if isinstance(message.get("content"), list)
        for block in message["content"]
        if block.get("type") == "tool_result"
    )
    openai_result = next(item for item in openai if item.get("type") == "function_call_output")
    assert anthropic_result["content"] == bounded
    assert openai_result["output"] == bounded
    assert messages == original
    assert "internal-only" not in str(anthropic)
    assert "internal-only" not in str(openai)


def test_provider_projections_do_not_leak_background_result_ref():
    messages = [{"role": "user", "content": "completion", "background_result_ref": "bg-1"}]

    chat = OpenAIClient(api_key="test")._preprocess_messages(messages)
    anthropic = AnthropicClient(api_key="test")._convert_messages(messages)
    _instructions, responses = convert_openai_messages(messages)
    _system, gemini = GeminiClient(api_key="test")._convert_messages(messages)

    assert "background_result_ref" not in str(chat)
    assert "background_result_ref" not in str(anthropic)
    assert "background_result_ref" not in str(responses)
    assert "background_result_ref" not in str(gemini)
