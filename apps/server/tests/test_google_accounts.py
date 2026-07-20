import json

from ntrp.integrations.google_auth.accounts import GoogleAccountStore


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


def test_store_binds_services_without_duplicating_credential(tmp_path):
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
    assert len(list((tmp_path / "google_tokens").glob("*.json"))) == 1
    assert store.token_path(second).read_text() == _credential(["gmail.readonly", "calendar"])


def test_disconnect_removes_only_local_service_binding(tmp_path):
    store = GoogleAccountStore(tmp_path)
    account = store.upsert_authorization(
        email="user@example.com",
        credential_json=_credential(["gmail.readonly", "calendar"]),
        scopes=("gmail.readonly", "calendar"),
        service="gmail",
    )
    store.bind_service(account.id, "calendar")

    store.disconnect_service(account.id, "gmail")

    assert store.accounts_for("gmail") == []
    assert [item.id for item in store.accounts_for("calendar")] == [account.id]
    assert store.token_path(account).exists()


def test_remove_account_deletes_index_and_token(tmp_path):
    store = GoogleAccountStore(tmp_path)
    account = store.upsert_authorization(
        email="user@example.com",
        credential_json=_credential(["calendar"]),
        scopes=("calendar",),
        service="calendar",
    )

    token_path = store.token_path(account)
    removed = store.remove_account(account.id)

    assert removed.id == account.id
    assert store.list_accounts() == []
    assert not token_path.exists()


def test_legacy_migration_is_idempotent_and_classifies_scopes(tmp_path):
    scopes = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/calendar",
    ]
    legacy = tmp_path / "gmail_token_user@example.com.json"
    legacy.write_text(_credential(scopes))
    store = GoogleAccountStore(tmp_path)

    store.migrate_legacy()
    store.migrate_legacy()

    [account] = store.list_accounts()
    assert account.email == "user@example.com"
    assert account.services == frozenset({"gmail", "calendar"})
    assert legacy.exists()
    assert len(list((tmp_path / "google_tokens").glob("*.json"))) == 1


def test_legacy_migration_never_overwrites_newer_canonical_authorization(tmp_path):
    legacy_scopes = ["https://www.googleapis.com/auth/gmail.readonly"]
    expanded_scopes = [*legacy_scopes, "https://www.googleapis.com/auth/spreadsheets"]
    legacy = tmp_path / "gmail_token_user@example.com.json"
    legacy.write_text(_credential(legacy_scopes))
    store = GoogleAccountStore(tmp_path)
    store.migrate_legacy()
    [account] = store.list_accounts()
    expanded_token = _credential(expanded_scopes)
    store.upsert_authorization(
        account_id=account.id,
        email=account.email,
        credential_json=expanded_token,
        scopes=tuple(expanded_scopes),
        service="google_drive",
    )

    store.migrate_legacy()

    [updated] = store.list_accounts()
    assert updated.scopes == tuple(expanded_scopes)
    assert updated.services == frozenset({"gmail", "google_drive"})
    assert store.token_path(updated).read_text() == expanded_token
