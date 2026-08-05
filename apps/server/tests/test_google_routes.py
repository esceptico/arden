from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from arden.integrations.base import IntegrationConnectionError, IntegrationOperationError
from arden.integrations.google_auth.accounts import GoogleAccount, GoogleAuthorization
from arden.server.app import app
from arden.server.runtime import get_runtime


class Store:
    def __init__(self):
        self.disconnected = []
        self.removed = []
        self.connected = False
        self.is_removed = False
        self.account = GoogleAccount(
            id="acct-1",
            email="user@example.com",
            authorizations=(
                GoogleAuthorization("gmail", "google_tokens/acct-1-gmail.json", ("scope",)),
                GoogleAuthorization("calendar", "google_tokens/acct-1-calendar.json", ("scope",)),
            ),
        )

    def list_accounts(self):
        return [] if self.is_removed else [self.account]

    def snapshot(self):
        return (list(self.disconnected), list(self.removed), self.connected, self.is_removed)

    def restore(self, snapshot):
        self.disconnected, self.removed, self.connected, self.is_removed = snapshot

    def disconnect_service(self, account_id, service):
        self.disconnected.append((account_id, service))
        return self.account

    def remove_account(self, account_id):
        self.removed.append(account_id)
        self.is_removed = True
        return self.account


@pytest.fixture(autouse=True)
def isolated_google_store(monkeypatch):
    import arden.server.routers.google as google_router

    store = Store()
    monkeypatch.setattr(google_router, "google_account_store", lambda: store)
    return store


def _runtime(*, failures: int = 0):
    calls = []

    async def sync_google_sources():
        calls.append("sync")
        if len(calls) <= failures:
            raise RuntimeError("sync failed")

    return SimpleNamespace(sync_google_sources=sync_google_sources, calls=calls)


def test_google_connect_authorizes_exact_integration(monkeypatch):
    import arden.server.routers.google as google_router

    calls = []
    runtime = _runtime()
    account = Store().account
    monkeypatch.setattr(
        google_router, "authorize_google_service", lambda service, **_kwargs: calls.append(service) or account
    )
    app.dependency_overrides[get_runtime] = lambda: runtime
    try:
        response = TestClient(app).post("/google/google_drive/connect", json={})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert calls == ["google_drive"]
    assert runtime.calls == ["sync"]
    assert response.json()["account"]["services"] == ["calendar", "gmail"]


