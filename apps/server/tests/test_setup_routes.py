import json
import stat
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from arden.integrations.google_auth import auth as google_auth
from arden.integrations.google_auth import credentials as google_credentials
from arden.integrations.google_auth import storage as google_storage
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
    monkeypatch.setattr(google_credentials, "CREDENTIALS_PATH", tmp_path / "gmail_credentials.json")

    response = TestClient(app).post("/setup/google/preflight", json={"integration_id": "gmail"})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["credentials"]["exists"] is False
    assert "Google credentials not found at" in body["warnings"][0]
    assert body["scopes"] == google_auth.scopes_for_google_service("gmail")


def test_google_preflight_accepts_exact_drive_integration(monkeypatch, tmp_path):
    monkeypatch.setattr(google_credentials, "CREDENTIALS_PATH", tmp_path / "gmail_credentials.json")

    response = TestClient(app).post(
        "/setup/google/preflight",
        json={"integration_id": "google_drive"},
    )

    assert response.status_code == 200
    assert response.json()["scopes"] == google_auth.scopes_for_google_service("google_drive")


def test_google_credentials_rejects_web_client(monkeypatch, tmp_path):
    monkeypatch.setattr(google_credentials, "CREDENTIALS_PATH", tmp_path / "gmail_credentials.json")

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
    assert response.json() == {"detail": "Invalid Google Desktop OAuth credentials request."}
    assert "shh" not in response.text
    assert not (tmp_path / "gmail_credentials.json").exists()


def test_google_credentials_rejects_invalid_field_type(monkeypatch, tmp_path):
    target = tmp_path / "gmail_credentials.json"
    monkeypatch.setattr(google_credentials, "CREDENTIALS_PATH", target)
    payload = {"installed": {**INSTALLED_CLIENT["installed"], "client_id": 123}}

    response = TestClient(app).post("/setup/google/credentials", json={"json": payload})

    assert response.status_code == 400
    assert not target.exists()


def test_google_credentials_rejects_flat_client_document(monkeypatch, tmp_path):
    target = tmp_path / "gmail_credentials.json"
    monkeypatch.setattr(google_credentials, "CREDENTIALS_PATH", target)

    response = TestClient(app).post(
        "/setup/google/credentials",
        json={"json": INSTALLED_CLIENT["installed"]},
    )

    assert response.status_code == 400
    assert not target.exists()


def test_google_credentials_requires_redirect_uris(monkeypatch, tmp_path):
    target = tmp_path / "gmail_credentials.json"
    monkeypatch.setattr(google_credentials, "CREDENTIALS_PATH", target)
    installed = dict(INSTALLED_CLIENT["installed"])
    installed.pop("redirect_uris")

    response = TestClient(app).post(
        "/setup/google/credentials",
        json={"json": {"installed": installed}},
    )

    assert response.status_code == 400
    assert not target.exists()


def test_google_credentials_rejects_invalid_redirect_uri(monkeypatch, tmp_path):
    target = tmp_path / "gmail_credentials.json"
    monkeypatch.setattr(google_credentials, "CREDENTIALS_PATH", target)
    payload = {"installed": {**INSTALLED_CLIENT["installed"], "redirect_uris": ["not a URI"]}}

    response = TestClient(app).post("/setup/google/credentials", json={"json": payload})

    assert response.status_code == 400
    assert not target.exists()


def test_google_credentials_accepts_loopback_redirect_uri(monkeypatch, tmp_path):
    target = tmp_path / "gmail_credentials.json"
    monkeypatch.setattr(google_credentials, "CREDENTIALS_PATH", target)
    payload = {"installed": {**INSTALLED_CLIENT["installed"], "redirect_uris": ["http://localhost"]}}

    response = TestClient(app).post("/setup/google/credentials", json={"json": payload})

    assert response.status_code == 200
    assert json.loads(target.read_text())["installed"]["redirect_uris"] == ["http://localhost"]


@pytest.mark.parametrize("redirect_uri", ["urn:ietf:wg:oauth:2.0:oob", "https://example.test/callback"])
def test_google_credentials_rejects_non_loopback_redirect_uri(monkeypatch, tmp_path, redirect_uri):
    target = tmp_path / "gmail_credentials.json"
    monkeypatch.setattr(google_credentials, "CREDENTIALS_PATH", target)
    payload = {"installed": {**INSTALLED_CLIENT["installed"], "redirect_uris": [redirect_uri]}}

    response = TestClient(app).post("/setup/google/credentials", json={"json": payload})

    assert response.status_code == 400
    assert not target.exists()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("auth_uri", "https://example.test/oauth"),
        ("token_uri", "https://example.test/token"),
        ("auth_provider_x509_cert_url", "https://example.test/certs"),
    ],
)
def test_google_credentials_rejects_non_google_provider_endpoints(monkeypatch, tmp_path, field, value):
    target = tmp_path / "gmail_credentials.json"
    monkeypatch.setattr(google_credentials, "CREDENTIALS_PATH", target)
    payload = {"installed": {**INSTALLED_CLIENT["installed"], field: value}}

    response = TestClient(app).post("/setup/google/credentials", json={"json": payload})

    assert response.status_code == 400
    assert not target.exists()


def test_google_credentials_file_rejects_duplicate_json_fields(monkeypatch, tmp_path):
    target = tmp_path / "gmail_credentials.json"
    source = tmp_path / "download.json"
    monkeypatch.setattr(google_credentials, "CREDENTIALS_PATH", target)
    source.write_text(
        '{"installed":{"client_id":"first","client_id":"second","client_secret":"secret",'
        '"auth_uri":"https://accounts.google.com/o/oauth2/auth",'
        '"token_uri":"https://oauth2.googleapis.com/token","redirect_uris":["http://localhost"]}}'
    )

    with pytest.raises(google_credentials.GoogleCredentialsFileError, match="duplicate fields"):
        google_credentials.import_google_credentials_file(source)

    assert not target.exists()


