from types import SimpleNamespace

import pytest

from arden.integrations.base import IntegrationConnectionError, IntegrationOperationError
from arden.integrations.slack.notifier import SlackNotifier


@pytest.mark.asyncio
async def test_notifier_sends_one_typed_message(monkeypatch):
    sent: list[tuple[str, str]] = []

    class FakeMessages:
        async def post_message(self, channel: str, text: str):
            sent.append((channel, text))

    class FakeClient:
        def __init__(self, *, bot_token: str):
            assert bot_token == "xoxb-test"
            self.messages = FakeMessages()

    monkeypatch.setattr("arden.integrations.slack.notifier.SlackClient", FakeClient)
    notifier = SlackNotifier(
        ctx=SimpleNamespace(get_config_value=lambda _key: "xoxb-test"),
        target="C1",
    )

    await notifier.send("Subject", "x" * 3_001)

    assert sent == [("C1", f"*Subject*\n\n{'x' * 3_001}")]


@pytest.mark.asyncio
async def test_notifier_rejects_oversize_message_before_delivery():
    notifier = SlackNotifier(
        ctx=SimpleNamespace(get_config_value=lambda _key: "xoxb-test"),
        target="C1",
    )

    with pytest.raises(IntegrationOperationError, match="40,000") as raised:
        await notifier.send("Subject", "x" * 40_000)

    assert raised.value.code == "payload_too_large"


@pytest.mark.asyncio
async def test_notifier_reports_missing_credentials_as_connection_error():
    notifier = SlackNotifier(
        ctx=SimpleNamespace(get_config_value=lambda _key: None),
        target="C1",
    )

    with pytest.raises(IntegrationConnectionError, match="SLACK_BOT_TOKEN"):
        await notifier.send("Subject", "Body")
