import json

import pytest

from arden.agent import ToolError, ToolOutcome, ToolOutcomeStatus, ToolResult
from arden.agent.types.tools import ToolOutcomePayloadError, ToolResultPayloadError
from arden.core.content import TextContent


def _payload() -> dict[str, object]:
    result = ToolResult(
        content="done",
        preview="Done",
        data={"count": 1, "items": ["a"]},
        model_content=(TextContent(text="model detail"),),
        outcome=ToolOutcome(status=ToolOutcomeStatus.SUCCEEDED),
    )
    return json.loads(result.serialized_payload())


def test_exact_tool_result_envelope_round_trips() -> None:
    result = ToolResult.from_serialized_payload(json.dumps(_payload()))

    assert result is not None
    assert result.content == "done"
    assert result.data == {"count": 1, "items": ["a"]}
    assert result.model_content == (TextContent(text="model detail"),)
    assert result.outcome == ToolOutcome(status=ToolOutcomeStatus.SUCCEEDED)


def test_unmarked_json_is_ordinary_tool_content() -> None:
    payload = _payload()
    payload.pop("_arden_envelope")

    assert ToolResult.from_serialized_payload(json.dumps(payload)) is None


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("_arden_envelope", "tool_result.v0", "unsupported.*version"),
        ("content", 3, "content must be a string"),
        ("preview", False, "preview must be a string"),
        ("is_error", 0, "is_error must be a boolean"),
        ("data", [], "data must be an object or null"),
        ("model_content", {}, "model_content must be an array"),
        ("source_refs", {}, "source_refs must be an array"),
        ("outcome", [], "outcome must be an object or null"),
    ],
)
def test_marked_envelope_rejects_wrong_field_types(field: str, value: object, message: str) -> None:
    payload = _payload()
    payload[field] = value

    with pytest.raises(ToolResultPayloadError, match=message):
        ToolResult.from_serialized_payload(json.dumps(payload))


def test_marked_envelope_rejects_missing_and_unknown_fields() -> None:
    missing = _payload()
    missing.pop("preview")
    with pytest.raises(ToolResultPayloadError, match="missing: preview"):
        ToolResult.from_serialized_payload(json.dumps(missing))

    unknown = _payload()
    unknown["legacy_preview"] = "old"
    with pytest.raises(ToolResultPayloadError, match="unknown fields: legacy_preview"):
        ToolResult.from_serialized_payload(json.dumps(unknown))


@pytest.mark.parametrize(
    "blocks",
    [
        [42],
        [{"type": "legacy", "text": "old"}],
        [{"type": "text", "text": "ok", "legacy": True}],
    ],
)
def test_marked_envelope_rejects_malformed_content_blocks(blocks: list[object]) -> None:
    payload = _payload()
    payload["model_content"] = blocks

    with pytest.raises(ToolResultPayloadError, match="invalid tool result envelope"):
        ToolResult.from_serialized_payload(json.dumps(payload))


@pytest.mark.parametrize(
    "outcome",
    [
        {"status": "failed"},
        {"status": "failed", "error": {"code": "bad", "retryable": "false"}},
        {"status": "succeeded", "legacy": True},
        {"status": "succeeded", "effect": None},
        {"status": "succeeded", "receipt": None},
    ],
)
def test_tool_outcome_decode_rejects_missing_coerced_and_unknown_fields(outcome: dict[str, object]) -> None:
    with pytest.raises(ToolOutcomePayloadError):
        ToolOutcome.decode(outcome)


@pytest.mark.parametrize(
    "data",
    [
        {"tuple": (1, 2)},
        {"not_a_number": float("nan")},
        {1: "non-string key"},
    ],
)
def test_tool_result_rejects_non_json_safe_data(data: dict) -> None:
    with pytest.raises((TypeError, ValueError)):
        ToolResult(content="done", preview="Done", data=data)


def test_tool_result_rejects_cyclic_and_deep_data() -> None:
    cyclic: dict[str, object] = {}
    cyclic["self"] = cyclic
    nested: object = "leaf"
    for _ in range(65):
        nested = {"nested": nested}

    with pytest.raises(ValueError, match="cycle"):
        ToolResult(content="done", preview="Done", data=cyclic)
    with pytest.raises(ValueError, match="maximum nesting depth"):
        ToolResult(content="done", preview="Done", data={"value": nested})


def test_marked_envelope_with_excessive_nesting_fails_closed() -> None:
    payload = _payload()
    nested: object = "leaf"
    for _ in range(1_100):
        nested = {"nested": nested}
    payload["data"] = {"value": nested}

    with pytest.raises(ToolResultPayloadError, match="maximum nesting depth"):
        ToolResult.from_serialized_payload(json.dumps(payload))


def test_failed_outcome_round_trips_without_coercion() -> None:
    outcome = ToolOutcome(
        status=ToolOutcomeStatus.FAILED,
        error=ToolError(code="provider_error", retryable=False, recovery_action="Reconnect."),
    )

    assert ToolOutcome.decode(outcome.to_dict()) == outcome
