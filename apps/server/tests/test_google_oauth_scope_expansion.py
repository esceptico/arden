from types import SimpleNamespace

import pytest

from ntrp.integrations.google_auth import auth
from ntrp.integrations.google_auth.accounts import GoogleAccountStore


class Flow:
    def __init__(self, warning: Warning):
        self.warning = warning
        self.oauth2session = SimpleNamespace(token={})
        self.credentials = SimpleNamespace(scopes=())

    def run_local_server(self, **_kwargs):
        raise self.warning


def _scope_warning(old: set[str], new: set[str]) -> Warning:
    warning = Warning("scope changed")
    warning.old_scope = old
    warning.new_scope = new
    warning.token = {"access_token": "token", "scope": list(new)}
    return warning


def test_oauth_accepts_google_scope_expansion():
    flow = Flow(_scope_warning({"drive"}, {"drive", "gmail"}))

    credentials = auth._run_local_oauth(flow)

    assert flow.oauth2session.token["access_token"] == "token"
    assert credentials is flow.credentials


def test_oauth_rejects_scope_reduction():
    flow = Flow(_scope_warning({"drive", "gmail"}, {"drive"}))

    with pytest.raises(Warning, match="scope changed"):
        auth._run_local_oauth(flow)


def test_service_oauth_detects_existing_account_by_email_without_requiring_optional_old_scopes(
    tmp_path,
    monkeypatch,
):
    store = GoogleAccountStore(tmp_path)
    old_scopes = tuple(auth.SCOPES_GMAIL_READ + auth.SCOPES_GMAIL_SEND + auth.SCOPES_CALENDAR + auth.SCOPES_PUBSUB)
    account = store.upsert_authorization(
        email="user@example.com",
        credential_json="old-token",
        scopes=old_scopes,
        service="gmail",
    )
    store.bind_service(account.id, "calendar")
    granted = tuple(auth.SCOPES_IDENTITY + auth.SCOPES_GMAIL_READ + auth.SCOPES_GMAIL_SEND + auth.SCOPES_CALENDAR + auth.SCOPES_DRIVE)
    credentials = SimpleNamespace(scopes=granted, to_json=lambda: "expanded-token")
    credentials_path = tmp_path / "credentials.json"
    credentials_path.write_text("{}")
    monkeypatch.setattr(auth, "CREDENTIALS_PATH", credentials_path)
    monkeypatch.setattr(auth.InstalledAppFlow, "from_client_secrets_file", lambda *_args: object())
    monkeypatch.setattr(auth, "_run_local_oauth", lambda _flow: credentials)
    monkeypatch.setattr(
        auth,
        "build",
        lambda *_args, **_kwargs: SimpleNamespace(
            userinfo=lambda: SimpleNamespace(
                get=lambda: SimpleNamespace(execute=lambda: {"email": "user@example.com"}),
            ),
        ),
    )

    updated = auth.authorize_google_service("google_drive", store=store)

    assert updated.id == account.id
    assert updated.services == frozenset({"gmail", "calendar", "google_drive"})
    assert store.token_path(updated, "gmail").read_text() == "old-token"
    assert store.token_path(updated, "calendar").read_text() == "old-token"
    assert store.token_path(updated, "google_drive").read_text() == "expanded-token"


def test_service_oauth_creates_a_separate_account_for_a_different_google_email(tmp_path, monkeypatch):
    store = GoogleAccountStore(tmp_path)
    existing = store.upsert_authorization(
        email="user@example.com",
        credential_json="old-token",
        scopes=tuple(auth.SCOPES_GMAIL_READ + auth.SCOPES_GMAIL_SEND),
        service="gmail",
    )
    granted = tuple(auth.SCOPES_IDENTITY + auth.SCOPES_DRIVE)
    credentials = SimpleNamespace(scopes=granted, to_json=lambda: "drive-token")
    credentials_path = tmp_path / "credentials.json"
    credentials_path.write_text("{}")
    monkeypatch.setattr(auth, "CREDENTIALS_PATH", credentials_path)
    monkeypatch.setattr(auth.InstalledAppFlow, "from_client_secrets_file", lambda *_args: object())
    monkeypatch.setattr(auth, "_run_local_oauth", lambda _flow: credentials)
    monkeypatch.setattr(
        auth,
        "build",
        lambda *_args, **_kwargs: SimpleNamespace(
            userinfo=lambda: SimpleNamespace(
                get=lambda: SimpleNamespace(execute=lambda: {"email": "other@example.com"}),
            ),
        ),
    )

    added = auth.authorize_google_service("google_drive", store=store)

    assert added.id != existing.id
    assert added.email == "other@example.com"
    assert added.services == frozenset({"google_drive"})
    assert {account.email for account in store.list_accounts()} == {"user@example.com", "other@example.com"}


def test_removing_a_google_account_revokes_each_distinct_service_token(tmp_path, monkeypatch):
    store = GoogleAccountStore(tmp_path)
    account = store.upsert_authorization(
        email="user@example.com",
        credential_json="gmail-token",
        scopes=("gmail.readonly",),
        service="gmail",
    )
    account = store.upsert_authorization(
        email="user@example.com",
        credential_json="drive-token",
        scopes=("drive.file",),
        service="google_drive",
    )
    revoked = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(
        auth.Credentials,
        "from_authorized_user_file",
        lambda path: SimpleNamespace(refresh_token=path, token=None),
    )
    monkeypatch.setattr(auth, "urlopen", lambda request, timeout: revoked.append(request.data) or Response())

    auth.revoke_google_account(account, store)

    assert len(revoked) == 2
