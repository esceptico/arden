"""Two Anthropic request-validation rejections seen live.

Claude 5: `"thinking.type.enabled" is not supported for this model. Use
"thinking.type.adaptive" and "output_config.effort"`.

Sonnet 4.6: `tools.N.custom.input_schema: input_schema does not support oneOf,
allOf, or anyOf at the top level` — pydantic `RootModel` discriminated unions
put the combinator at the schema root.
"""

from arden.llm.anthropic import AnthropicClient, _flatten_root_combinator
from arden.tools.core.schema import tool_parameters
from arden.tools.fact_capture import FactCaptureReviewInput


def _client() -> AnthropicClient:
    return AnthropicClient.__new__(AnthropicClient)


def test_claude_5_models_use_adaptive_thinking():
    client = _client()
    for model in ("claude-opus-5", "claude-sonnet-5", "claude-fable-5", "claude-haiku-5"):
        assert client._thinking_config(model, "high", 32_000) == {"type": "adaptive"}, model
        assert client._output_config(model, "high") == {"effort": "high"}, model


def test_pre_adaptive_models_keep_budgeted_thinking():
    client = _client()
    config = client._thinking_config("claude-haiku-4-5-20251001", "high", 32_000)
    assert config is not None and config["type"] == "enabled"
    assert client._output_config("claude-haiku-4-5-20251001", "high") is None


def test_root_union_tool_schema_is_flattened_for_anthropic():
    """The exact schema that 400'd: fact_capture_review's discriminated union."""
    client = _client()
    # What the registry actually hands the provider: refs inlined, display
    # title injected — with the union combinator still at the root.
    schema = tool_parameters(FactCaptureReviewInput.model_json_schema(), tool_name="fact_capture_review")
    assert "oneOf" in schema or "anyOf" in schema  # the premise, kept honest

    tools = client._convert_tools(
        [{"function": {"name": "fact_capture_review", "description": "", "parameters": schema}}]
    )
    input_schema = tools[0]["input_schema"]
    assert not ({"oneOf", "anyOf", "allOf"} & input_schema.keys())
    assert input_schema["type"] == "object"
    # The union's fields survive the merge instead of vanishing.
    assert "action" in input_schema["properties"]
    assert "decision" in input_schema["properties"]
    # Only what every variant requires is required.
    assert input_schema["required"] == ["action"]


def test_non_object_variants_are_left_alone():
    schema = {"oneOf": [{"type": "string"}, {"type": "object", "properties": {}}]}
    assert _flatten_root_combinator(schema) is schema


def test_plain_object_schemas_pass_through_untouched():
    schema = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
    assert _flatten_root_combinator(schema) is schema
