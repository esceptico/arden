import json
import stat

import pytest

from arden.integrations.google_auth.accounts import (
    GoogleAccount,
    GoogleAccountStore,
    GoogleAccountStoreCorruption,
    GoogleAuthorization,
)


def _credential(scopes: list[str]) -> str:
    return json.dumps(
        {
            "token": "token",
            "refresh_token": "refresh",
            "token_uri": "https://oauth2.googleapis.com/token",
            "client_id": "client",
            "client_secret": "secret",
            "scopes": scopes,
        }
    )


def test_store_keeps_separate_credentials_for_each_service(tmp_path):
    store = GoogleAccountStore(tmp_path)
    first = store.upsert_authorization(
        email="user@example.com",
        credential_json=_credential(["gmail.readonly"]),
        scopes=("gmail.readonly",),
        service="gmail",
    )
    second = store.upsert_authorization(
        account_id=first.id,
        email="user@example.com",
        credential_json=_credential(["gmail.readonly", "calendar"]),
        scopes=("gmail.readonly", "calendar"),
        service="calendar",
    )

    assert second.services == frozenset({"gmail", "calendar"})
    assert len(list((tmp_path / "google_tokens").glob("*.json"))) == 2
    assert store.token_path(second, "gmail").read_text() == _credential(["gmail.readonly"])
    assert store.token_path(second, "calendar").read_text() == _credential(["gmail.readonly", "calendar"])


def test_authorizing_drive_for_an_existing_email_keeps_service_tokens_isolated(tmp_path):
    store = GoogleAccountStore(tmp_path)
    gmail_token = _credential(["gmail.readonly", "gmail.send"])
    drive_token = _credential(["drive.file", "spreadsheets"])
    account = store.upsert_authorization(
        email="user@example.com",
        credential_json=gmail_token,
        scopes=("gmail.readonly", "gmail.send"),
        service="gmail",
    )

    updated = store.upsert_authorization(
        email="user@example.com",
        credential_json=drive_token,
        scopes=("drive.file", "spreadsheets"),
        service="google_drive",
    )

    assert updated.id == account.id
    assert store.token_path(updated, "gmail").read_text() == gmail_token
    assert store.token_path(updated, "google_drive").read_text() == drive_token


def test_store_rejects_version_one_flat_account(tmp_path):
    (tmp_path / "google_accounts.json").write_text(
        json.dumps(
            {
                "version": 1,
                "accounts": [
                    {
                        "id": "acct-1",
                        "email": "user@example.com",
                        "token_file": "google_tokens/acct-1.json",
                        "scopes": ["gmail.readonly", "calendar"],
                        "services": ["gmail", "calendar"],
                    }
                ],
            }
        )
    )
    store = GoogleAccountStore(tmp_path)

    with pytest.raises(GoogleAccountStoreCorruption, match="canonical version 2"):
        store.list_accounts()


def test_disconnect_removes_only_local_service_binding(tmp_path):
    store = GoogleAccountStore(tmp_path)
    account = store.upsert_authorization(
        email="user@example.com",
        credential_json=_credential(["gmail.readonly", "calendar"]),
        scopes=("gmail.readonly", "calendar"),
        service="gmail",
    )
    account = store.upsert_authorization(
        account_id=account.id,
        email="user@example.com",
        credential_json=_credential(["calendar"]),
        scopes=("calendar",),
        service="calendar",
    )
    gmail_token = store.token_path(account, "gmail")
    calendar_token = store.token_path(account, "calendar")

    disconnected = store.disconnect_service(account.id, "gmail")

    assert store.accounts_for("gmail") == []
    assert [item.id for item in store.accounts_for("calendar")] == [account.id]
    assert not gmail_token.exists()
    assert calendar_token.exists()
    assert disconnected.authorization("calendar").token_file == account.authorization("calendar").token_file


def test_disconnect_deletes_unreferenced_service_token(tmp_path):
    store = GoogleAccountStore(tmp_path)
    account = store.upsert_authorization(
        email="user@example.com",
        credential_json=_credential(["gmail.readonly"]),
        scopes=("gmail.readonly",),
        service="gmail",
    )
    token_path = store.token_path(account, "gmail")

    disconnected = store.disconnect_service(account.id, "gmail")

    assert disconnected.services == frozenset()
    assert not token_path.exists()


def test_disconnect_keeps_token_referenced_by_another_service(tmp_path):
    shared_token_path = tmp_path / "google_tokens" / "acct-1-shared.json"
    shared_token_path.parent.mkdir()
    shared_token_path.write_text("shared-token")
    (tmp_path / "google_accounts.json").write_text(
        json.dumps(
            {
                "version": 2,
                "accounts": [
                    {
                        "id": "acct-1",
                        "email": "user@example.com",
                        "authorizations": {
                            "gmail": {
                                "token_file": "google_tokens/acct-1-shared.json",
                                "scopes": ["gmail.readonly"],
                            },
                            "calendar": {
                                "token_file": "google_tokens/acct-1-shared.json",
                                "scopes": ["calendar"],
                            },
                        },
                    }
                ],
            }
        )
    )
    store = GoogleAccountStore(tmp_path)

    disconnected = store.disconnect_service("acct-1", "gmail")

    assert disconnected.services == frozenset({"calendar"})
    assert store.token_path(disconnected, "calendar") == shared_token_path
    assert shared_token_path.exists()


