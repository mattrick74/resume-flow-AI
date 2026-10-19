from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, File, Request, Response, UploadFile
from pydantic import AfterValidator, BaseModel, ConfigDict, Field
from sqlalchemy import select

from resumeflow.api.deps import AI, Config, DbSession
from resumeflow.db.models import Candidate, ResumeVersion
from resumeflow.domain.enums import CandidateStatus, VerificationStatus
from resumeflow.domain.profile import CandidatePreferences, CandidateProfile
from resumeflow.errors import AppError
from resumeflow.services import candidates as service
from resumeflow.services import experience
from resumeflow.services.resume_parser import MAX_RESUME_BYTES

router = APIRouter(prefix="/candidates", tags=["candidates"])


def _email(value: str | None) -> str | None:
    value = (value or "").strip()
    if not value:
        return None
    if "@" not in value or " " in value or "." not in value.split("@")[-1]:
        raise ValueError("Enter a valid email address")
    return value


Email = Annotated[str | None, Field(max_length=320), AfterValidator(_email)]


def _read_upload(file: UploadFile) -> bytes:
    data = file.file.read(MAX_RESUME_BYTES + 1)
    if len(data) > MAX_RESUME_BYTES:
        raise AppError(413, "file_too_large", "Resumes must be 10 MB or smaller.")
    return data


class CandidateCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(min_length=1, max_length=200)
    email: Email = None
    phone: str | None = Field(default=None, max_length=50)
    location: str | None = Field(default=None, max_length=200)


class CandidateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, max_length=200)
    email: Email = None
    phone: str | None = Field(default=None, max_length=50)
    location: str | None = Field(default=None, max_length=200)
    status: CandidateStatus | None = None
    profile: CandidateProfile | None = None
    preferences: CandidatePreferences | None = None
    consent_confirmed: bool | None = None


class SkillCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_name: str = Field(min_length=1, max_length=200)
    evidence: str | None = Field(default=None, max_length=2000)
    verification_status: VerificationStatus = VerificationStatus.UNVERIFIED


class SkillUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_name: str | None = Field(default=None, max_length=200)
    evidence: str | None = Field(default=None, max_length=2000)
    verification_status: VerificationStatus | None = None


@router.get("")
def list_candidates(db: DbSession, status: str | None = None, q: str | None = None) -> list[dict[str, Any]]:
    return service.list_candidates(db, status, q)


@router.post("", status_code=201)
def create_candidate(body: CandidateCreate, db: DbSession) -> dict[str, Any]:
    candidate = Candidate(**body.model_dump())
    db.add(candidate)
    db.commit()
    return service.candidate_detail(db, candidate)


@router.post("/import", status_code=201)
def import_resume(db: DbSession, config: Config, ai: AI, file: UploadFile = File(...)) -> dict[str, Any]:
    """Creates a new candidate from a resume file (DOCX or text-based PDF)."""
    return service.import_resume(db, config, ai, filename=file.filename or "resume", data=_read_upload(file))


@router.get("/{candidate_id}")
def get_candidate(candidate_id: str, db: DbSession) -> dict[str, Any]:
    return service.candidate_detail(db, service.get_candidate(db, candidate_id))


@router.patch("/{candidate_id}")
def update_candidate(candidate_id: str, body: CandidateUpdate, db: DbSession) -> dict[str, Any]:
    candidate = service.get_candidate(db, candidate_id)
    service.update_candidate(db, candidate, body.model_dump(exclude_unset=True, mode="json"))
    return service.candidate_detail(db, candidate)


@router.delete("/{candidate_id}", status_code=204)
def delete_candidate(candidate_id: str, db: DbSession, config: Config) -> Response:
    service.delete_candidate(db, config, service.get_candidate(db, candidate_id))
    return Response(status_code=204)


@router.post("/{candidate_id}/resumes", status_code=201)
def add_resume_version(candidate_id: str, db: DbSession, config: Config, ai: AI, file: UploadFile = File(...)) -> dict[str, Any]:
    service.get_candidate(db, candidate_id)
    return service.import_resume(
        db, config, ai, filename=file.filename or "resume", data=_read_upload(file), candidate_id=candidate_id
    )


@router.get("/{candidate_id}/resumes/{version_id}")
def get_resume_version(candidate_id: str, version_id: str, db: DbSession) -> dict[str, Any]:
    version = db.scalar(
        select(ResumeVersion).where(ResumeVersion.id == version_id, ResumeVersion.candidate_id == candidate_id)
    )
    if version is None:
        raise AppError(404, "resume_version_not_found", "Resume version not found")
    return service._version_dict(version, include_text=True)


@router.post("/{candidate_id}/resumes/{version_id}/apply-profile")
def apply_version_profile(candidate_id: str, version_id: str, db: DbSession) -> dict[str, Any]:
    candidate = service.get_candidate(db, candidate_id)
    service.apply_version_profile(db, candidate, version_id)
    return service.candidate_detail(db, candidate)


@router.post("/{candidate_id}/resumes/{version_id}/reextract")
def reextract_version(candidate_id: str, version_id: str, db: DbSession, ai: AI) -> dict[str, Any]:
    """Reads the stored resume again (AI when a key is set) and replaces the profile; unchanged verified facts stay verified."""
    candidate = service.get_candidate(db, candidate_id)
    return service.reextract_version(db, ai, candidate, version_id)


