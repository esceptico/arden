from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

import arden.integrations.gmail as gmail_integration
from arden.config import Config
from arden.integrations.base import IntegrationConnectionError
from arden.integrations.gmail.models import GmailPageRef
from arden.integrations.gmail.read_tools import EmailSearchInput
from arden.integrations.gmail.write_tools import EmailSendInput


class _AccountStore:
    def __init__(self, accounts: list[SimpleNamespace]):
        self.accounts = accounts

    def accounts_for(self, service: str) -> list[SimpleNamespace]:
        assert service == "gmail"
        return self.accounts

    def token_path(self, account: SimpleNamespace, service: str) -> Path:
        assert service == "gmail"
        return Path(f"/tokens/{account.id}.json")


def _config() -> Config:
    return Config(
        _env_file=None,
        memory=False,
        integration_states={"gmail": True},
    )


def test_gmail_builder_uses_account_emails_as_refs(monkeypatch):
    store = _AccountStore(
        [
            SimpleNamespace(id="internal-1", email="First@Example.test"),
            SimpleNamespace(id="internal-2", email="second@example.test"),
        ]
    )
    monkeypatch.setattr(gmail_integration, "google_account_store", lambda: store)

    source = gmail_integration._build(_config())

    assert source is not None
    assert source.list_accounts() == ["First@Example.test", "second@example.test"]
    assert [item.token_path for item in source.sources] == [
        Path("/tokens/internal-1.json"),
        Path("/tokens/internal-2.json"),
    ]


@pytest.mark.parametrize(
    "accounts, private_ids",
    [
        ([SimpleNamespace(id="private-missing", email=None)], ("private-missing",)),
        ([SimpleNamespace(id="private-spaced", email=" user@example.test ")], ("private-spaced",)),
        (
            [
                SimpleNamespace(id="private-first", email="same@example.test"),
                SimpleNamespace(id="private-second", email="SAME@example.test"),
            ],
            ("private-first", "private-second"),
        ),
    ],
)
def test_gmail_builder_rejects_unstable_account_refs_without_leaking_internal_ids(
    monkeypatch,
    accounts: list[SimpleNamespace],
    private_ids: tuple[str, ...],
):
    monkeypatch.setattr(gmail_integration, "google_account_store", lambda: _AccountStore(accounts))

    with pytest.raises(IntegrationConnectionError) as exc_info:
        gmail_integration._build(_config())

    assert exc_info.value.reason == "auth_required"
    assert all(private_id not in str(exc_info.value) for private_id in private_ids)


def test_gmail_search_input_accepts_only_explicit_boundary_fields():
    page_ref = GmailPageRef(
        account_ref="me@example.test",
        provider_token="provider-page-2",
        query="roadmap",
        days=None,
        limit=30,
    ).encode()
    args = EmailSearchInput(page_ref=page_ref)

    assert args.page_ref == page_ref
    with pytest.raises(ValidationError, match="extra_forbidden"):
        EmailSearchInput.model_validate({"account_id": "internal-id"})


def test_gmail_search_rejects_raw_provider_page_tokens():
    with pytest.raises(ValidationError):
        EmailSearchInput(account_ref="me@example.test", page_ref="provider-page-2")


@pytest.mark.parametrize("query", ["", "   "])
def test_gmail_search_rejects_blank_query(query: str):
    with pytest.raises(ValidationError):
        EmailSearchInput(query=query)


@pytest.mark.parametrize("field", ["account_ref", "recipient", "subject"])
def test_gmail_send_rejects_header_line_breaks(field: str):
    payload = {
        "account_ref": "me@example.test",
        "recipient": "you@example.test",
        "subject": "Status",
        "body": "Ready",
        "idempotency_key": "send-status-1",
    }
    payload[field] += "\nBcc: hidden@example.test"

    with pytest.raises(ValidationError):
        EmailSendInput.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("account_ref", "a" * 321),
        ("recipient", "a" * 999),
    ],
)
def test_gmail_send_rejects_oversized_refs_and_recipients(field: str, value: str):
    payload = {
        "account_ref": "me@example.test",
        "recipient": "you@example.test",
        "subject": "Status",
        "body": "Ready",
        "idempotency_key": "send-status-1",
    }
    payload[field] = value

    with pytest.raises(ValidationError):
        EmailSendInput.model_validate(payload)


@pytest.mark.parametrize("account_ref", [" me@example.test", "me@example.test "])
def test_gmail_send_requires_exact_account_ref(account_ref: str):
    with pytest.raises(ValidationError):
        EmailSendInput(
            account_ref=account_ref,
            recipient="you@example.test",
            subject="Status",
            body="Ready",
            idempotency_key="send-status-1",
        )
