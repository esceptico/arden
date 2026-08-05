from types import SimpleNamespace
from urllib.error import HTTPError, URLError

import pytest

from arden.integrations.base import IntegrationConnectionError, IntegrationOperationError
from arden.integrations.google_auth import auth
from arden.integrations.google_auth.accounts import GoogleAccountStore


class Flow:
    def __init__(self, warning: Warning):
        self.warning = warning
        self.oauth2session = SimpleNamespace(token={})
        self.credentials = SimpleNamespace(scopes=())

    def run_local_server(self, **_kwargs):
        raise self.warning


def _scope_warning(old: set[str], new: set[str]) -> Warning:
    warning = Warning("scope changed")
    token = auth.OAuth2Token(
        {"access_token": "token", "scope": sorted(new)},
        old_scope=sorted(old),
    )
    warning.old_scope = token.old_scopes
    warning.new_scope = token.scopes
    warning.token = token
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


def test_oauth_rejects_duck_typed_scope_warning_token():
    warning = Warning("scope changed")
    warning.old_scope = ["drive"]
    warning.new_scope = ["drive", "gmail"]
    warning.token = {"access_token": "token", "scope": ["drive", "gmail"]}

    with pytest.raises(Warning, match="scope changed"):
        auth._run_local_oauth(Flow(warning))


def test_service_oauth_detects_existing_account_by_email_without_requiring_optional_old_scopes(
    tmp_path,
    monkeypatch,
):
    store = GoogleAccountStore(tmp_path)
    old_scopes = tuple(auth.SCOPES_GMAIL_READ + auth.SCOPES_GMAIL_SEND + auth.SCOPES_CALENDAR)
    account = store.upsert_authorization(
        email="user@example.com",
        credential_json="old-token",
        scopes=old_scopes,
        service="gmail",
    )
    store.upsert_authorization(
        account_id=account.id,
        email=account.email,
        credential_json="old-token",
        scopes=old_scopes,
        service="calendar",
    )
    granted = tuple(auth.SCOPES_IDENTITY + auth.SCOPES_DRIVE)
    credentials = SimpleNamespace(scopes=granted, to_json=lambda: "expanded-token")
    credentials_path = tmp_path / "credentials.json"
    credentials_path.write_text("{}")
    monkeypatch.setattr(auth, "CREDENTIALS_PATH", credentials_path)
    requested_scopes = []
    monkeypatch.setattr(
        auth.InstalledAppFlow,
        "from_client_secrets_file",
        lambda _path, scopes: requested_scopes.extend(scopes) or object(),
    )
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
    assert requested_scopes == auth.GOOGLE_SERVICE_SCOPES["google_drive"]


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


def test_recovery_oauth_rejects_a_different_email_before_persistence(tmp_path, monkeypatch):
    store = GoogleAccountStore(tmp_path)
    credentials_path = tmp_path / "credentials.json"
    credentials_path.write_text("{}")
    credentials = SimpleNamespace(
        scopes=tuple(auth.SCOPES_IDENTITY + auth.SCOPES_GMAIL_READ + auth.SCOPES_GMAIL_SEND),
        to_json=lambda: "must-not-persist",
    )
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

    with pytest.raises(IntegrationConnectionError) as raised:
        auth.authorize_google_service(
            "gmail",
            expected_account_ref="broken@example.com",
            store=store,
        )

    assert raised.value.account_ref == "broken@example.com"
    assert store.list_accounts() == []
    assert not (tmp_path / "google_tokens").exists()


def test_recovery_oauth_preserves_exact_stored_account_ref_casing(tmp_path, monkeypatch):
    store = GoogleAccountStore(tmp_path)
    existing = store.upsert_authorization(
        email="User@Example.com",
        credential_json="old-token",
        scopes=tuple(auth.SCOPES_GMAIL_READ + auth.SCOPES_GMAIL_SEND),
        service="gmail",
    )
    credentials_path = tmp_path / "credentials.json"
    credentials_path.write_text("{}")
    credentials = SimpleNamespace(
        scopes=tuple(auth.SCOPES_IDENTITY + auth.SCOPES_GMAIL_READ + auth.SCOPES_GMAIL_SEND),
        to_json=lambda: "new-token",
    )
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

    connected = auth.authorize_google_service(
        "gmail",
        expected_account_ref="User@Example.com",
        store=store,
    )

    assert connected.id == existing.id
    assert connected.email == "User@Example.com"
    assert store.list_accounts()[0].email == "User@Example.com"


