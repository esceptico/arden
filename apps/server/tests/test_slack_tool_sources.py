from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from arden.context.models import SessionState
from arden.integrations.slack.client import SlackClient
from arden.integrations.slack.models import SlackChannel, SlackMessage, SlackMessagePage, SlackThreadResult
from arden.integrations.slack.read_tools import (
    SlackChannelInput,
    SlackChannelsInput,
    SlackFileInput,
    SlackSearchInput,
    SlackThreadInput,
    SlackUserInput,
    slack_channels,
    slack_search,
    slack_thread,
)
from arden.integrations.slack.tool_input import SlackToolInput
from arden.integrations.slack.write_tools import SlackPostBlocksInput, approve_slack_post_blocks
from arden.tools.core.context import IOBridge, RunContext, ToolContext, ToolExecution
from arden.tools.core.registry import ToolRegistry


def _execution(service: object, tool_name: str) -> ToolExecution:
    return ToolExecution(
        tool_id="call-1",
        tool_name=tool_name,
        ctx=ToolContext(
            session_state=SessionState(session_id="session-1", started_at=datetime(2026, 7, 10, tzinfo=UTC)),
            registry=ToolRegistry(),
            run=RunContext(run_id="run-1"),
            io=IOBridge(),
            services={"slack": service},
        ),
    )


class FakeSlackSource(SlackClient):
    def __init__(self, messages: list[SlackMessage], *, next_page_ref: str | None = None):
        async def search_messages(*_args, **_kwargs) -> SlackMessagePage:
            return SlackMessagePage(messages=tuple(messages), next_page_ref=next_page_ref)

        async def read_thread(_thread_ref: str, *, after_ref: str | None = None) -> SlackThreadResult | None:
            assert after_ref is None
            return SlackThreadResult(text="thread body")

        self.messages = SimpleNamespace(search_messages=search_messages)
        self.media = SimpleNamespace(read_thread=read_thread)
        self.directory = SimpleNamespace()


def test_slack_tool_locators_use_refs_and_reject_unknown_fields():
    assert all(model.model_config["extra"] == "forbid" for model in SlackToolInput.__subclasses__())
    assert "thread_ref" in SlackThreadInput.model_fields
    assert "message_ref" not in SlackThreadInput.model_fields
    assert "user_ref" in SlackUserInput.model_fields
    assert "user_id" not in SlackUserInput.model_fields
    assert "file_ref" in SlackFileInput.model_fields
    assert "file_id" not in SlackFileInput.model_fields
    assert "page_ref" in SlackSearchInput.model_fields
    assert "page_ref" in SlackChannelInput.model_fields
    with pytest.raises(ValidationError, match="extra_forbidden"):
        SlackSearchInput.model_validate({"query": "release", "unknown": True})


@pytest.mark.asyncio
async def test_slack_search_uses_message_ref_title_and_existing_permalink():
    source = FakeSlackSource(
        [
            SlackMessage(
                ref="C123:1710000000.000100",
                channel_ref="C123",
                channel_name="product",
                author_ref="U123",
                author_name="Ada",
                text="result content",
                created_at=datetime(2026, 7, 10, tzinfo=UTC),
                thread_ref="C123:1710000000.000100",
                permalink="https://workspace.slack.com/archives/C123/p1710000000000100",
            )
        ]
    )

    result = await slack_search(_execution(source, "slack_search"), SlackSearchInput(query="roadmap"))

    assert [ref.to_dict() for ref in result.source_refs] == [
        {
            "provider": "slack",
            "kind": "message",
            "ref": "C123:1710000000.000100",
            "title": "#product — Ada",
            "url": "https://workspace.slack.com/archives/C123/p1710000000000100",
        }
    ]


@pytest.mark.asyncio
async def test_slack_search_exposes_exact_page_ref_even_for_empty_page():
    source = FakeSlackSource([], next_page_ref="page-2")

    result = await slack_search(
        _execution(source, "slack_search"),
        SlackSearchInput(query="roadmap"),
    )

    assert "continue with page_ref: page-2" in result.content
    assert result.data == {"count": 0, "next_page_ref": "page-2"}