def test_google_connect_forwards_existing_account(monkeypatch):
    import arden.server.routers.google as google_router

    calls = []
    runtime = _runtime()
    account = Store().account
    monkeypatch.setattr(
        google_router,
        "authorize_google_service",
        lambda service, **kwargs: calls.append((service, kwargs["account_id"])) or account,
    )
    app.dependency_overrides[get_runtime] = lambda: runtime
    try:
        response = TestClient(app).post(
            "/google/google_drive/connect",
            json={"account_id": "acct-1"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert calls == [("google_drive", "acct-1")]


def test_google_connect_forwards_expected_account_reference(monkeypatch):
    import arden.server.routers.google as google_router

    calls = []
    runtime = _runtime()
    account = Store().account
    monkeypatch.setattr(
        google_router,
        "authorize_google_service",
        lambda service, **kwargs: calls.append((service, kwargs["expected_account_ref"])) or account,
    )
    app.dependency_overrides[get_runtime] = lambda: runtime
    try:
        response = TestClient(app).post(
            "/google/gmail/connect",
            json={"account_ref": "user@example.com"},
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert calls == [("gmail", "user@example.com")]


def test_google_connect_exposes_typed_connection_error(monkeypatch):
    import arden.server.routers.google as google_router

    def fail(*_args, **_kwargs):
        raise IntegrationConnectionError(
            integration_id="gmail",
            reason="auth_required",
            detail="Reconnect the requested Gmail account.",
        )

    monkeypatch.setattr(google_router, "authorize_google_service", fail)
    app.dependency_overrides[get_runtime] = _runtime
    try:
        response = TestClient(app).post("/google/gmail/connect", json={})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert response.json() == {"detail": "Reconnect the requested Gmail account."}


def test_google_connect_exposes_only_safe_provider_error(monkeypatch):
    import arden.server.routers.google as google_router

    def fail(*_args, **_kwargs):
        error = IntegrationOperationError(code="provider_error", safe_message="Google provider request failed.")
        error.__cause__ = RuntimeError("provider secret")
        raise error

    monkeypatch.setattr(google_router, "authorize_google_service", fail)
    app.dependency_overrides[get_runtime] = _runtime
    try:
        response = TestClient(app).post("/google/gmail/connect", json={})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 502
    assert response.json() == {"detail": "Google provider request failed."}
    assert "provider secret" not in response.text


def test_google_connect_hides_untyped_provider_error(monkeypatch):
    import arden.server.routers.google as google_router

    def fail(*_args, **_kwargs):
        raise RuntimeError("provider secret")

    monkeypatch.setattr(google_router, "authorize_google_service", fail)
    app.dependency_overrides[get_runtime] = _runtime
    try:
        response = TestClient(app, raise_server_exceptions=False).post("/google/gmail/connect", json={})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    assert "provider secret" not in response.text


def test_google_connect_rejects_non_email_account_reference():
    app.dependency_overrides[get_runtime] = _runtime
    try:
        response = TestClient(app).post("/google/gmail/connect", json={"account_ref": "internal-id"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_google_connect_rejects_unknown_request_fields():
    app.dependency_overrides[get_runtime] = _runtime
    try:
        response = TestClient(app).post("/google/gmail/connect", json={"legacy_account": "user@example.com"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 422


def test_google_connect_rolls_back_persistence_when_runtime_sync_fails(monkeypatch, isolated_google_store):
    import arden.server.routers.google as google_router

    runtime = _runtime(failures=1)

    def authorize(_service, **_kwargs):
        isolated_google_store.connected = True
        return isolated_google_store.account

    monkeypatch.setattr(google_router, "authorize_google_service", authorize)
    app.dependency_overrides[get_runtime] = lambda: runtime
    try:
        response = TestClient(app, raise_server_exceptions=False).post("/google/gmail/connect", json={})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    assert isolated_google_store.connected is False
    assert runtime.calls == ["sync", "sync"]


def test_google_connect_exposes_only_safe_configuration_error(monkeypatch):
    import arden.server.routers.google as google_router

    def fail(*_args, **_kwargs):
        error = IntegrationOperationError(
            code="configuration_error",
            safe_message="Google OAuth credentials are invalid or unavailable.",
        )
        error.__cause__ = ValueError("/private/google-client.json")
        raise error

    monkeypatch.setattr(google_router, "authorize_google_service", fail)
    app.dependency_overrides[get_runtime] = _runtime
    try:
        response = TestClient(app).post("/google/gmail/connect", json={})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 400
    assert response.json() == {"detail": "Google OAuth credentials are invalid or unavailable."}
    assert "/private/google-client.json" not in response.text


def test_disconnect_service_does_not_revoke_account(monkeypatch):
    import arden.server.routers.google as google_router

    store = Store()
    runtime = _runtime()
    revoke_calls = []
    monkeypatch.setattr(google_router, "google_account_store", lambda: store)
    monkeypatch.setattr(
        google_router,
        "execute_google_account_revocation",
        lambda *_args: revoke_calls.append(True),
    )
    app.dependency_overrides[get_runtime] = lambda: runtime
    try:
        response = TestClient(app).delete("/google/gmail/accounts/acct-1")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert store.disconnected == [("acct-1", "gmail")]
    assert revoke_calls == []


def test_disconnect_rolls_back_persistence_when_runtime_sync_fails(monkeypatch):
    import arden.server.routers.google as google_router

    store = Store()
    runtime = _runtime(failures=1)
    monkeypatch.setattr(google_router, "google_account_store", lambda: store)
    app.dependency_overrides[get_runtime] = lambda: runtime
    try:
        response = TestClient(app, raise_server_exceptions=False).delete("/google/gmail/accounts/acct-1")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    assert store.disconnected == []
    assert runtime.calls == ["sync", "sync"]


def test_remove_account_syncs_local_delete_before_revocation(monkeypatch):
    import arden.server.routers.google as google_router

    store = Store()
    runtime = _runtime()
    events = []
    monkeypatch.setattr(google_router, "google_account_store", lambda: store)
    monkeypatch.setattr(
        google_router,
        "prepare_google_account_revocation",
        lambda account, _store: events.append(("prepare", account.id)) or "plan",
    )
    monkeypatch.setattr(
        google_router,
        "execute_google_account_revocation",
        lambda plan: events.append(("revoke", plan)),
    )
    original_remove = store.remove_account
    store.remove_account = lambda account_id: events.append(("remove", account_id)) or original_remove(account_id)
    app.dependency_overrides[get_runtime] = lambda: runtime
    try:
        response = TestClient(app).delete("/google/accounts/acct-1")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert events == [("prepare", "acct-1"), ("remove", "acct-1"), ("revoke", "plan")]
    assert runtime.calls == ["sync"]


def test_remove_account_rolls_back_before_any_revocation_when_runtime_sync_fails(monkeypatch):
    import arden.server.routers.google as google_router

    store = Store()
    runtime = _runtime(failures=1)
    revoked = []
    monkeypatch.setattr(google_router, "google_account_store", lambda: store)
    monkeypatch.setattr(google_router, "prepare_google_account_revocation", lambda *_args: "plan")
    monkeypatch.setattr(
        google_router,
        "execute_google_account_revocation",
        lambda plan: revoked.append(plan),
    )
    app.dependency_overrides[get_runtime] = lambda: runtime
    try:
        response = TestClient(app, raise_server_exceptions=False).delete("/google/accounts/acct-1")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    assert store.removed == []
    assert store.is_removed is False
    assert revoked == []
    assert runtime.calls == ["sync", "sync"]


def test_remove_account_reports_provider_failure_after_local_removal(monkeypatch):
    import arden.server.routers.google as google_router

    store = Store()
    runtime = _runtime()

    def fail_revocation(*_args):
        error = IntegrationOperationError(
            code="provider_error",
            safe_message="Google authorization revocation failed.",
            retryable=True,
        )
        error.__cause__ = OSError("provider secret")
        raise error

    monkeypatch.setattr(google_router, "google_account_store", lambda: store)
    monkeypatch.setattr(google_router, "prepare_google_account_revocation", lambda *_args: "plan")
    monkeypatch.setattr(google_router, "execute_google_account_revocation", fail_revocation)
    app.dependency_overrides[get_runtime] = lambda: runtime
    try:
        response = TestClient(app).delete("/google/accounts/acct-1")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 502
    assert response.json() == {
        "detail": "The Google account was removed locally. Google authorization revocation failed."
    }
    assert "provider secret" not in response.text
    assert store.removed == ["acct-1"]
    assert runtime.calls == ["sync"]


def test_remove_account_reports_unreadable_credentials_without_local_delete(monkeypatch):
    import arden.server.routers.google as google_router

    store = Store()
    runtime = _runtime()
    monkeypatch.setattr(google_router, "google_account_store", lambda: store)
    monkeypatch.setattr(
        google_router,
        "prepare_google_account_revocation",
        lambda *_args: (_ for _ in ()).throw(
            IntegrationOperationError(
                code="invalid_credentials",
                safe_message="Google authorization credentials could not be read for revocation.",
            )
        ),
    )
    app.dependency_overrides[get_runtime] = lambda: runtime
    try:
        response = TestClient(app).delete("/google/accounts/acct-1")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 409
    assert store.removed == []
    assert runtime.calls == []


def test_remove_account_keeps_local_credentials_on_unknown_failure(monkeypatch):
    import arden.server.routers.google as google_router

    store = Store()
    runtime = _runtime()
    monkeypatch.setattr(google_router, "google_account_store", lambda: store)
    monkeypatch.setattr(
        google_router,
        "prepare_google_account_revocation",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("provider secret")),
    )
    app.dependency_overrides[get_runtime] = lambda: runtime
    try:
        response = TestClient(app, raise_server_exceptions=False).delete("/google/accounts/acct-1")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    assert "provider secret" not in response.text
    assert store.removed == []
    assert runtime.calls == []
