from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from resumeflow.db.models import Application, CandidateSkill, GenerationRun, Job
from resumeflow.devtools.sample_data import SAMPLE_CANDIDATES, resume_docx_bytes, resume_pdf_bytes
from resumeflow.services.ai.provider import TransientAIError
from tests.conftest import make_ready, upload


def test_import_docx_creates_unverified_candidate(client, config):
    response = upload(client, 0)
    assert response.status_code == 201, response.text
    body = response.json()
    candidate = body["candidate"]
    assert body["created"] and body["version"] == 1 and body["extraction_source"] == "heuristic"
    assert candidate["full_name"] == "Avery Quinlan"
    assert candidate["email"] == "avery.quinlan@example.com"
    assert len(candidate["profile"]["employment"]) == 2
    # PRD §5: extracted claims never silently become facts.
    assert all(s["verification_status"] == "unverified" for s in candidate["skills"])
    assert not any(r["verified"] for r in candidate["profile"]["employment"])
    assert candidate["readiness"]["ready_for_matching"] is False

    stored = Path(candidate["resume_versions"][0]["original_file_path"])
    assert stored.is_relative_to(config.originals_dir / candidate["id"])
    assert stored.read_bytes() == resume_docx_bytes(SAMPLE_CANDIDATES[0])  # original kept unchanged


def test_import_pdf(client):
    response = upload(client, 3, data=resume_pdf_bytes(SAMPLE_CANDIDATES[3]), name="priya.pdf")
    assert response.status_code == 201, response.text
    assert response.json()["candidate"]["full_name"] == "Priya Raman"


def test_duplicate_file_is_rejected(client):
    first = upload(client, 0).json()["candidate"]
    response = upload(client, 0)
    assert response.status_code == 409
    assert response.json()["error"]["details"]["candidate_id"] == first["id"]


def test_invalid_files_are_rejected(client):
    cases = [
        ("old.doc", b"\xd0\xcf\x11\xe0legacy", "unsupported_file", 422),
        ("fake.pdf", b"not really a pdf", "unsupported_file", 422),
        ("notes.txt", b"hello", "unsupported_file", 422),
        ("empty.docx", b"", "empty_file", 422),
        ("huge.pdf", b"%PDF-" + b"0" * (10 * 1024 * 1024), "file_too_large", 413),
    ]
    for name, data, code, status in cases:
        response = client.post("/candidates/import", files={"file": (name, data, "application/octet-stream")})
        assert response.status_code == status, (name, response.text)
        assert response.json()["error"]["code"] == code


def test_scanned_pdf_without_text_is_rejected(client):
    import pymupdf

    document = pymupdf.open()
    document.new_page()  # a page with no text layer, like a scan
    response = client.post("/candidates/import", files={"file": ("scan.pdf", document.tobytes(), "application/pdf")})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "no_text"


def test_new_version_keeps_history_and_rejections(client, db):
    candidate = upload(client, 0).json()["candidate"]
    rejected = next(s for s in candidate["skills"] if s["skill_name"] == "Docker")
    client.patch(f"/candidates/{candidate['id']}/skills/{rejected['id']}", json={"verification_status": "rejected"})

    second = resume_pdf_bytes(SAMPLE_CANDIDATES[0])
    response = upload(client, 0, path=f"/candidates/{candidate['id']}/resumes", data=second, name="avery_2026.pdf")
    assert response.status_code == 201, response.text
    detail = response.json()["candidate"]
    assert [v["version"] for v in detail["resume_versions"]] == [2, 1]
    docker = next(s for s in detail["skills"] if s["skill_name"] == "Docker")
    assert docker["verification_status"] == "rejected"


