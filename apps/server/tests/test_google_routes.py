from types import SimpleNamespace

from fastapi.testclient import TestClient

from arden.integrations.google_auth.accounts import GoogleAccount
from arden.server.app import app
from arden.server.runtime import get_runtime


class Store:
    def __init__(self):
        self.disconnected = []
        self.removed = []
        self.account = GoogleAccount(
            id="acct-1",
            email="user@example.com",
            token_file="google_tokens/acct-1.json",
            scopes=("scope",),
            services=frozenset({"gmail", "calendar"}),
        )

    def list_accounts(self):
        return [self.account]

    def disconnect_service(self, account_id, service):
        self.disconnected.append((account_id, service))
        return self.account

    def remove_account(self, account_id):
        self.removed.append(account_id)
        return self.account

    def token_path(self, _account):
        return SimpleNamespace()


def _runtime():
    calls = []

    async def sync_google_sources():
        calls.append("sync")

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


def test_disconnect_service_does_not_revoke_account(monkeypatch):
    import arden.server.routers.google as google_router

    store = Store()
    runtime = _runtime()
    revoke_calls = []
    monkeypatch.setattr(google_router, "google_account_store", lambda: store)
    monkeypatch.setattr(google_router, "revoke_google_account", lambda *_args: revoke_calls.append(True))
    app.dependency_overrides[get_runtime] = lambda: runtime
    try:
        response = TestClient(app).delete("/google/gmail/accounts/acct-1")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert store.disconnected == [("acct-1", "gmail")]
    assert revoke_calls == []


def test_remove_account_revokes_before_local_delete(monkeypatch):
    import arden.server.routers.google as google_router

    store = Store()
    runtime = _runtime()
    events = []
    monkeypatch.setattr(google_router, "google_account_store", lambda: store)
    monkeypatch.setattr(
        google_router, "revoke_google_account", lambda account, _store: events.append(("revoke", account.id))
    )
    original_remove = store.remove_account
    store.remove_account = lambda account_id: events.append(("remove", account_id)) or original_remove(account_id)
    app.dependency_overrides[get_runtime] = lambda: runtime
    try:
        response = TestClient(app).delete("/google/accounts/acct-1")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert events == [("revoke", "acct-1"), ("remove", "acct-1")]
