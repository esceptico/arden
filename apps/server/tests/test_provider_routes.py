from fastapi.testclient import TestClient

from arden.server.app import app


def test_provider_routes_are_registered_once():
    paths = TestClient(app).get("/openapi.json").json()["paths"]

    assert "/providers" in paths
    assert "/providers/{provider_id}/connect" in paths
    assert "/services" in paths
    assert "/services/{service_id}/connect" in paths
    assert "/tool-providers" in paths
    assert "/setup/status" in paths
    assert "/setup/google/credentials" in paths
    assert "/setup/google/preflight" in paths
    assert "/setup/slack/verify" in paths
    assert "/gmail/add" in paths