@pytest.mark.asyncio
async def test_slack_thread_uses_exact_thread_ref_without_permalink_lookup():
    source = FakeSlackSource([])

    result = await slack_thread(
        _execution(source, "slack_thread"),
        SlackThreadInput(thread_ref="C123:1710000000.000100"),
    )

    assert [ref.to_dict() for ref in result.source_refs] == [
        {
            "provider": "slack",
            "kind": "message",
            "ref": "C123:1710000000.000100",
            "title": "Slack thread C123:1710000000.000100",
        }
    ]


@pytest.mark.asyncio
async def test_slack_channels_consumes_typed_channel_records():
    source = FakeSlackSource([])

    async def channels(_query: str | None, *, limit: int):
        return [SlackChannel(ref="C123", name="product")]

    source.directory.search_channels = channels
    result = await slack_channels(_execution(source, "slack_channels"), SlackChannelsInput())

    assert result.content == "• #product  (ref: C123)"
    assert [ref.to_dict() for ref in result.source_refs] == [
        {"provider": "slack", "kind": "channel", "ref": "C123", "title": "product"}
    ]


@pytest.mark.asyncio
async def test_slack_thread_missing_message_is_typed_and_recoverable():
    source = FakeSlackSource([])

    async def missing_thread(_thread_ref: str, *, after_ref: str | None = None) -> SlackThreadResult | None:
        assert after_ref is None
        return None

    source.media.read_thread = missing_thread
    result = await slack_thread(
        _execution(source, "slack_thread"),
        SlackThreadInput(thread_ref="C404:0"),
    )

    assert result.is_error
    assert result.outcome is not None and result.outcome.error is not None
    assert result.outcome.error.code == "not_found"
    assert result.outcome.error.recovery_action == "Call slack_search first to obtain a current thread reference."


@pytest.mark.asyncio
async def test_slack_blocks_approval_includes_the_actual_blocks():
    info = await approve_slack_post_blocks(
        _execution(FakeSlackSource([]), "slack_post_blocks"),
        SlackPostBlocksInput(
            channel="#releases",
            text="Release status",
            blocks=[
                {
                    "type": "section",
                    "text": {"type": "mrkdwn", "text": "*Release is green*"},
                }
            ],
            idempotency_key="slack-release-1",
        ),
    )

    assert info is not None
    assert '"text":"*Release is green*"' in (info.preview or "")


def test_slack_blocks_reject_unknown_or_malformed_blocks():
    with pytest.raises(ValidationError):
        SlackPostBlocksInput(
            channel="#releases",
            text="Release status",
            blocks=[{"type": "unknown", "text": "surprise"}],
            idempotency_key="slack-release-1",
        )

    with pytest.raises(ValidationError):
        SlackPostBlocksInput(
            channel="#releases",
            text="Release status",
            blocks=[{"type": "section", "provider_extension": True}],
            idempotency_key="slack-release-1",
        )


def test_slack_blocks_preserve_text_and_serialize_exact_payload():
    args = SlackPostBlocksInput(
        channel="#releases",
        text="Release status",
        blocks=[
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "first\\nsecond"},
            }
        ],
        idempotency_key="slack-release-1",
    )

    assert args.blocks.provider_payload() == [{"type": "section", "text": {"type": "mrkdwn", "text": "first\\nsecond"}}]


@pytest.mark.parametrize(
    "block",
    [
        {"type": "header", "text": {"type": "plain_text", "text": "h" * 151}},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "b" * 76},
                    "action_id": "approve",
                }
            ],
        },
        {
            "type": "section",
            "fields": [{"type": "mrkdwn", "text": "f" * 2_001}],
        },
        {
            "type": "image",
            "image_url": "https://example.test/image.png",
            "alt_text": "example",
            "title": {"type": "plain_text", "text": "i" * 2_001},
        },
    ],
)
def test_slack_blocks_enforce_context_specific_text_limits(block):
    with pytest.raises(ValidationError):
        SlackPostBlocksInput(
            channel="#releases",
            text="Release status",
            blocks=[block],
            idempotency_key="slack-release-1",
        )