def test_verification_requires_evidence(client):
    candidate = upload(client, 0).json()["candidate"]
    response = client.post(
        f"/candidates/{candidate['id']}/skills",
        json={"skill_name": "COBOL", "verification_status": "verified"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "evidence_required"
    ok = client.post(
        f"/candidates/{candidate['id']}/skills",
        json={"skill_name": "COBOL", "evidence": "Maintained COBOL batch jobs (operator confirmed)", "verification_status": "verified"},
    )
    assert ok.status_code == 201


def test_candidate_isolation(client):
    a = upload(client, 0).json()["candidate"]
    b = upload(client, 1).json()["candidate"]
    b_skill = b["skills"][0]["id"]
    b_version = b["resume_versions"][0]["id"]
    assert client.patch(f"/candidates/{a['id']}/skills/{b_skill}", json={"verification_status": "verified"}).status_code == 404
    assert client.delete(f"/candidates/{a['id']}/skills/{b_skill}").status_code == 404
    assert client.get(f"/candidates/{a['id']}/resumes/{b_version}").status_code == 404
    assert client.post(f"/candidates/{a['id']}/resumes/{b_version}/apply-profile").status_code == 404
    assert {s["id"] for s in client.get(f"/candidates/{a['id']}").json()["skills"]}.isdisjoint({s["id"] for s in b["skills"]})


def test_snapshot_contains_only_verified_facts(client, db):
    from resumeflow.db.models import Candidate
    from resumeflow.services.candidates import verified_snapshot

    candidate = upload(client, 0).json()["candidate"]
    profile = candidate["profile"]
    profile["employment"][0]["verified"] = True
    profile["employment"][0]["bullets"][0]["verified"] = True
    client.patch(f"/candidates/{candidate['id']}", json={"profile": profile})
    python = next(s for s in candidate["skills"] if s["skill_name"] == "Python")
    client.patch(f"/candidates/{candidate['id']}/skills/{python['id']}", json={"verification_status": "verified"})

    snapshot = verified_snapshot(db, db.get(Candidate, candidate["id"]))
    assert [r["employer"] for r in snapshot["employment"]] == ["Northwind Analytics Inc"]
    assert len(snapshot["employment"][0]["bullets"]) == 1
    assert [s["name"] for s in snapshot["skills"]] == ["Python"]
    assert snapshot["summary"] is None and snapshot["work_authorization"] is None


def test_review_and_readiness(client):
    candidate = upload(client, 0).json()["candidate"]
    assert "Candidate authorization not confirmed" in candidate["readiness"]["blockers"]
    detail = make_ready(client, candidate["id"])
    assert detail["readiness"]["ready_for_matching"] is True
    assert detail["resume_versions"][0]["verification_status"] == "verified"
    assert detail["years_experience"] > 9


def test_delete_candidate(client, config, db):
    candidate = upload(client, 0).json()["candidate"]
    folder = config.originals_dir / candidate["id"]
    assert folder.exists()
    assert client.delete(f"/candidates/{candidate['id']}").status_code == 204
    assert not folder.exists()
    assert db.scalars(select(CandidateSkill).where(CandidateSkill.candidate_id == candidate["id"])).all() == []


def test_delete_blocked_when_applications_exist(client, db):
    from datetime import date

    candidate = upload(client, 0).json()["candidate"]
    job = Job(job_url="https://x.example/1", normalized_url="x.example/1", raw_jd="jd", jd_hash="h")
    db.add(job)
    db.flush()
    db.add(Application(job_id=job.id, candidate_id=candidate["id"], application_date=date.today(), sequence_number=1))
    db.commit()
    response = client.delete(f"/candidates/{candidate['id']}")
    assert response.status_code == 409


def test_list_and_search(client):
    upload(client, 0)
    upload(client, 1)
    assert len(client.get("/candidates").json()) == 2
    found = client.get("/candidates", params={"q": "marisol"}).json()
    assert [c["full_name"] for c in found] == ["Marisol Venn"]


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
                "bullets": ["Built Spark and Airflow pipelines processing 4 TB of events per day on AWS"],
            }
        ],
        "education": [],
        "certifications": [],
        "skills": [
            {"name": "Apache Spark", "evidence": "Built Spark and Airflow pipelines"},
            {"name": "Haskell", "evidence": "Wrote Haskell compilers"},  # not in the resume
        ],
        "work_authorization": "U.S. Citizen",
    }


def test_ai_extraction_is_checked_against_source(client, secret_store, fake_ai, db):
    secret_store.set("openai_api_key", "sk-test-key-123456")
    fake_ai.on("AIResumeExtraction", _ai_extraction)
    body = upload(client, 0).json()
    assert body["extraction_source"] == "ai"
    skills = {s["skill_name"]: s for s in body["candidate"]["skills"]}
    assert skills["Haskell"]["evidence"] is None  # hallucinated evidence was cleared
    assert any("evidence" in w for w in body["warnings"])
    assert body["candidate"]["email"] == "avery.quinlan@example.com"  # backfilled from offline parse
    assert "<resume>" in fake_ai.calls[0]["input"]
    run = db.scalars(select(GenerationRun)).one()
    assert run.operation == "resume_parse" and run.estimated_cost_micros > 0


def test_ai_failure_falls_back_to_offline_parsing(client, secret_store, fake_ai):
    secret_store.set("openai_api_key", "sk-test-key-123456")
    fake_ai.fail_with = TransientAIError("rate limited")
    body = upload(client, 0).json()
    assert body["extraction_source"] == "heuristic"
    assert any("AI extraction failed" in w for w in body["warnings"])
