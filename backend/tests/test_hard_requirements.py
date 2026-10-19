"""Hard requirement checks are off by default: work authorization, clearance, licenses and location never stop
generation. Placeholder values such as "No" or "None" are never read as requirements."""

from __future__ import annotations

from resumeflow.devtools.sample_data import SAMPLE_JOBS
from tests.conftest import upload


def _strict_job(client) -> dict:
    """A job whose requirements would fail or leave unknown every gate."""
    job = client.post("/jobs", json={"job_url": SAMPLE_JOBS[0]["url"], "raw_jd": SAMPLE_JOBS[0]["text"]}).json()
    requirements = {
        **job["requirements"],
        "work_authorization": "Must be a U.S. citizen",
        "security_clearance": "Active Top Secret clearance",
        "required_credentials": ["PMP"],
        "work_arrangement": "onsite",
        "location": "Houston, Texas",
        "other_hard_requirements": ["Must travel 50%"],
    }
    return client.put(f"/jobs/{job['id']}/requirements", json=requirements).json()


def _ready(client) -> str:
    client.patch("/settings", json={"require_verification": False})
    candidate_id = upload(client, 0).json()["candidate"]["id"]
    client.patch(f"/candidates/{candidate_id}", json={"consent_confirmed": True})
    return candidate_id


def test_hard_requirements_never_stop_generation_by_default(client):
    client.patch("/settings", json={"hard_requirement_gates": False})
    candidate_id = _ready(client)
    job = _strict_job(client)
    assert job["status"] == "MATCHED"
    match = next(m for m in job["matches"] if m["candidate_id"] == candidate_id)
    assert match["rank"] == 1 and match["eligibility_status"] == "eligible"
    assert match["details"]["gates"] == [] and match["details"]["manual_checks"] == []
    assert client.get(f"/jobs/{job['id']}/skill-gaps").json()["hard_gates"] == []


def test_turning_the_checks_on_restores_the_gates(client):
    client.patch("/settings", json={"hard_requirement_gates": True})
    candidate_id = _ready(client)
    job = _strict_job(client)
    match = next(m for m in job["matches"] if m["candidate_id"] == candidate_id)
    assert match["rank"] is None and match["eligibility_status"] in {"ineligible", "needs_review"}
    assert {g["name"] for g in match["details"]["gates"]} >= {"Work authorization", "Security clearance", "Required credential"}


def test_placeholders_are_not_requirements(client):
    job = client.post("/jobs", json={"job_url": SAMPLE_JOBS[0]["url"], "raw_jd": SAMPLE_JOBS[0]["text"]}).json()
    saved = client.put(
        f"/jobs/{job['id']}/requirements",
        json={
            **job["requirements"],
            "work_authorization": "N/A",
            "security_clearance": "No",
            "required_credentials": ["None", "PMP"],
            "other_hard_requirements": ["Not specified", "No travel"],
        },
    ).json()["requirements"]
    assert saved["work_authorization"] is None and saved["security_clearance"] is None
    assert saved["required_credentials"] == ["PMP"] and saved["other_hard_requirements"] == ["No travel"]
