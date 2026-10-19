"""Settings → "Require verification" off (the default): profiles are used as entered, without ticks or a review step."""

from __future__ import annotations

from resumeflow.devtools.sample_data import SAMPLE_JOBS
from tests.conftest import TEST_TOKEN, upload


def _imported(client) -> str:
    """Imported and authorized, but nothing ticked and no review completed."""
    candidate_id = upload(client, 0).json()["candidate"]["id"]
    client.patch(f"/candidates/{candidate_id}", json={"consent_confirmed": True})
    return candidate_id


def test_verification_is_off_by_default(anon_client):
    anon_client.headers["Authorization"] = f"Bearer {TEST_TOKEN}"  # without the test client's settings
    assert anon_client.get("/settings").json()["settings"]["require_verification"] is False


def test_profile_is_used_without_verification(client, tmp_path_factory):
    client.patch("/settings", json={"require_verification": False, "output_root": str(tmp_path_factory.mktemp("o")), "pdf_converter": "none"})
    candidate_id = _imported(client)
    detail = client.get(f"/candidates/{candidate_id}").json()
    ready = detail["readiness"]
    assert ready["ready_for_matching"] and ready["blockers"] == []
    assert ready["verified_roles"] == len(detail["profile"]["employment"]) and ready["verified_skills"] == len(detail["skills"])
    assert not any("verif" in w for w in ready["warnings"])
    assert detail["years_experience"] > 0

    job = client.post("/jobs", json={"job_url": SAMPLE_JOBS[0]["url"], "raw_jd": SAMPLE_JOBS[0]["text"]}).json()
    assert next(m for m in job["matches"] if m["candidate_id"] == candidate_id)["rank"] == 1
    app = client.post("/applications", json={"job_id": job["id"], "candidate_id": candidate_id}).json()
    client.app.state.queue.drain()
    resume = client.get(f"/applications/{app['id']}").json()["resume"]
    assert resume["issue_counts"]["error"] == 0, resume["issues"]
    content = resume["content"]
    assert len(content["experience"]) == len(detail["profile"]["employment"])
    assert all(role["bullets"] for role in content["experience"])
    assert content["education"]  # unticked education is used too

    # Turning verification back on leaves this candidate out until they are reviewed.
    client.patch("/settings", json={"require_verification": True})
    blocked = client.get(f"/candidates/{candidate_id}").json()["readiness"]
    assert not blocked["ready_for_matching"] and "Profile review not completed" in blocked["blockers"]


def test_paste_a_list_of_skills(client):
    client.patch("/settings", json={"require_verification": False})
    candidate_id = _imported(client)
    skills = client.get(f"/candidates/{candidate_id}").json()["skills"]
    rejected = next(s for s in skills if s["skill_name"] == "Docker")
    client.patch(f"/candidates/{candidate_id}/skills/{rejected['id']}", json={"verification_status": "rejected"})
    before = client.get(f"/candidates/{candidate_id}").json()["readiness"]["verified_skills"]

    response = client.post(f"/candidates/{candidate_id}/skills/bulk", json={"text": "Apache Flink, Pulumi\n• Datadog; pulumi\nPython | Docker"})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["added"] == ["Apache Flink", "Pulumi", "Datadog", "Docker"]  # Docker was rejected: restored
    assert body["already"] == ["Pulumi", "Python"]
    assert body["candidate"]["readiness"]["verified_skills"] == before + 4

    # Unverified skills only count once verification is required again.
    client.patch("/settings", json={"require_verification": True})
    assert client.get(f"/candidates/{candidate_id}").json()["readiness"]["verified_skills"] == 0