def test_disconnect_accepts_missing_unreferenced_service_token(tmp_path):
    store = GoogleAccountStore(tmp_path)
    account = store.upsert_authorization(
        email="user@example.com",
        credential_json=_credential(["gmail.readonly"]),
        scopes=("gmail.readonly",),
        service="gmail",
    )
    store.token_path(account, "gmail").unlink()

    disconnected = store.disconnect_service(account.id, "gmail")

    assert disconnected.services == frozenset()
    assert store.accounts_for("gmail") == []


def test_disconnect_keeps_authorization_when_token_deletion_fails(tmp_path, monkeypatch):
    store = GoogleAccountStore(tmp_path)
    account = store.upsert_authorization(
        email="user@example.com",
        credential_json=_credential(["gmail.readonly"]),
        scopes=("gmail.readonly",),
        service="gmail",
    )
    token_path = store.token_path(account, "gmail")
    original_unlink = type(token_path).unlink

    def fail_unlink(path, *, missing_ok):
        if path == token_path:
            raise OSError("read-only filesystem")
        return original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(type(token_path), "unlink", fail_unlink)

    with pytest.raises(OSError, match="read-only filesystem"):
        store.disconnect_service(account.id, "gmail")

    assert [item.id for item in store.accounts_for("gmail")] == [account.id]


def test_remove_account_deletes_index_and_token(tmp_path):
    store = GoogleAccountStore(tmp_path)
    account = store.upsert_authorization(
        email="user@example.com",
        credential_json=_credential(["calendar"]),
        scopes=("calendar",),
        service="calendar",
    )

    token_path = store.token_path(account, "calendar")
    removed = store.remove_account(account.id)

    assert removed.id == account.id
    assert store.list_accounts() == []
    assert not token_path.exists()


def test_store_persists_only_canonical_version_two_fields(tmp_path):
    store = GoogleAccountStore(tmp_path)
    account = store.upsert_authorization(
        email="user@example.com",
        credential_json="token",
        scopes=("gmail.send", "gmail.readonly"),
        service="gmail",
    )

    payload = json.loads(store.index_path.read_text())

    assert set(payload) == {"version", "accounts"}
    assert payload["version"] == 2
    assert set(payload["accounts"][0]) == {"id", "email", "authorizations"}
    assert set(payload["accounts"][0]["authorizations"]["gmail"]) == {"token_file", "scopes"}
    assert payload["accounts"][0]["authorizations"]["gmail"]["scopes"] == [
        "gmail.readonly",
        "gmail.send",
    ]
    assert store.list_accounts() == [account]


@pytest.mark.parametrize(
    "payload",
    [
        "{not json",
        json.dumps({"version": 3, "accounts": []}),
        json.dumps({"version": 2, "accounts": [], "unknown": True}),
    ],
)
def test_store_rejects_malformed_or_noncanonical_index(tmp_path, payload):
    (tmp_path / "google_accounts.json").write_text(payload)

    with pytest.raises(GoogleAccountStoreCorruption):
        GoogleAccountStore(tmp_path).list_accounts()


def test_store_rejects_unsafe_token_path(tmp_path):
    (tmp_path / "google_accounts.json").write_text(
        json.dumps(
            {
                "version": 2,
                "accounts": [
                    {
                        "id": "acct-1",
                        "email": "user@example.com",
                        "authorizations": {
                            "gmail": {"token_file": "../outside.json", "scopes": ["gmail.readonly"]}
                        },
                    }
                ],
            }
        )
    )

    with pytest.raises(GoogleAccountStoreCorruption, match="canonical version 2"):
        GoogleAccountStore(tmp_path).list_accounts()


def test_consumer_account_rejects_unsafe_token_path():
    with pytest.raises(ValueError, match="token_file"):
        GoogleAccount(
            id="acct-1",
            email="user@example.com",
            authorizations=(GoogleAuthorization("gmail", "../outside.json", ("gmail.readonly",)),),
        )


def test_store_rejects_duplicate_accounts(tmp_path):
    account = {"id": "acct-1", "email": "user@example.com", "authorizations": {}}
    (tmp_path / "google_accounts.json").write_text(
        json.dumps({"version": 2, "accounts": [account, account]})
    )

    with pytest.raises(GoogleAccountStoreCorruption, match="canonical version 2"):
        GoogleAccountStore(tmp_path).list_accounts()