def test_service_oauth_rejects_absent_granted_scopes(tmp_path, monkeypatch):
    store = GoogleAccountStore(tmp_path)
    credentials_path = tmp_path / "credentials.json"
    credentials_path.write_text("{}")
    credentials = SimpleNamespace(scopes=None, to_json=lambda: "must-not-persist")
    monkeypatch.setattr(auth, "CREDENTIALS_PATH", credentials_path)
    monkeypatch.setattr(auth.InstalledAppFlow, "from_client_secrets_file", lambda *_args: object())
    monkeypatch.setattr(auth, "_run_local_oauth", lambda _flow: credentials)

    with pytest.raises(IntegrationConnectionError) as raised:
        auth.authorize_google_service("gmail", store=store)

    assert raised.value.reason == "scope_required"
    assert raised.value.required_scopes == tuple(auth.GOOGLE_SERVICE_SCOPES["gmail"])
    assert store.list_accounts() == []


def test_service_oauth_rejects_malformed_granted_scopes(tmp_path, monkeypatch):
    credentials_path = tmp_path / "credentials.json"
    credentials_path.write_text("{}")
    credentials = SimpleNamespace(scopes="openid", to_json=lambda: "must-not-persist")
    monkeypatch.setattr(auth, "CREDENTIALS_PATH", credentials_path)
    monkeypatch.setattr(auth.InstalledAppFlow, "from_client_secrets_file", lambda *_args: object())
    monkeypatch.setattr(auth, "_run_local_oauth", lambda _flow: credentials)

    with pytest.raises(IntegrationOperationError) as raised:
        auth.authorize_google_service("gmail", store=GoogleAccountStore(tmp_path))

    assert raised.value.code == "invalid_response"
    assert GoogleAccountStore(tmp_path).list_accounts() == []


def test_blank_selected_account_id_does_not_fall_back_to_email_matching(tmp_path, monkeypatch):
    store = GoogleAccountStore(tmp_path)
    store.upsert_authorization(
        email="user@example.com",
        credential_json="old-token",
        scopes=tuple(auth.SCOPES_GMAIL_READ + auth.SCOPES_GMAIL_SEND),
        service="gmail",
    )
    credentials_path = tmp_path / "credentials.json"
    credentials_path.write_text("{}")
    monkeypatch.setattr(auth, "CREDENTIALS_PATH", credentials_path)

    with pytest.raises(IntegrationConnectionError, match="selected Google account is unavailable"):
        auth.authorize_google_service("gmail", account_id="", store=store)


def test_selected_account_oauth_cannot_replace_it_with_a_different_identity(tmp_path, monkeypatch):
    store = GoogleAccountStore(tmp_path)
    existing = store.upsert_authorization(
        email="user@example.com",
        credential_json="old-token",
        scopes=tuple(auth.SCOPES_GMAIL_READ + auth.SCOPES_GMAIL_SEND),
        service="gmail",
    )
    credentials_path = tmp_path / "credentials.json"
    credentials_path.write_text("{}")
    credentials = SimpleNamespace(
        scopes=tuple(auth.SCOPES_IDENTITY + auth.SCOPES_DRIVE),
        to_json=lambda: "must-not-persist",
    )
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

    with pytest.raises(IntegrationConnectionError, match=r"must use user@example\.com"):
        auth.authorize_google_service("google_drive", account_id=existing.id, store=store)

    unchanged = store.list_accounts()
    assert len(unchanged) == 1
    assert unchanged[0].email == "user@example.com"
    assert unchanged[0].services == frozenset({"gmail"})
    assert store.token_path(unchanged[0], "gmail").read_text() == "old-token"


def test_service_oauth_types_user_authorization_failure(tmp_path, monkeypatch):
    credentials_path = tmp_path / "credentials.json"
    credentials_path.write_text("{}")
    monkeypatch.setattr(auth, "CREDENTIALS_PATH", credentials_path)
    monkeypatch.setattr(auth.InstalledAppFlow, "from_client_secrets_file", lambda *_args: object())
    monkeypatch.setattr(
        auth,
        "_run_local_oauth",
        lambda _flow: (_ for _ in ()).throw(auth.OAuth2Error("provider secret")),
    )

    with pytest.raises(IntegrationConnectionError) as raised:
        auth.authorize_google_service("gmail", store=GoogleAccountStore(tmp_path))

    assert raised.value.reason == "auth_required"
    assert "provider secret" not in str(raised.value)


