from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from resumeflow.app import create_app
from resumeflow.config import AppConfig
from resumeflow.devtools.sample_data import SAMPLE_CANDIDATES, resume_docx_bytes
from resumeflow.services.secrets import MemorySecretStore
from tests.fakes import FakeProvider

TEST_TOKEN = "test-session-token-0123456789abcdef"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.fixture
def config(tmp_path) -> AppConfig:
    # Background workers off: tests drive the queue explicitly with queue.drain().
    return AppConfig(data_dir=tmp_path / "data", session_token=TEST_TOKEN, workers_enabled=False, auto_backup=False)


@pytest.fixture
def secret_store() -> MemorySecretStore:
    return MemorySecretStore()


@pytest.fixture
def fake_ai() -> FakeProvider:
    return FakeProvider()


@pytest.fixture
def anon_client(config, secret_store, fake_ai) -> Iterator[TestClient]:
    app = create_app(config, secret_store=secret_store, ai_provider_factory=lambda key: fake_ai)
    with TestClient(app, base_url="http://127.0.0.1") as client:
        yield client


@pytest.fixture
def client(anon_client) -> TestClient:
    anon_client.headers["Authorization"] = f"Bearer {TEST_TOKEN}"
    # Most tests walk through review and verification explicitly and expect background processing to run straight
    # through; test_auto_export, test_trusted_profiles and test_confirmed_experience cover the defaults (export
    # automatically, profiles without verification, ask about missing job skills first).
    anon_client.patch(
        "/settings",
        json={"export_mode": "review", "require_verification": True, "gap_confirmation": "off", "hard_requirement_gates": True},
    )
    return anon_client


@pytest.fixture
def db(client):
    session = client.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def upload(client: TestClient, index: int = 0, *, path: str = "/candidates/import", data: bytes | None = None, name: str | None = None):
    sample = SAMPLE_CANDIDATES[index]
    payload = data if data is not None else resume_docx_bytes(sample)
    filename = name or f"{sample.name.replace(' ', '_')}_Resume.docx"
    return client.post(path, files={"file": (filename, payload, DOCX)})


def make_ready(client: TestClient, candidate_id: str) -> dict:
    """Verifies every extracted fact, confirms consent and completes review."""
    detail = client.get(f"/candidates/{candidate_id}").json()
    profile = detail["profile"]
    profile["summary_verified"] = True
    profile["work_authorization_verified"] = bool(profile.get("work_authorization"))
    for role in profile["employment"]:
        role["verified"] = True
        for bullet in role["bullets"]:
            bullet["verified"] = True
    for item in profile["education"] + profile["certifications"]:
        item["verified"] = True
    client.patch(f"/candidates/{candidate_id}", json={"profile": profile, "consent_confirmed": True})
    client.post(f"/candidates/{candidate_id}/skills/verify-with-evidence")
    response = client.post(f"/candidates/{candidate_id}/review-complete")
    assert response.status_code == 200, response.text
    return response.json()
