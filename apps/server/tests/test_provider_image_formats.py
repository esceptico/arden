from arden.agent import Role
from arden.integrations import ALL_INTEGRATIONS
from arden.llm.anthropic import AnthropicClient
from arden.llm.gemini import GeminiClient
from arden.llm.history import ModelHistory
from arden.llm.openai import OpenAIClient
from arden.llm.openai_codex import OpenAICodexClient
from arden.llm.openai_responses import prepare_responses_request
from arden.tool_call_metadata import DISPLAY_TITLE_ARG
from arden.tools.app_control import app_open_tool
from arden.tools.core.registry import ToolRegistry
from arden.tools.deferred import load_tools_tool


def _load_tools_schema() -> list[dict]:
    registry = ToolRegistry()
    registry.register("load_tools", load_tools_tool, source="_system")
    return registry.get_schemas()


def _tool_media_messages(media_type: str = "image/png") -> list[dict]:
    return [
        {
            "role": Role.ASSISTANT,
            "content": "",
            "tool_calls": [{"id": "call_1", "function": {"name": "slack_thread", "arguments": "{}"}}],
        },
        {"role": Role.TOOL, "tool_call_id": "call_1", "content": "thread text"},
        {
            "role": Role.USER,
            "client_id": "tool-media:call_1",
            "is_meta": True,
            "content": [
                {"type": "context", "content_type": "tool_result_media", "content": "Media returned."},
                {"type": "image", "media_type": media_type, "data": "iVBORw0KGgo="},
            ],
        },
    ]


def test_claude_formats_tool_media_as_base64_image_block():
    messages = AnthropicClient(api_key="test")._convert_messages(
        ModelHistory.from_raw(_tool_media_messages("image/png"))
    )

    assert messages[1] == {
        "role": Role.USER,
        "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "thread text"}],
    }
    assert messages[2]["content"][1] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "iVBORw0KGgo="},
    }


def test_openrouter_formats_tool_media_as_chat_completion_data_url():
    request = OpenAIClient(api_key="test", base_url="https://openrouter.ai/api/v1", native_openai=False)._prepare(
        messages=_tool_media_messages("image/webp"),
        model="openrouter-model",
        tools=None,
        tool_choice=None,
        temperature=None,
        max_tokens=None,
        reasoning_effort=None,
        response_format=None,
    )

    assert request["messages"][2]["content"][1] == {
        "type": "image_url",
        "image_url": {"url": "data:image/webp;base64,iVBORw0KGgo="},
    }


def test_gemini_formats_supported_tool_media_as_inline_data():
    _, contents = GeminiClient(api_key="test")._convert_messages(
        ModelHistory.from_raw(_tool_media_messages("image/png"))
    )

    image_part = contents[2].parts[1]
    assert image_part.inline_data.mime_type == "image/png"
    assert image_part.inline_data.data == b"\x89PNG\r\n\x1a\n"


def test_gemini_skips_unsupported_gif_tool_media():
    _, contents = GeminiClient(api_key="test")._convert_messages(
        ModelHistory.from_raw(_tool_media_messages("image/gif"))
    )

    assert len(contents[2].parts) == 1
    assert contents[2].parts[0].text == "<tool_result_media>\nMedia returned.\n</tool_result_media>"


def test_deferred_loader_schema_formats_for_openai_chat_and_openrouter():
    tools = _load_tools_schema()

    openai_request = OpenAIClient(api_key="test")._prepare(
        messages=[{"role": Role.USER, "content": "load slack"}],
        model="gpt-5.2",
        tools=tools,
        tool_choice="auto",
        temperature=None,
        max_tokens=None,
        reasoning_effort=None,
        response_format=None,
    )
    openrouter_request = OpenAIClient(
        api_key="test",
        base_url="https://openrouter.ai/api/v1",
        native_openai=False,
    )._prepare(
        messages=[{"role": Role.USER, "content": "load slack"}],
        model="openrouter-model",
        tools=tools,
        tool_choice="auto",
        temperature=None,
        max_tokens=None,
        reasoning_effort=None,
        response_format=None,
    )

    assert openai_request["tools"][0]["function"]["name"] == "load_tools"
    assert openai_request["tool_choice"] == "auto"
    assert openrouter_request["tools"] == openai_request["tools"]
    assert openrouter_request["tool_choice"] == "auto"


def test_deferred_loader_schema_formats_for_openai_responses_and_codex():
    tools = _load_tools_schema()

    responses_request = prepare_responses_request(
        messages=[{"role": Role.USER, "content": "load slack"}],
        model="gpt-5.5",
        tools=tools,
        tool_choice="auto",
        temperature=None,
        max_tokens=None,
        reasoning_effort="high",
        response_format=None,
        allow_sampling_options=True,
    )
    codex_request = OpenAICodexClient()._prepare(
        messages=[{"role": Role.USER, "content": "load slack"}],
        model="openai-codex/gpt-5.5",
        tools=tools,
        tool_choice="auto",
        temperature=None,
        max_tokens=None,
        reasoning_effort="high",
        response_format=None,
    )

    assert responses_request["tools"][0]["name"] == "load_tools"
    assert responses_request["tool_choice"] == "auto"
    assert codex_request["model"] == "gpt-5.5"
    assert codex_request["tools"][0]["name"] == "load_tools"


def test_deferred_loader_schema_formats_for_claude():
    request_model, request = AnthropicClient(api_key="test")._prepare(
        messages=[{"role": Role.USER, "content": "load slack"}],
        model="claude-sonnet-4-6",
        tools=_load_tools_schema(),
        tool_choice="auto",
        temperature=None,
        max_tokens=None,
        reasoning_effort=None,
        response_format=None,
    )

    assert request_model == "claude-sonnet-4-6"
    assert request["tools"][0]["name"] == "load_tools"
    assert request["tools"][0]["input_schema"]["properties"].keys() == {
        DISPLAY_TITLE_ARG,
        "group",
        "names",
    }
    assert request["tool_choice"] == {"type": "auto"}


def test_deferred_loader_schema_formats_for_gemini():
    tools = GeminiClient(api_key="test")._convert_tools(_load_tools_schema())
    declaration = tools[0].function_declarations[0]

    assert declaration.name == "load_tools"
    assert set(declaration.parameters.properties) == {DISPLAY_TITLE_ARG, "group", "names"}


def test_every_tool_schema_converts_for_gemini():
    """`types.Schema` rejects allOf, oneOf, const and discriminator, and
    `_convert_tools` builds every declaration in one pass — a single unconvertible
    tool aborts the whole request, not just its own declaration."""
    client = GeminiClient(api_key="test")

    for integration in ALL_INTEGRATIONS:
        for name, tool in integration.tools.items():
            client._convert_tools([tool.to_dict(name)])


def test_gemini_folds_a_discriminated_union_into_any_of():
    declaration = (
        GeminiClient(api_key="test")._convert_tools([app_open_tool.to_dict("app_open")])[0].function_declarations[0]
    )

    destination = declaration.parameters.properties["destination"]
    assert [member.properties["kind"].enum for member in destination.any_of] == [
        ["home"],
        ["session"],
        ["settings"],
        ["automation"],
        ["memory"],
        ["area"],
    ]