@pytest.mark.parametrize("profile", [[], {}, {"email": "  "}, {"email": "not an email"}])
def test_service_oauth_rejects_invalid_profile_payload(tmp_path, monkeypatch, profile):
    store = GoogleAccountStore(tmp_path)
    credentials_path = tmp_path / "credentials.json"
    credentials_path.write_text("{}")
    credentials = SimpleNamespace(
        scopes=tuple(auth.SCOPES_IDENTITY + auth.SCOPES_GMAIL_READ + auth.SCOPES_GMAIL_SEND),
        to_json=lambda: "must-not-persist",
    )
    monkeypatch.setattr(auth, "CREDENTIALS_PATH", credentials_path)
    monkeypatch.setattr(auth.InstalledAppFlow, "from_client_secrets_file", lambda *_args: object())
    monkeypatch.setattr(auth, "_run_local_oauth", lambda _flow: credentials)
    monkeypatch.setattr(
        auth,
        "build",
        lambda *_args, **_kwargs: SimpleNamespace(
            userinfo=lambda: SimpleNamespace(get=lambda: SimpleNamespace(execute=lambda: profile)),
        ),
    )

    with pytest.raises(IntegrationOperationError) as raised:
        auth.authorize_google_service("gmail", store=store)

    assert raised.value.code == "invalid_response"
    assert store.list_accounts() == []


@pytest.mark.parametrize(
    ("status", "error_type"),
    [(401, IntegrationConnectionError), (503, IntegrationOperationError)],
)
def test_google_profile_http_failures_are_typed_without_provider_details(monkeypatch, status, error_type):
    provider_error = auth.HttpError(
        SimpleNamespace(status=status, reason="provider secret"),
        b'{"error":{"message":"provider secret"}}',
    )
    monkeypatch.setattr(
        auth,
        "build",
        lambda *_args, **_kwargs: SimpleNamespace(
            userinfo=lambda: SimpleNamespace(
                get=lambda: SimpleNamespace(execute=lambda: (_ for _ in ()).throw(provider_error)),
            ),
        ),
    )

    with pytest.raises(error_type) as raised:
        auth._google_profile_email("gmail", SimpleNamespace(), "user@example.test")

    assert "provider secret" not in str(raised.value)


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


def test_account_revocation_prevalidates_every_token_before_network_mutation(tmp_path, monkeypatch):
    store = GoogleAccountStore(tmp_path)
    account = store.upsert_authorization(
        email="user@example.com",
        credential_json="gmail-token",
        scopes=("gmail.readonly",),
        service="gmail",
    )
    account = store.upsert_authorization(
        account_id=account.id,
        email=account.email,
        credential_json="drive-token",
        scopes=("drive.file",),
        service="google_drive",
    )
    provider_calls = []
    credential_calls = 0

    def load_credentials(_path):
        nonlocal credential_calls
        credential_calls += 1
        if credential_calls == 2:
            raise ValueError("malformed second credential")
        return SimpleNamespace(refresh_token="first-token", token=None)

    monkeypatch.setattr(auth.Credentials, "from_authorized_user_file", load_credentials)
    monkeypatch.setattr(auth, "urlopen", lambda *_args, **_kwargs: provider_calls.append(True))

    with pytest.raises(IntegrationOperationError) as raised:
        auth.revoke_google_account(account, store)

    assert raised.value.code == "invalid_credentials"
    assert provider_calls == []


def test_account_revocation_reports_confirmed_partial_provider_failure(tmp_path, monkeypatch):
    store = GoogleAccountStore(tmp_path)
    account = store.upsert_authorization(
        email="user@example.com",
        credential_json="gmail-token",
        scopes=("gmail.readonly",),
        service="gmail",
    )
    account = store.upsert_authorization(
        account_id=account.id,
        email=account.email,
        credential_json="drive-token",
        scopes=("drive.file",),
        service="google_drive",
    )
    calls = 0

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

    def revoke(_request, timeout):
        nonlocal calls
        assert timeout == 15
        calls += 1
        if calls == 2:
            raise URLError("provider unavailable")
        return Response()

    monkeypatch.setattr(auth, "urlopen", revoke)

    with pytest.raises(IntegrationOperationError) as raised:
        auth.revoke_google_account(account, store)

    assert raised.value.code == "partial_revocation"
    assert calls == 2


