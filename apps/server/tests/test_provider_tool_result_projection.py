from copy import deepcopy

import pytest

from arden.llm.anthropic import AnthropicClient
from arden.llm.gemini import GeminiClient
from arden.llm.history import ModelHistory
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

    anthropic = AnthropicClient(api_key="test")._convert_messages(ModelHistory.from_raw(messages[1:]))
    _instructions, openai = convert_openai_messages(ModelHistory.from_raw(messages))

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

    history = ModelHistory.from_raw(messages)
    chat = OpenAIClient(api_key="test")._preprocess_messages(history)
    anthropic = AnthropicClient(api_key="test")._convert_messages(history)
    _instructions, responses = convert_openai_messages(history)
    _system, gemini = GeminiClient(api_key="test")._convert_messages(history)

    assert "background_result_ref" not in str(chat)
    assert "background_result_ref" not in str(anthropic)
    assert "background_result_ref" not in str(responses)
    assert "background_result_ref" not in str(gemini)


@pytest.mark.parametrize("content", ["plain text", "[]", "null", '"text"', '{"value":1}'])
def test_gemini_projects_tool_content_as_text_without_guessing_json(content: str):
    history = ModelHistory.from_raw(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "example", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": content},
        ]
    )

    _system, converted = GeminiClient(api_key="test")._convert_messages(history)
    response = converted[-1].parts[0].function_response.response

    assert response == {"result": content}


def test_gemini_projects_failed_tool_outcome_inside_result_text():
    history = ModelHistory.from_raw(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "example", "arguments": "{}"},
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-1",
                "content": "failed",
                "outcome": {
                    "status": "failed",
                    "error": {"code": "provider_error", "retryable": False},
                },
            },
        ]
    )

    _system, converted = GeminiClient(api_key="test")._convert_messages(history)
    response = converted[-1].parts[0].function_response.response

    assert response["result"].startswith("failed\n\n<tool_outcome>")
    assert '"status":"failed"' in response["result"]
