"""A reasoning summary is a LIST of parts, each shaped `**Title**\\n\\nbody`.

Gluing them together buries every title after the first mid-line, where it is
no longer a heading — the desktop then renders the whole summary as a single
step with a bold blob for a body.
"""

from arden.llm.anthropic import AnthropicClient
from arden.llm.openai_responses import _ResponsesStreamCollector, parse_responses_response

PART_ONE = "**Recommending applying next cycle**\n\nThey should reapply in spring."
PART_TWO = "**Planning timed practice**\n\nTimed sets beat untimed review."


class _Event:
    def __init__(self, data: dict) -> None:
        self._data = data

    def model_dump(self, **_kwargs) -> dict:
        return self._data


class _Response:
    output: list = []
    usage = None
    id = "resp_1"
    status = "completed"


def _reasoning_item(*summaries: str) -> dict:
    return {"type": "reasoning", "summary": [{"type": "summary_text", "text": s} for s in summaries]}


def test_streamed_summary_parts_are_separated_by_a_blank_line():
    collector = _ResponsesStreamCollector()
    events = [
        {"type": "response.reasoning_summary_part.added", "summary_index": 0},
        {"type": "response.reasoning_summary_text.delta", "delta": PART_ONE},
        {"type": "response.reasoning_summary_part.added", "summary_index": 1},
        {"type": "response.reasoning_summary_text.delta", "delta": PART_TWO},
    ]
    streamed = "".join(
        delta.content for event in events for delta in collector.consume(_Event(event), emit_deltas=True)
    )

    assert streamed == f"{PART_ONE}\n\n{PART_TWO}"
    # The live stream and the collected fallback must agree byte for byte, or
    # the transcript changes shape the moment the session reloads.
    assert "".join(collector.reasoning_parts) == streamed


def test_a_leading_part_boundary_does_not_open_with_a_blank_line():
    collector = _ResponsesStreamCollector()
    collector.consume(_Event({"type": "response.reasoning_summary_part.added", "summary_index": 0}), emit_deltas=True)
    collector.consume(_Event({"type": "response.reasoning_summary_text.delta", "delta": PART_ONE}), emit_deltas=True)

    assert "".join(collector.reasoning_parts) == PART_ONE


def test_parsed_summary_parts_are_separated_by_a_blank_line():
    parsed = parse_responses_response(_Response(), "gpt-5.6-sol", [_reasoning_item(PART_ONE, PART_TWO)])

    assert parsed.choices[0].message.reasoning_content == f"{PART_ONE}\n\n{PART_TWO}"


def test_every_reasoning_item_contributes_its_summary():
    """The emptiness guard used to be response-scoped, so a response carrying
    more than one reasoning item silently dropped all but the first."""
    parsed = parse_responses_response(
        _Response(),
        "gpt-5.6-sol",
        [_reasoning_item(PART_ONE), _reasoning_item(PART_TWO)],
    )

    assert parsed.choices[0].message.reasoning_content == f"{PART_ONE}\n\n{PART_TWO}"


def test_reasoning_text_still_wins_over_summary_within_one_item():
    item = _reasoning_item(PART_TWO)
    item["content"] = [{"type": "reasoning_text", "text": PART_ONE}]
    parsed = parse_responses_response(_Response(), "gpt-5.6-sol", [item])

    assert parsed.choices[0].message.reasoning_content == PART_ONE


def test_anthropic_keeps_every_thinking_block():
    """Assigning per block kept only the last, so all earlier thinking vanished."""

    class _Block:
        def __init__(self, thinking: str) -> None:
            self.type = "thinking"
            self.thinking = thinking

        def model_dump(self, **_kwargs) -> dict:
            return {"type": self.type, "thinking": self.thinking}

    client = AnthropicClient.__new__(AnthropicClient)
    _content, _tools, reasoning, _raw = client._parse_content_blocks(
        [_Block("First consideration."), _Block("Second consideration.")], None
    )

    assert reasoning == "First consideration.\n\nSecond consideration."