def test_google_credentials_rejects_non_oauth_redirect_scheme(monkeypatch, tmp_path):
    target = tmp_path / "gmail_credentials.json"
    monkeypatch.setattr(google_credentials, "CREDENTIALS_PATH", target)
    payload = {"installed": {**INSTALLED_CLIENT["installed"], "redirect_uris": ["file:///tmp/callback"]}}

    response = TestClient(app).post("/setup/google/credentials", json={"json": payload})

    assert response.status_code == 400
    assert not target.exists()


def test_google_credentials_rejects_unknown_document_field(monkeypatch, tmp_path):
    target = tmp_path / "gmail_credentials.json"
    monkeypatch.setattr(google_credentials, "CREDENTIALS_PATH", target)
    payload = {"installed": {**INSTALLED_CLIENT["installed"], "unexpected": "value"}}

    response = TestClient(app).post("/setup/google/credentials", json={"json": payload})

    assert response.status_code == 400
    assert not target.exists()


def test_google_credentials_accepts_installed_client_json_and_masks_secret(monkeypatch, tmp_path):
    target = tmp_path / "gmail_credentials.json"
    monkeypatch.setattr(google_credentials, "CREDENTIALS_PATH", target)

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


def test_google_credentials_preserves_standard_installed_document_fields(monkeypatch, tmp_path):
    target = tmp_path / "gmail_credentials.json"
    monkeypatch.setattr(google_credentials, "CREDENTIALS_PATH", target)
    document = {
        "installed": {
            **INSTALLED_CLIENT["installed"],
            "project_id": "arden-project",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
        }
    }

    response = TestClient(app).post("/setup/google/credentials", json={"json": document})

    assert response.status_code == 200
    assert target.read_text()
    assert json.loads(target.read_text()) == document


def test_google_credentials_file_is_atomically_replaced_with_private_mode(monkeypatch, tmp_path):
    target = tmp_path / "gmail_credentials.json"
    target.write_text("old credentials")
    target.chmod(0o644)
    monkeypatch.setattr(google_credentials, "CREDENTIALS_PATH", target)

    response = TestClient(app).post("/setup/google/credentials", json={"json": INSTALLED_CLIENT})

    assert response.status_code == 200
    assert json.loads(target.read_text()) == INSTALLED_CLIENT
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert list(tmp_path.glob(".gmail_credentials.json.*.tmp")) == []


def test_google_credentials_atomic_write_failure_keeps_original_and_cleans_temp(monkeypatch, tmp_path):
    target = tmp_path / "gmail_credentials.json"
    target.write_text("original credentials")
    monkeypatch.setattr(google_credentials, "CREDENTIALS_PATH", target)
    monkeypatch.setattr(
        google_storage.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("replace failed")),
    )
    document = google_credentials.GoogleOAuthClientDocument.model_validate(INSTALLED_CLIENT, strict=True)

    with pytest.raises(OSError, match="replace failed"):
        google_credentials.save_google_credentials_json(document)

    assert target.read_text() == "original credentials"
    assert list(tmp_path.glob(".gmail_credentials.json.*.tmp")) == []


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("auth_uri", "ftp://accounts.example.com/oauth"),
        ("token_uri", "urn:example:token"),
        ("auth_provider_x509_cert_url", "not an endpoint"),
    ],
)
def test_google_credentials_rejects_non_http_provider_endpoints(monkeypatch, tmp_path, field, value):
    target = tmp_path / "gmail_credentials.json"
    monkeypatch.setattr(google_credentials, "CREDENTIALS_PATH", target)
    payload = {"installed": {**INSTALLED_CLIENT["installed"], field: value}}

    response = TestClient(app).post("/setup/google/credentials", json={"json": payload})

    assert response.status_code == 400
    assert not target.exists()


def test_google_credentials_status_rejects_malformed_document_without_leaking_secret(monkeypatch, tmp_path):
    target = tmp_path / "gmail_credentials.json"
    target.write_text('{"installed":{"client_secret":"status-secret"}}')
    monkeypatch.setattr(google_credentials, "CREDENTIALS_PATH", target)

    status = google_credentials.google_credentials_status()

    assert status.exists is True
    assert status.valid is False
    assert status.client_id is None
    assert status.error == "Google credentials must be an installed Desktop OAuth client document."
    assert "status-secret" not in status.model_dump_json()


def test_google_credentials_status_lets_code_errors_propagate(monkeypatch, tmp_path):
    target = tmp_path / "gmail_credentials.json"
    target.write_text(json.dumps(INSTALLED_CLIENT))
    monkeypatch.setattr(google_credentials, "CREDENTIALS_PATH", target)
    monkeypatch.setattr(
        google_credentials.GoogleOAuthClientDocument,
        "model_validate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("code bug")),
    )

    with pytest.raises(RuntimeError, match="code bug"):
        google_credentials.google_credentials_status()


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
    assert body["google"]["google_accounts"][0]["services"] == ["gmail", "google_drive"]
    assert set(body["google"]) == {"enabled", "credentials", "google_accounts", "provider_statuses"}
    assert [p["id"] for p in body["google"]["provider_statuses"]] == ["gmail", "calendar"]
    assert [s["id"] for s in body["slack"]["services"]] == ["slack_bot_token", "slack_user_token"]
    assert body["slack"]["provider_status"]["id"] == "slack"
    assert body["mcp"]["servers"][0]["name"] == "linear"
    assert body["mcp"]["provider_statuses"][0]["kind"] == "mcp"
