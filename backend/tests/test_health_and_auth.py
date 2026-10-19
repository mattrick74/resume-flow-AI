from __future__ import annotations

import pytest
from pydantic import ValidationError

from resumeflow.config import AppConfig
from resumeflow.db.migrate import head_revision
from tests.conftest import TEST_TOKEN


def test_health_is_public_and_reports_current_schema(anon_client):
    response = anon_client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["schema_revision"] == head_revision()


@pytest.mark.parametrize(
    "header",
    [None, "Bearer wrong-token-wrong-token-wrong-token", f"Basic {TEST_TOKEN}", TEST_TOKEN],
)
def test_protected_routes_reject_missing_or_bad_tokens(anon_client, header):
    headers = {"Authorization": header} if header else {}
    response = anon_client.get("/settings", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_valid_token_is_accepted(client):
    assert client.get("/settings").status_code == 200


def test_untrusted_host_header_is_rejected(client):
    response = client.get("/health", headers={"Host": "attacker.example"})
    assert response.status_code == 400


def test_cors_allows_only_the_desktop_origin(client):
    allowed = client.get("/health", headers={"Origin": "http://tauri.localhost"})
    assert allowed.headers.get("access-control-allow-origin") == "http://tauri.localhost"

    denied = client.get("/health", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in denied.headers


def test_api_schema_and_docs_are_not_exposed(client):
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert client.get(path).status_code == 404


def test_unknown_route_uses_structured_error(client):
    response = client.get("/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "http_404"


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.10", "example.com"])
def test_config_refuses_non_loopback_bind(tmp_path, host):
    with pytest.raises(ValidationError):
        AppConfig(data_dir=tmp_path, host=host, session_token=TEST_TOKEN)


def test_config_refuses_weak_session_token(tmp_path):
    with pytest.raises(ValidationError):
        AppConfig(data_dir=tmp_path, session_token="short")


def test_config_generates_strong_token_when_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("RESUMEFLOW_SESSION_TOKEN", raising=False)
    assert len(AppConfig(data_dir=tmp_path).session_token) >= 32