@router.post("/{candidate_id}/review-complete")
def complete_review(candidate_id: str, db: DbSession) -> dict[str, Any]:
    candidate = service.get_candidate(db, candidate_id)
    service.complete_review(db, candidate)
    return service.candidate_detail(db, candidate)


@router.post("/{candidate_id}/skills", status_code=201)
def add_skill(candidate_id: str, body: SkillCreate, db: DbSession) -> dict[str, Any]:
    candidate = service.get_candidate(db, candidate_id)
    service.add_skill(db, candidate, body.skill_name, body.evidence, body.verification_status)
    return service.candidate_detail(db, candidate)


class SkillList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=20000, description="Skills separated by commas, semicolons or new lines")


@router.post("/{candidate_id}/skills/bulk", status_code=201)
def add_skills(candidate_id: str, body: SkillList, db: DbSession) -> dict[str, Any]:
    candidate = service.get_candidate(db, candidate_id)
    result = service.add_skills(db, candidate, body.text)
    return {**result, "candidate": service.candidate_detail(db, candidate)}


class ConfirmBulk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answers: list[experience.SkillAnswer] = Field(min_length=1, max_length=200)
    source_job_ids: list[str] = Field(default_factory=list, max_length=500)


@router.post("/{candidate_id}/skills/confirm-bulk")
def confirm_skills(candidate_id: str, body: ConfirmBulk, request: Request, db: DbSession, ai: AI) -> dict[str, Any]:
    """v1.8 CAN-14/17: Have used / Have not used / Unsure for job skills, with roles and a description of the work.
    Descriptions become drafted bullets that wait for approval; waiting jobs continue once nothing is left to answer."""
    candidate = service.get_candidate(db, candidate_id)
    result = experience.confirm_skills(db, ai, request.app.state.queue, candidate, body.answers, body.source_job_ids)
    return {**result, "candidate": service.candidate_detail(db, candidate)}


@router.get("/{candidate_id}/experience-evidence")
def list_experience(candidate_id: str, db: DbSession) -> dict[str, Any]:
    """v1.8 UX-02: suggested bullets, approved additions and the confirmation history."""
    return experience.evidence_overview(db, service.get_candidate(db, candidate_id))


@router.post("/{candidate_id}/experience-evidence", status_code=201)
def add_experience(candidate_id: str, body: experience.RecordCreate, request: Request, db: DbSession, ai: AI) -> dict[str, Any]:
    """v1.8 CAN-15: one piece of work at one role, in the operator's words; a bullet is drafted from it (and added at once
    when Settings → auto_approve_bullets is on)."""
    candidate = service.get_candidate(db, candidate_id)
    experience.create_record(db, ai, candidate, body, request.app.state.queue)
    return experience.evidence_overview(db, candidate)


class ApproveExperience(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ids: list[str] = Field(min_length=1, max_length=200)
    acknowledge: bool = False
    edits: dict[str, str] = Field(default_factory=dict)


@router.post("/{candidate_id}/experience-evidence/approve")
def approve_experience(candidate_id: str, body: ApproveExperience, request: Request, db: DbSession) -> dict[str, Any]:
    candidate = service.get_candidate(db, candidate_id)
    result = experience.approve_records(db, request.app.state.queue, candidate, body.ids, acknowledge=body.acknowledge, edits=body.edits)
    return {**result, "overview": experience.evidence_overview(db, candidate)}


@router.patch("/{candidate_id}/experience-evidence/{record_id}")
def update_experience(candidate_id: str, record_id: str, body: experience.RecordUpdate, request: Request, db: DbSession, ai: AI) -> dict[str, Any]:
    candidate = service.get_candidate(db, candidate_id)
    record = experience.get_record(db, candidate_id, record_id)
    result = experience.update_record(db, ai, request.app.state.queue, candidate, record, body)
    return {**result, "overview": experience.evidence_overview(db, candidate)}


@router.delete("/{candidate_id}/experience-evidence/{record_id}")
def delete_experience(candidate_id: str, record_id: str, request: Request, db: DbSession) -> dict[str, Any]:
    candidate = service.get_candidate(db, candidate_id)
    record = experience.get_record(db, candidate_id, record_id)
    result = experience.delete_record(db, request.app.state.queue, candidate, record)
    return {**result, "overview": experience.evidence_overview(db, candidate)}


@router.post("/{candidate_id}/skills/verify-with-evidence")
def verify_skills_with_evidence(candidate_id: str, db: DbSession) -> dict[str, Any]:
    candidate = service.get_candidate(db, candidate_id)
    count = service.verify_skills_with_evidence(db, candidate)
    return {"verified": count, "candidate": service.candidate_detail(db, candidate)}


@router.patch("/{candidate_id}/skills/{skill_id}")
def update_skill(candidate_id: str, skill_id: str, body: SkillUpdate, db: DbSession) -> dict[str, Any]:
    candidate = service.get_candidate(db, candidate_id)
    service.update_skill(db, service.get_skill(db, candidate_id, skill_id), body.model_dump(exclude_unset=True))
    return service.candidate_detail(db, candidate)


@router.delete("/{candidate_id}/skills/{skill_id}")
def delete_skill(candidate_id: str, skill_id: str, db: DbSession) -> dict[str, Any]:
    candidate = service.get_candidate(db, candidate_id)
    db.delete(service.get_skill(db, candidate_id, skill_id))
    db.commit()
    return service.candidate_detail(db, candidate)