def test_store_rejects_duplicate_account_emails(tmp_path):
    accounts = [
        {"id": "acct-1", "email": "User@example.com", "authorizations": {}},
        {"id": "acct-2", "email": "user@example.com", "authorizations": {}},
    ]
    (tmp_path / "google_accounts.json").write_text(
        json.dumps({"version": 2, "accounts": accounts})
    )

    with pytest.raises(GoogleAccountStoreCorruption, match="canonical version 2"):
        GoogleAccountStore(tmp_path).list_accounts()


def test_store_requires_verified_account_email(tmp_path):
    account = {"id": "acct-1", "email": None, "authorizations": {}}
    (tmp_path / "google_accounts.json").write_text(
        json.dumps({"version": 2, "accounts": [account]})
    )

    with pytest.raises(GoogleAccountStoreCorruption, match="canonical version 2"):
        GoogleAccountStore(tmp_path).list_accounts()


@pytest.mark.parametrize("email", ["not an email", " user@example.com"])
def test_store_rejects_invalid_account_email(tmp_path, email):
    account = {"id": "acct-1", "email": email, "authorizations": {}}
    (tmp_path / "google_accounts.json").write_text(
        json.dumps({"version": 2, "accounts": [account]})
    )

    with pytest.raises(GoogleAccountStoreCorruption, match="canonical version 2"):
        GoogleAccountStore(tmp_path).list_accounts()


def test_store_rejects_duplicate_json_keys(tmp_path):
    (tmp_path / "google_accounts.json").write_text('{"version":2,"version":2,"accounts":[]}')

    with pytest.raises(GoogleAccountStoreCorruption, match="duplicate key"):
        GoogleAccountStore(tmp_path).list_accounts()


def test_remove_account_keeps_index_when_token_deletion_fails(tmp_path, monkeypatch):
    store = GoogleAccountStore(tmp_path)
    account = store.upsert_authorization(
        email="user@example.com",
        credential_json="token",
        scopes=("calendar",),
        service="calendar",
    )
    token_path = store.token_path(account, "calendar")
    original_unlink = type(token_path).unlink

    def fail_unlink(path, *, missing_ok):
        if path == token_path:
            raise OSError("read-only filesystem")
        return original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(type(token_path), "unlink", fail_unlink)

    with pytest.raises(OSError, match="read-only filesystem"):
        store.remove_account(account.id)

    assert store.list_accounts() == [account]
    assert token_path.exists()


def test_upsert_restores_previous_credential_when_index_write_fails(tmp_path, monkeypatch):
    store = GoogleAccountStore(tmp_path)
    account = store.upsert_authorization(
        email="user@example.com",
        credential_json="old-token",
        scopes=("gmail.readonly",),
        service="gmail",
    )
    token_path = store.token_path(account, "gmail")
    token_path.chmod(0o640)
    monkeypatch.setattr(
        store,
        "_write_index",
        lambda _index: (_ for _ in ()).throw(OSError("index write failed")),
    )

    with pytest.raises(OSError, match="index write failed"):
        store.upsert_authorization(
            account_id=account.id,
            email=account.email,
            credential_json="new-token",
            scopes=("gmail.readonly", "gmail.send"),
            service="gmail",
        )

    assert token_path.read_text() == "old-token"
    assert stat.S_IMODE(token_path.stat().st_mode) == 0o600


def test_store_snapshot_restores_index_tokens_and_private_modes(tmp_path):
    store = GoogleAccountStore(tmp_path)
    account = store.upsert_authorization(
        email="user@example.com",
        credential_json="gmail-token",
        scopes=("gmail.readonly",),
        service="gmail",
    )
    gmail_path = store.token_path(account, "gmail")
    gmail_path.chmod(0o640)
    snapshot = store.snapshot()

    updated = store.upsert_authorization(
        account_id=account.id,
        email=account.email,
        credential_json="drive-token",
        scopes=("drive.file",),
        service="google_drive",
    )
    drive_path = store.token_path(updated, "google_drive")
    store.restore(snapshot)

    restored = store.list_accounts()
    assert len(restored) == 1
    assert restored[0].services == frozenset({"gmail"})
    assert gmail_path.read_text() == "gmail-token"
    assert stat.S_IMODE(gmail_path.stat().st_mode) == 0o600
    assert not drive_path.exists()


def test_upsert_removes_new_credential_when_index_write_fails(tmp_path, monkeypatch):
    store = GoogleAccountStore(tmp_path)
    monkeypatch.setattr(
        store,
        "_write_index",
        lambda _index: (_ for _ in ()).throw(OSError("index write failed")),
    )

    with pytest.raises(OSError, match="index write failed"):
        store.upsert_authorization(
            email="user@example.com",
            credential_json="new-token",
            scopes=("gmail.readonly",),
            service="gmail",
        )

    assert list((tmp_path / "google_tokens").glob("*.json")) == []
    assert not store.index_path.exists()
