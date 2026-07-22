from fastapi.testclient import TestClient

from arden.server.app import app
from arden.settings import hash_api_key


class _Config:
    api_key_hash = hash_api_key("test-key")


class _Runtime:
    config = _Config()


def _install_runtime():
    had_runtime = hasattr(app.state, "runtime")
    previous = getattr(app.state, "runtime", None)
    app.state.runtime = _Runtime()
    return had_runtime, previous


def _restore_runtime(had_runtime, previous):
    if had_runtime:
        app.state.runtime = previous
    else:
        delattr(app.state, "runtime")


def test_auth_middleware_allows_cors_preflight_for_protected_routes():
    had_runtime, previous = _install_runtime()
    try:
        response = TestClient(app).options(
            "/sessions",
            headers={
                "Origin": "http://127.0.0.1:5176",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization",
            },
        )
    finally:
        _restore_runtime(had_runtime, previous)

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5176"
    assert "authorization" in response.headers["access-control-allow-headers"].lower()


def test_auth_middleware_still_rejects_missing_key_for_protected_routes():
    had_runtime, previous = _install_runtime()
    try:
        response = TestClient(app).get("/sessions")
    finally:
        _restore_runtime(had_runtime, previous)

    assert response.status_code == 401
    assert response.json()["detail"] == "Missing API key. Include Authorization: Bearer <key> header."
