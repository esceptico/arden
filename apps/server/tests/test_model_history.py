import base64
from copy import deepcopy

import pytest

from arden.llm.anthropic import AnthropicClient
from arden.llm.gemini import GeminiClient
from arden.llm.history import HistoryDecodeError, ModelHistory
from arden.llm.openai import OpenAIClient
from arden.llm.openai_responses import _convert_messages as convert_openai_responses_messages


class _CopyProbe:
    def __init__(self):
        self.copies = 0

    def __deepcopy__(self, _memo):
        self.copies += 1
        return self


def test_provider_replay_state_stays_with_its_owner_without_mutating_history():
    signature = base64.b64encode(b"gemini-state").decode()
    messages = [
        {
            "role": "assistant",
            "content": "canonical",
            "tool_calls": [
                {
                    "id": "call_1",
                    "thought_signature": signature,
                    "function": {"name": "Search", "arguments": '{"q":"arden"}'},
                }
            ],
            "anthropic_content": [{"type": "thinking", "thinking": "anthropic-state"}],
            "openai_response_items": [{"type": "reasoning", "encrypted_content": "openai-state"}],
            "background_result_ref": "internal-only",
        }
    ]
    original = deepcopy(messages)

    history = ModelHistory.from_raw(messages)
    chat = OpenAIClient(api_key="test")._preprocess_messages(history)
    anthropic = AnthropicClient(api_key="test")._convert_messages(history)
    _instructions, responses = convert_openai_responses_messages(history)
    _system, gemini = GeminiClient(api_key="test")._convert_messages(history)

    assert chat == [
        {
            "role": "assistant",
            "content": "canonical",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "Search", "arguments": '{"q":"arden"}'},
                }
            ],
        }
    ]
    assert anthropic == [{"role": "assistant", "content": [{"type": "thinking", "thinking": "anthropic-state"}]}]
    assert responses == [{"type": "reasoning", "encrypted_content": "openai-state"}]
    assert gemini[0].parts[0].text == "canonical"
    assert gemini[0].parts[1].thought_signature == b"gemini-state"
    assert "internal-only" not in f"{chat}{anthropic}{responses}{gemini}"
    assert messages == original


@pytest.mark.parametrize("signature", ["", "not base64!", None, 42])
def test_model_history_rejects_present_invalid_thought_signatures(signature: object):
    with pytest.raises(HistoryDecodeError, match=r"tool_call\.thought_signature_invalid"):
        ModelHistory.from_raw(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "thought_signature": signature,
                            "function": {"name": "Read", "arguments": "{}"},
                        }
                    ],
                }
            ]
        )


def test_model_history_rejects_orphan_tool_results():
    with pytest.raises(HistoryDecodeError, match=r"tool\.orphan_result"):
        ModelHistory.from_raw([{"role": "tool", "tool_call_id": "missing", "content": "unmatched"}])


def test_model_history_rejects_system_messages_after_conversation_content():
    with pytest.raises(HistoryDecodeError, match=r"system\.not_first"):
        ModelHistory.from_raw(
            [
                {"role": "user", "content": "hello"},
                {"role": "system", "content": "late policy"},
            ]
        )


def test_foreign_replay_is_lazy_and_materialized_once_by_its_owner():
    probe = _CopyProbe()
    history = ModelHistory.from_raw(
        [
            {
                "role": "assistant",
                "content": "canonical",
                "openai_response_items": [{"type": "reasoning", "payload": probe}],
            }
        ]
    )

    assert probe.copies == 0
    assert OpenAIClient(api_key="test")._preprocess_messages(history) == [{"role": "assistant", "content": "canonical"}]
    assert probe.copies == 0

    _instructions, items = convert_openai_responses_messages(history)
    assert items[0]["payload"] is probe
    assert probe.copies == 1


def test_model_history_reports_the_invalid_entry_without_echoing_content():
    secret = "secret-payload-that-must-not-reach-logs"
    with pytest.raises(HistoryDecodeError, match="message at index 1") as error:
        ModelHistory.from_raw(
            [
                {"role": "user", "content": "safe"},
                {
                    "role": "user",
                    "content": [{"type": "image", "media_type": "image/png", "data": {"secret": secret}}],
                },
            ]
        )

    assert secret not in str(error.value)
    assert error.value.__cause__ is None
    assert "content.invalid_block" in str(error.value)


def test_model_history_rejects_unsupported_cache_policy_explicitly():
    with pytest.raises(HistoryDecodeError, match=r"system\.cache_control_unsupported"):
        ModelHistory.from_raw(
            [
                {
                    "role": "system",
                    "content": [
                        {
                            "type": "text",
                            "text": "stable",
                            "cache_control": {"type": "ephemeral", "ttl": "1h"},
                        }
                    ],
                }
            ]
        )


@pytest.mark.parametrize(
    ("message", "reason"),
    [
        (
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "call_1", "type": "computer", "function": {"name": "Read"}}],
            },
            "tool_call.type_unsupported",
        ),
        (
            {"role": "tool", "tool_call_id": "call_1", "content": "failed", "outcome": "failed"},
            "tool.outcome_invalid",
        ),
    ],
)
def test_model_history_rejects_values_that_would_make_typed_state_lie(message: dict, reason: str):
    with pytest.raises(HistoryDecodeError, match=reason.replace(".", r"\.")):
        ModelHistory.from_raw([message])


def test_malformed_provider_replay_is_lazy_for_foreign_adapters_and_strict_for_owner():
    history = ModelHistory.from_raw(
        [{"role": "assistant", "content": "canonical", "openai_response_items": {"not": "a list"}}]
    )

    assert OpenAIClient(api_key="test")._preprocess_messages(history) == [{"role": "assistant", "content": "canonical"}]
    with pytest.raises(HistoryDecodeError, match=r"provider\.invalid_replay"):
        convert_openai_responses_messages(history)
