from __future__ import annotations

from datetime import date

from sqlalchemy import func, select

from resumeflow.db.models import (
    Application,
    ApplicationAnswer,
    Candidate,
    CandidateMatch,
    CandidateRoleExperience,
    CandidateSkillEvidence,
    Document,
    GenerationRun,
    Job,
    ProcessingJob,
    ProcessingStageEvent,
    ResumeVersion,
)
from resumeflow.devtools.sample_data import SAMPLE_JOBS
from resumeflow.domain.enums import (
    AdditionStatus,
    DocumentFormat,
    DocumentStatus,
    DocumentType,
    EligibilityStatus,
    GenerationOperation,
    ProcessingJobType,
    ProcessingStatus,
    RunStatus,
    SkillDecision,
)


def _job(client, index: int = 0) -> dict:
    sample = SAMPLE_JOBS[index]
    response = client.post(
        "/jobs",
        json={
            "job_url": sample["url"],
            "raw_jd": sample["text"],
            "company": sample["company"],
            "title": sample["title"],
            "analyze": False,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _count(db, model) -> int:
    db.expire_all()
    return db.scalar(select(func.count()).select_from(model)) or 0


def test_delete_all_removes_job_history_and_generated_files_but_keeps_candidate(client, db, tmp_path):
    first = _job(client, 0)
    _job(client, 1)
    candidate = Candidate(full_name="Avery Quinlan")
    db.add(candidate)
    db.flush()

    originals = tmp_path / "originals" / candidate.id
    originals.mkdir(parents=True)
    original_file = originals / "Resume.docx"
    original_file.write_bytes(b"original resume")
    version = ResumeVersion(
        candidate_id=candidate.id,
        version=1,
        original_filename="Resume.docx",
        original_file_path=str(original_file),
        file_sha256="a" * 64,
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    db.add(version)

    # Simulate files exported under a previous output-root setting. The strict
    # date/sequence layout remains safe to clean from its recorded paths.
    exported_on = date(2026, 10, 30)
    old_root = tmp_path / "old-output"
    first_folder = old_root / exported_on.isoformat() / "001 Avery Quinlan"
    second_folder = old_root / exported_on.isoformat() / "002 Avery Quinlan"
    first_folder.mkdir(parents=True)
    second_folder.mkdir(parents=True)
    resume_file = first_folder / "Resume.pdf"
    cover_file = first_folder / "CoverLetter.pdf"
    other_resume = second_folder / "Resume.pdf"
    for path in (resume_file, cover_file, other_resume):
        path.write_bytes(b"generated")
    user_file = first_folder / "My notes.txt"
    user_file.write_text("keep me", encoding="utf-8")

    app1 = Application(
        job_id=first["id"],
        candidate_id=candidate.id,
        application_date=exported_on,
        sequence_number=1,
        output_directory=str(first_folder),
    )
    app2 = Application(
        job_id=first["id"],
        candidate_id=candidate.id,
        application_date=exported_on,
        sequence_number=2,
        output_directory=str(second_folder),
    )
    db.add_all([app1, app2])
    db.flush()
    db.add_all(
        [
            Document(
                application_id=app1.id,
                document_type=DocumentType.RESUME,
                format=DocumentFormat.PDF,
                version=1,
                status=DocumentStatus.APPROVED,
                file_path=str(resume_file),
            ),
            Document(
                application_id=app1.id,
                document_type=DocumentType.COVER_LETTER,
                format=DocumentFormat.PDF,
                version=1,
                status=DocumentStatus.APPROVED,
                file_path=str(cover_file),
            ),
            Document(
                application_id=app2.id,
                document_type=DocumentType.RESUME,
                format=DocumentFormat.PDF,
                version=1,
                status=DocumentStatus.APPROVED,
                file_path=str(other_resume),
            ),
            CandidateMatch(
                job_id=first["id"],
                candidate_id=candidate.id,
                score=90,
                rank=1,
                eligibility_status=EligibilityStatus.ELIGIBLE,
            ),
            ApplicationAnswer(
                application_id=app1.id,
                field_id="email",
                label="Email",
                field_type="text",
                answer="avery@example.com",
                source="profile",
            ),
        ]
    )
    processing = ProcessingJob(
        application_id=app1.id,
        job_id=first["id"],
        job_type=ProcessingJobType.EXPORT_APPLICATION,
        status=ProcessingStatus.SUCCEEDED,
        idempotency_key=f"export:{app1.id}",
    )
    db.add(processing)
    db.flush()
    db.add(
        ProcessingStageEvent(
            processing_job_id=processing.id,
            application_id=app1.id,
            job_id=first["id"],
            stage="pdf_conversion",
            status="completed",
            message="PDF conversion completed",
            duration_ms=10,
        )
    )
    related_run = GenerationRun(
        application_id=app1.id,
        job_id=first["id"],
        operation=GenerationOperation.APPLICATION_DOCUMENTS,
        provider="openai",
        model="test",
        status=RunStatus.SUCCEEDED,
    )
    independent_run = GenerationRun(
        operation=GenerationOperation.RESUME_PARSE,
        provider="offline",
        model="parser",
        status=RunStatus.SUCCEEDED,
    )
    skill_evidence = CandidateSkillEvidence(
        candidate_id=candidate.id,
        skill_name="Python",
        normalized_name="python",
        decision=SkillDecision.USED,
        source_job_ids_json=[first["id"]],
    )
    role_evidence = CandidateRoleExperience(
        candidate_id=candidate.id,
        role_id="role-1",
        role_label="Acme | Engineer | 2020",
        skills_json=["Python"],
        context="Built services",
        status=AdditionStatus.APPROVED,
        source_job_ids_json=[first["id"]],
    )
    db.add_all([related_run, independent_run, skill_evidence, role_evidence])
    db.commit()
    related_run_id = related_run.id
    independent_run_id = independent_run.id
    client.patch("/settings", json={"output_root": str(tmp_path / "new-output")})

    response = client.post("/jobs/delete-all", json={"confirmation": "DELETE ALL JOBS"})

    assert response.status_code == 200, response.text
    result = response.json()
    assert result["jobs"] == 2
    assert result["applications"] == 2
    assert result["documents"] == 3
    assert result["generation_runs"] == 1
    assert result["files_deleted"] == 3
    assert str(first_folder.resolve()) in result["folders_not_deleted"]
    assert not resume_file.exists() and not cover_file.exists() and not other_resume.exists()
    assert user_file.read_text(encoding="utf-8") == "keep me"
    assert not second_folder.exists()

    for model in (Job, Application, CandidateMatch, Document, ApplicationAnswer, ProcessingJob, ProcessingStageEvent):
        assert _count(db, model) == 0
    assert _count(db, Candidate) == 1
    assert _count(db, ResumeVersion) == 1
    assert original_file.read_bytes() == b"original resume"
    assert db.get(GenerationRun, related_run_id) is None
    assert db.get(GenerationRun, independent_run_id) is not None
    db.refresh(skill_evidence)
    db.refresh(role_evidence)
    assert skill_evidence.source_job_ids_json == []
    assert role_evidence.source_job_ids_json == []


def test_delete_all_requires_exact_confirmation_and_is_idempotent(client):
    assert client.post("/jobs/delete-all", json={"confirmation": "delete all jobs"}).status_code == 422
    response = client.post("/jobs/delete-all", json={"confirmation": "DELETE ALL JOBS"})
    assert response.status_code == 200
    assert response.json()["jobs"] == response.json()["applications"] == 0


def test_delete_all_is_blocked_atomically_while_work_is_queued(client, db):
    job = _job(client)
    db.add(
        ProcessingJob(
            job_id=job["id"],
            job_type=ProcessingJobType.PROCESS_JOB,
            status=ProcessingStatus.QUEUED,
            idempotency_key=f"process:{job['id']}",
        )
    )
    db.commit()

    response = client.post("/jobs/delete-all", json={"confirmation": "DELETE ALL JOBS"})

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "jobs_are_processing"
    assert client.get(f"/jobs/{job['id']}").status_code == 200
