from types import SimpleNamespace

from fastapi.testclient import TestClient

from arden.integrations.google_auth import auth as google_auth
from arden.server.app import app
from arden.server.runtime import get_runtime

INSTALLED_CLIENT = {
    "installed": {
        "client_id": "desktop-client.apps.googleusercontent.com",
        "client_secret": "shh",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token",
        "redirect_uris": ["http://localhost"],
    }
}


def test_drive_scopes_cover_identity_metadata_docs_and_sheets():
    assert google_auth.scopes_for_google_service("google_drive") == [
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/drive.metadata.readonly",
        "https://www.googleapis.com/auth/drive.file",
        "https://www.googleapis.com/auth/documents",
        "https://www.googleapis.com/auth/spreadsheets",
    ]


def test_google_preflight_reports_missing_credentials_without_throwing(monkeypatch, tmp_path):
    monkeypatch.setattr(google_auth, "CREDENTIALS_PATH", tmp_path / "gmail_credentials.json")

    response = TestClient(app).post("/setup/google/preflight", json={"service_choice": "email"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["credentials"]["exists"] is False
    assert "Google credentials not found at" in body["warnings"][0]
    assert body["scopes"] == [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.send",
    ]


def test_google_preflight_accepts_exact_drive_integration(monkeypatch, tmp_path):
    monkeypatch.setattr(google_auth, "CREDENTIALS_PATH", tmp_path / "gmail_credentials.json")

    response = TestClient(app).post(
        "/setup/google/preflight",
        json={"integration_id": "google_drive"},
    )

    assert response.status_code == 200
    assert response.json()["scopes"] == google_auth.scopes_for_google_service("google_drive")


def test_google_credentials_rejects_web_client(monkeypatch, tmp_path):
    monkeypatch.setattr(google_auth, "CREDENTIALS_PATH", tmp_path / "gmail_credentials.json")

    response = TestClient(app).post(
        "/setup/google/credentials",
        json={
            "json": {
                "web": {
                    "client_id": "web-client.apps.googleusercontent.com",
                    "client_secret": "shh",
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                }
            }
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Use OAuth client type Desktop app, not Web application."


def test_google_credentials_accepts_installed_client_json_and_masks_secret(monkeypatch, tmp_path):
    target = tmp_path / "gmail_credentials.json"
    monkeypatch.setattr(google_auth, "CREDENTIALS_PATH", target)

    response = TestClient(app).post("/setup/google/credentials", json={"json": INSTALLED_CLIENT})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "saved"
    assert body["credentials"]["client_id"] == "desktop-client.apps.googleusercontent.com"
    assert body["credentials"]["client_type"] == "installed"
    assert body["credentials"]["valid"] is True
    assert "client_secret" not in body["credentials"]
    assert "client_secret" not in response.text
    assert target.exists()


def test_slack_verify_rejects_wrong_prefix_without_network():
    response = TestClient(app).post(
        "/setup/slack/verify",
        json={"service_id": "slack_bot_token", "api_key": "xoxp-user-token"},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "slack_bot_token requires xoxb-"


def test_setup_status_flattens_google_slack_mcp_shapes(monkeypatch):
    import arden.server.routers.setup as setup_router

    class Provider:
        def __init__(self, id, label, kind, status, detail, tool_count):
            self.id = id
            self.label = label
            self.kind = kind
            self.health = SimpleNamespace(status=status, detail=detail)
            self.tool_count = tool_count

    runtime = SimpleNamespace(
        config=SimpleNamespace(
            integration_enabled=lambda service: service in {"gmail", "calendar"},
            slack_bot_token="xoxb-secret",
            slack_user_token=None,
            mcp_servers={"linear": {"transport": "http", "url": "https://example.test/mcp"}},
            tool_overrides={},
        ),
        integrations=SimpleNamespace(
            integrations={
                "slack": SimpleNamespace(
                    service_fields=[
                        SimpleNamespace(key="slack_bot_token", label="Slack Bot Token", env_var="SLACK_BOT_TOKEN"),
                        SimpleNamespace(key="slack_user_token", label="Slack User Token", env_var="SLACK_USER_TOKEN"),
                    ]
                )
            },
            list_providers=lambda: [
                Provider("gmail", "Gmail", "native", "connected", None, 3),
                Provider("calendar", "Calendar", "native", "error", "missing scope", 2),
                Provider("slack", "Slack", "native", "connected", None, 9),
            ],
        ),
        mcp_manager=SimpleNamespace(
            sessions={},
            errors={"linear": "offline"},
            list_providers=lambda: [Provider("linear", "linear", "mcp", "error", "offline", 0)],
        ),
    )
    app.dependency_overrides[get_runtime] = lambda: runtime
    monkeypatch.setattr(
        setup_router,
        "google_credentials_status",
        lambda: {
            "path": "/tmp/gmail_credentials.json",
            "exists": True,
            "valid": True,
            "client_id": "client-id",
            "client_type": "installed",
            "error": None,
        },
    )
    monkeypatch.setattr(
        setup_router,
        "_gmail_accounts",
        lambda: [
            {
                "email": "user@example.com",
                "token_file": "gmail_token_user@example.com.json",
                "has_send_scope": True,
                "error": None,
            }
        ],
    )
    monkeypatch.setattr(
        setup_router,
        "_calendar_token_statuses",
        lambda: [{"token_file": "calendar_token.json", "has_calendar_scope": True, "error": None}],
    )
    monkeypatch.setattr(
        setup_router,
        "_google_accounts",
        lambda: [
            {
                "id": "acct-1",
                "email": "user@example.com",
                "services": ["gmail", "google_drive"],
                "scopes": ["scope"],
            }
        ],
    )

    try:
        response = TestClient(app).get("/setup/status")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    body = response.json()
    assert body["google"]["enabled"] is True
    assert body["google"]["credentials"]["client_id"] == "client-id"
    assert body["google"]["accounts"][0]["email"] == "user@example.com"
    assert body["google"]["google_accounts"][0]["services"] == ["gmail", "google_drive"]
    assert [p["id"] for p in body["google"]["provider_statuses"]] == ["gmail", "calendar"]
    assert [s["id"] for s in body["slack"]["services"]] == ["slack_bot_token", "slack_user_token"]
    assert body["slack"]["provider_status"]["id"] == "slack"
    assert body["mcp"]["servers"][0]["name"] == "linear"
    assert body["mcp"]["provider_statuses"][0]["kind"] == "mcp"


def test_google_status_reads_gmail_tokens_passively_without_oauth(monkeypatch, tmp_path):
    import arden.server.routers.setup as setup_router

    token = tmp_path / "gmail_token_user@example.com.json"
    token.write_text(
        '{"token":"ya29.expired","client_id":"client-id","client_secret":"secret",'
        '"refresh_token":null,"scopes":["https://www.googleapis.com/auth/gmail.send"],'
        '"expiry":"2000-01-01T00:00:00Z"}'
    )
    monkeypatch.setattr(google_auth, "ARDEN_DIR", tmp_path)

    def fail_oauth(*args, **kwargs):
        raise AssertionError("status must not start OAuth")

    monkeypatch.setattr(google_auth.InstalledAppFlow, "from_client_secrets_file", fail_oauth)

    accounts = setup_router._gmail_accounts()

    assert accounts == [
        {
            "email": "user@example.com",
            "token_file": "gmail_token_user@example.com.json",
            "has_send_scope": True,
            "error": "Google token is invalid and has no refresh token. Re-run setup.",
        }
    ]


def test_google_status_reads_calendar_tokens_passively_without_oauth(monkeypatch, tmp_path):
    import arden.server.routers.setup as setup_router

    token = tmp_path / "calendar_token.json"
    token.write_text(
        '{"token":"ya29.expired","client_id":"client-id","client_secret":"secret",'
        '"refresh_token":null,"scopes":["https://www.googleapis.com/auth/calendar"],'
        '"expiry":"2000-01-01T00:00:00Z"}'
    )
    monkeypatch.setattr(google_auth, "ARDEN_DIR", tmp_path)

    def fail_oauth(*args, **kwargs):
        raise AssertionError("status must not start OAuth")

    monkeypatch.setattr(google_auth.InstalledAppFlow, "from_client_secrets_file", fail_oauth)

    statuses = setup_router._calendar_token_statuses()

    assert statuses == [
        {
            "token_file": "calendar_token.json",
            "has_calendar_scope": True,
            "error": "Google token is invalid and has no refresh token. Re-run setup.",
        }
    ]
