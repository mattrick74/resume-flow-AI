"""Re-reading stored resumes, regenerating with newly verified facts, and resume-quality guards."""

from __future__ import annotations

from resumeflow.devtools.sample_data import SAMPLE_CANDIDATES, SAMPLE_JOBS
from resumeflow.services.evidence import evidence_listing, years_phrase
from tests.conftest import make_ready, upload
from tests.test_workflow import drain


def _ai_extraction(text: str) -> dict:
    return {
        "full_name": "Avery Quinlan",
        "email": None,
        "phone": None,
        "location": "Austin, TX",
        "headline": "Senior Data Engineer",
        "summary": "Data engineer with 9 years building batch and streaming pipelines on AWS.",
        "links": [],
        "employment": [
            {
                "employer": "Northwind Analytics Inc",
                "title": "Senior Data Engineer",
                "location": "Austin, TX",
                "start": "2020-03",
                "end": "present",
                "bullets": [SAMPLE_CANDIDATES[0].roles[0].bullets[0], "Mentored three junior engineers on data modeling"],
            },
            {
                "employer": "Bluebonnet Logistics LLC",
                "title": "Data Engineer",
                "location": "Dallas, TX",
                "start": "2016-06",
                "end": "2020-02",
                "bullets": [SAMPLE_CANDIDATES[0].roles[1].bullets[0]],
            },
        ],
        "education": [],
        "certifications": [],
        "skills": [{"name": "Apache Spark", "evidence": "Spark"}],
        "work_authorization": None,
    }


def test_reextract_with_ai_keeps_ticks_on_unchanged_facts(client, secret_store, fake_ai):
    candidate = upload(client, 0).json()["candidate"]  # offline parse (no key yet)
    make_ready(client, candidate["id"])
    before = client.get(f"/candidates/{candidate['id']}").json()
    assert before["readiness"]["ready_for_matching"]

    client.post(f"/candidates/{candidate['id']}/skills", json={"skill_name": "Looker", "evidence": "Told us in a call", "verification_status": "verified"})
    secret_store.set("openai_api_key", "sk-test-key-123456")
    fake_ai.on("AIResumeExtraction", _ai_extraction)
    version_id = before["resume_versions"][0]["id"]
    response = client.post(f"/candidates/{candidate['id']}/resumes/{version_id}/reextract")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["extraction_source"] == "ai"
    assert (body["roles"], body["bullets"]) == (2, 3)

    profile = body["candidate"]["profile"]
    northwind = profile["employment"][0]
    assert northwind["verified"]  # same employer, title and start as the verified role
    ticks = {b["text"]: b["verified"] for b in northwind["bullets"]}
    assert ticks[SAMPLE_CANDIDATES[0].roles[0].bullets[0]] is True  # unchanged text keeps its tick
    assert ticks["Mentored three junior engineers on data modeling"] is False  # new fact needs review
    assert body["kept_verified"] >= 3
    # Anything new means the profile must be reviewed again before it is used.
    assert body["candidate"]["profile_reviewed_at"] is None
    assert not body["candidate"]["readiness"]["ready_for_matching"]
    assert fake_ai.calls[-1]["schema"] == "AIResumeExtraction"
    skills = {s["skill_name"]: s["verification_status"] for s in body["candidate"]["skills"]}
    assert skills["Looker"] == "verified"  # added by hand: kept
    assert skills["Apache Spark"] == "verified"  # read again: keeps its tick
    assert body["skills_removed"] > 0 and "Terraform" not in skills  # only the earlier reading had it


def test_reextract_without_a_key_uses_the_improved_offline_parser(client):
    candidate = upload(client, 1).json()["candidate"]
    version_id = candidate["resume_versions"][0]["id"]
    body = client.post(f"/candidates/{candidate['id']}/resumes/{version_id}/reextract").json()
    assert body["extraction_source"] == "heuristic" and body["roles"] >= 1
    assert client.post(f"/candidates/{candidate['id']}/resumes/nope/reextract").status_code == 404


def test_candidate_without_verified_roles_is_not_ready_and_gaps_are_listed(client):
    candidate = upload(client, 0).json()["candidate"]
    make_ready(client, candidate["id"])
    detail = client.get(f"/candidates/{candidate['id']}").json()
    profile = detail["profile"]
    for role in profile["employment"]:
        role["verified"] = False
    for item in profile["education"]:
        item["verified"] = False
    client.patch(f"/candidates/{candidate['id']}", json={"profile": profile})
    readiness = client.get(f"/candidates/{candidate['id']}").json()["readiness"]
    assert not readiness["ready_for_matching"]
    assert any("No verified work experience" in b for b in readiness["blockers"])
    assert any("role(s) aren't verified" in w for w in readiness["warnings"])
    assert any("Education isn't verified" in w for w in readiness["warnings"])