def test_account_revocation_types_unreadable_credentials_without_deleting_them(tmp_path, monkeypatch):
    store = GoogleAccountStore(tmp_path)
    account = store.upsert_authorization(
        email="user@example.com",
        credential_json="token-secret",
        scopes=("gmail.readonly",),
        service="gmail",
    )
    token_path = store.token_path(account, "gmail")
    monkeypatch.setattr(
        auth.Credentials,
        "from_authorized_user_file",
        lambda _path: (_ for _ in ()).throw(ValueError("credential secret")),
    )

    with pytest.raises(IntegrationOperationError) as raised:
        auth.revoke_google_account(account, store)

    assert raised.value.code == "invalid_credentials"
    assert "credential secret" not in str(raised.value)
    assert token_path.read_text() == "token-secret"
    assert store.list_accounts() == [account]


def test_account_revocation_rejects_tokenless_credentials_without_provider_or_local_mutation(tmp_path, monkeypatch):
    store = GoogleAccountStore(tmp_path)
    account = store.upsert_authorization(
        email="user@example.com",
        credential_json="token-secret",
        scopes=("gmail.readonly",),
        service="gmail",
    )
    token_path = store.token_path(account, "gmail")
    provider_calls = []
    monkeypatch.setattr(
        auth.Credentials,
        "from_authorized_user_file",
        lambda _path: SimpleNamespace(refresh_token=None, token=None),
    )
    monkeypatch.setattr(auth, "urlopen", lambda *_args, **_kwargs: provider_calls.append(True))

    with pytest.raises(IntegrationOperationError) as raised:
        auth.revoke_google_account(account, store)

    assert raised.value.code == "invalid_credentials"
    assert provider_calls == []
    assert token_path.read_text() == "token-secret"
    assert store.list_accounts() == [account]


def test_account_revocation_does_not_fall_back_from_empty_refresh_token(tmp_path, monkeypatch):
    store = GoogleAccountStore(tmp_path)
    account = store.upsert_authorization(
        email="user@example.com",
        credential_json="token-secret",
        scopes=("gmail.readonly",),
        service="gmail",
    )
    provider_calls = []
    monkeypatch.setattr(
        auth.Credentials,
        "from_authorized_user_file",
        lambda _path: SimpleNamespace(refresh_token="", token="access-token"),
    )
    monkeypatch.setattr(auth, "urlopen", lambda *_args, **_kwargs: provider_calls.append(True))

    with pytest.raises(IntegrationOperationError) as raised:
        auth.revoke_google_account(account, store)

    assert raised.value.code == "invalid_credentials"
    assert provider_calls == []


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        (URLError("transport secret"), "revocation_uncertain"),
        (HTTPError("https://oauth2.googleapis.com/revoke", 503, "provider secret", {}, None), "provider_error"),
    ],
)
def test_account_revocation_types_provider_failure_without_deleting_tokens(
    tmp_path,
    monkeypatch,
    failure,
    expected_code,
):
    store = GoogleAccountStore(tmp_path)
    account = store.upsert_authorization(
        email="user@example.com",
        credential_json="token-secret",
        scopes=("gmail.readonly",),
        service="gmail",
    )
    token_path = store.token_path(account, "gmail")
    monkeypatch.setattr(
        auth.Credentials,
        "from_authorized_user_file",
        lambda _path: SimpleNamespace(refresh_token="refresh-token", token=None),
    )
    monkeypatch.setattr(auth, "urlopen", lambda *_args, **_kwargs: (_ for _ in ()).throw(failure))

    with pytest.raises(IntegrationOperationError) as raised:
        auth.revoke_google_account(account, store)

    assert raised.value.code == expected_code
    assert raised.value.retryable is True
    assert "secret" not in str(raised.value)
    assert token_path.read_text() == "token-secret"
    assert store.list_accounts() == [account]


def test_account_revocation_accepts_already_invalid_provider_token(tmp_path, monkeypatch):
    store = GoogleAccountStore(tmp_path)
    account = store.upsert_authorization(
        email="user@example.com",
        credential_json="token-secret",
        scopes=("gmail.readonly",),
        service="gmail",
    )
    monkeypatch.setattr(
        auth.Credentials,
        "from_authorized_user_file",
        lambda _path: SimpleNamespace(refresh_token="refresh-token", token=None),
    )
    monkeypatch.setattr(
        auth,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            HTTPError("https://oauth2.googleapis.com/revoke", 400, "invalid token", {}, None)
        ),
    )

    auth.revoke_google_account(account, store)
