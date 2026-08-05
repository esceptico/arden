import threading
from types import SimpleNamespace

import pytest

from arden.integrations.base import IntegrationConnectionError
from arden.integrations.gmail.client import GmailPreparedSend, MultiGmailSource
from arden.integrations.gmail.notifier import EmailNotifier
from arden.notifiers.base import NotifierContext


@pytest.mark.asyncio
async def test_email_notifier_sends_through_exact_account(monkeypatch):
    event_loop_thread = threading.get_ident()
    worker_threads: list[int] = []
    prepared_calls: list[dict[str, object]] = []
    sent: list[GmailPreparedSend] = []
    source = object.__new__(MultiGmailSource)
    prepared = GmailPreparedSend("me@example.test", "you@example.test", "encoded")

    def prepare_send(**kwargs):
        worker_threads.append(threading.get_ident())
        prepared_calls.append(kwargs)
        return prepared

    def send_email(target):
        worker_threads.append(threading.get_ident())
        sent.append(target)

    monkeypatch.setattr(source, "prepare_send", prepare_send)
    monkeypatch.setattr(source, "send_email", send_email)
    notifier = EmailNotifier(
        NotifierContext(get_source=lambda _name: source, get_config_value=lambda _name: None),
        account_ref="me@example.test",
        recipient="you@example.test",
    )

    await notifier.send("Status", "Ready")

    assert prepared_calls == [
        {
            "account_ref": "me@example.test",
            "recipient": "you@example.test",
            "subject": "Status",
            "body": "Ready",
            "html": True,
        }
    ]
    assert sent == [prepared]
    assert worker_threads and all(thread_id != event_loop_thread for thread_id in worker_threads)


@pytest.mark.asyncio
async def test_email_notifier_requires_typed_gmail_source():
    notifier = EmailNotifier(
        NotifierContext(
            get_source=lambda _name: SimpleNamespace(send_email=lambda **_kwargs: None),
            get_config_value=lambda _name: None,
        ),
        account_ref="me@example.test",
        recipient="you@example.test",
    )

    with pytest.raises(IntegrationConnectionError) as raised:
        await notifier.send("Status", "Ready")

    assert raised.value.reason == "not_configured"