def test_regenerate_uses_facts_verified_after_the_application(client, tmp_path):
    for index in range(3):
        make_ready(client, upload(client, index).json()["candidate"]["id"])
    client.patch("/settings", json={"output_root": str(tmp_path / "out"), "pdf_converter": "none"})
    job = client.post("/jobs", json={"job_url": SAMPLE_JOBS[0]["url"], "raw_jd": SAMPLE_JOBS[0]["text"]}).json()
    avery = next(c for c in client.get("/candidates").json() if c["full_name"] == "Avery Quinlan")
    app = client.post("/applications", json={"job_id": job["id"], "candidate_id": avery["id"]}).json()
    drain(client)
    first = client.get(f"/applications/{app['id']}").json()["resume"]["content"]["skills"]

    added = client.post(f"/candidates/{avery['id']}/skills", json={"skill_name": "Looker", "evidence": "Built Looker dashboards", "verification_status": "verified"})
    assert added.status_code in (200, 201), added.text
    assert "Looker" not in first
    client.post(f"/applications/{app['id']}/generate", json={"document": "resume", "section": "all"})
    drain(client)
    again = client.get(f"/applications/{app['id']}").json()["resume"]["content"]["skills"]
    assert "Looker" in again


def test_evidence_states_years_like_a_resume():
    assert years_phrase(4.4) == "4+" and years_phrase(4.0) == "4" and years_phrase(0.5) == "less than 1"
    listing = evidence_listing({"employment": [], "skills": [], "total_years_experience": 4.4})
    assert "4+" in listing and "verified" not in listing.lower()


def test_ai_fact_check_never_sees_the_candidate_name(client, secret_store, fake_ai, tmp_path):
    candidate = upload(client, 0).json()["candidate"]
    make_ready(client, candidate["id"])
    client.patch("/settings", json={"output_root": str(tmp_path / "out"), "pdf_converter": "none"})
    job = client.post("/jobs", json={"job_url": SAMPLE_JOBS[0]["url"], "raw_jd": SAMPLE_JOBS[0]["text"]}).json()  # analysed offline
    secret_store.set("openai_api_key", "sk-test-key-123456")

    def tailoring(text: str) -> dict:
        role_ids = [line.split("]")[0].lstrip("[") for line in text.splitlines() if line.startswith("[R-")]
        bullet_ids = [line.split("]")[0].lstrip("[") for line in text.splitlines() if line.startswith("[B-")]
        return {
            "headline": "Senior Data Engineer",
            "summary": "Senior Data Engineer with 9 years of total verified experience building pipelines.",
            "skill_order": [],
            "roles": [{"role_id": role_ids[0], "bullets": [{"text": "Built pipelines", "source_bullet_ids": [bullet_ids[0]]}]}],
        }

    fake_ai.on("AIResumeTailoring", tailoring)
    fake_ai.on("AICoverLetter", lambda text: {"greeting": "Dear team,", "paragraphs": [{"text": "I am applying to Acme Data Co.", "evidence_ids": []}], "closing": "Sincerely,"})
    fake_ai.on("AIClaimReview", lambda text: {"issues": [{"quote": "Avery Quinlan", "reason": "no name in evidence", "severity": "error"}]})
    app = client.post("/applications", json={"job_id": job["id"], "candidate_id": candidate["id"]}).json()
    drain(client)
    resume = client.get(f"/applications/{app['id']}").json()["resume"]

    review_inputs = [c["input"] for c in fake_ai.calls if c["schema"] == "AIClaimReview"]
    generated_doc = review_inputs[0].split("<generated_document>")[1]
    assert "Avery Quinlan" not in generated_doc
    codes = {i["code"] for i in resume["issues"]}
    assert "ai_unsupported_claim" not in codes  # the name can't be quoted, so the false finding is dropped
    assert "internal_wording" in codes  # "total verified experience" is flagged
    assert resume["content"]["skills"]  # an empty AI skill list falls back to the verified skills


def test_skills_keep_the_resume_order_after_job_matches():
    from resumeflow.services.jobs import empty_requirements
    from resumeflow.services.generation import order_skills

    from resumeflow.domain.skills import normalize_skill

    snap = {"skills": [{"name": n, "normalized": normalize_skill(n)} for n in ("Rust", "Kafka", "Python", "Agile", "Machine Learning")]}
    req = empty_requirements().model_copy(update={"title": "Machine Learning Engineer", "mandatory_skills": ["Python"], "preferred_skills": ["Kafka"]})
    assert order_skills(snap, req) == ["Python", "Kafka", "Machine Learning", "Rust", "Agile"]