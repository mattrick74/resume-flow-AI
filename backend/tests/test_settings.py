from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select

from resumeflow.app import create_app
from resumeflow.db.models import Setting

FAKE_KEY = "sk-test-0000000000000000000000"


def test_defaults_are_returned(client):
    body = client.get("/settings").json()
    assert body["settings"]["daily_goal"] == 100
    assert body["settings"]["worker_concurrency"] == 2
    assert body["settings"]["theme"] == "system"
    assert body["settings"]["auto_fetch_job_urls"] is False
    assert body["secrets_configured"] == {"openai_api_key": False}
    assert body["paths"]["database"].endswith("resumeflow.db")


def test_patch_persists_across_restart(client, config, secret_store):
    response = client.patch("/settings", json={"daily_goal": 120, "theme": "dark"})
    assert response.status_code == 200
    assert response.json()["settings"]["daily_goal"] == 120

    restarted = create_app(config, secret_store=secret_store)
    with TestClient(restarted, base_url="http://127.0.0.1") as second:
        second.headers["Authorization"] = client.headers["Authorization"]
        settings = second.get("/settings").json()["settings"]
    assert settings["daily_goal"] == 120
    assert settings["theme"] == "dark"


def test_patch_rejects_secret_fields(client, db):
    response = client.patch("/settings", json={"openai_api_key": FAKE_KEY})
    assert response.status_code == 422
    assert FAKE_KEY not in response.text
    assert sorted(row.key for row in db.scalars(select(Setting))) == ["export_mode", "gap_confirmation", "hard_requirement_gates", "local_analysis_only", "require_verification"]  # only what the test client set


def test_patch_rejects_invalid_values(client):
    for patch in (
        {"worker_concurrency": 50},
        {"daily_goal": None},
        {"output_root": "relative\\folder"},
        {"theme": "neon"},
    ):
        response = client.patch("/settings", json=patch)
        assert response.status_code == 422, patch
        assert response.json()["error"]["code"] in {"invalid_settings", "validation_error"}


def test_nullable_budget_can_be_cleared(client):
    client.patch("/settings", json={"daily_budget_usd": 25.0})
    response = client.patch("/settings", json={"daily_budget_usd": None})
    assert response.json()["settings"]["daily_budget_usd"] is None


def test_secret_lifecycle_never_exposes_value(client, secret_store, db):
    assert client.put("/settings/secrets/openai_api_key", json={"value": FAKE_KEY}).status_code == 204
    assert secret_store.get("openai_api_key") == FAKE_KEY

    response = client.get("/settings")
    assert response.json()["secrets_configured"]["openai_api_key"] is True
    assert FAKE_KEY not in response.text
    assert all(FAKE_KEY not in str(row.value_json) for row in db.scalars(select(Setting)))

    assert client.delete("/settings/secrets/openai_api_key").status_code == 204
    assert client.get("/settings").json()["secrets_configured"]["openai_api_key"] is False


def test_unknown_secret_names_are_rejected(client):
    assert client.put("/settings/secrets/aws_key", json={"value": FAKE_KEY}).status_code == 404


def test_invalid_secret_is_not_echoed(client):
    bad = "sk-has whitespace-1234567890"
    response = client.put("/settings/secrets/openai_api_key", json={"value": bad})
    assert response.status_code == 422
    assert bad not in response.text
