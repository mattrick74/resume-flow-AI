from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from resumeflow.api.deps import DbSession
from resumeflow.db.models import Application, Candidate, Job
from resumeflow.domain.enums import DocumentType, PipelineStatus
from resumeflow.services import applications as service
from resumeflow.services.queue import ProcessingQueue

router = APIRouter(prefix="/applications", tags=["applications"])


def _queue(request: Request) -> ProcessingQueue:
    return request.app.state.queue


class ApplicationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str
    candidate_id: str
    override_reason: str | None = Field(default=None, max_length=1000)
    generate: bool = True
    instructions: str | None = Field(default=None, max_length=2000, description="Notes for the AI writer for this application")


class GenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document: Literal["both", "resume", "cover_letter"] = "both"
    section: Literal["all", "summary", "skills", "experience"] = "all"
    # None keeps the application's instructions; "" clears them.
    instructions: str | None = Field(default=None, max_length=2000)


class DocumentEdit(BaseModel):
    content: dict[str, Any]


class ApproveRequest(BaseModel):
    acknowledge_warnings: bool = False


class OutcomeRequest(BaseModel):
    status: Literal["SUBMITTED", "INTERVIEW", "REJECTED", "OFFER"]


@router.get("")
def list_applications(
    db: DbSession,
    status: str | None = None,
    candidate_id: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    q: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    stmt = select(Application, Candidate.full_name, Job).join(Candidate, Candidate.id == Application.candidate_id).join(Job, Job.id == Application.job_id)
    if status:
        statuses = [PipelineStatus(s) for s in status.split(",") if s in PipelineStatus.__members__]
        if statuses:
            stmt = stmt.where(Application.status.in_(statuses))
    if candidate_id:
        stmt = stmt.where(Application.candidate_id == candidate_id)
    if date_from:
        stmt = stmt.where(Application.application_date >= date_from)
    if date_to:
        stmt = stmt.where(Application.application_date <= date_to)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(Job.company.ilike(like) | Job.title.ilike(like) | Candidate.full_name.ilike(like))
    rows = db.execute(stmt.order_by(Application.application_date.desc(), Application.sequence_number.desc()).limit(min(max(limit, 1), 1000)))
    return [service.application_summary(app, name, job) for app, name, job in rows]


@router.post("", status_code=201)
def create_application(body: ApplicationCreate, request: Request, db: DbSession) -> dict[str, Any]:
    app = service.create_application(
        db, _queue(request), job_id=body.job_id, candidate_id=body.candidate_id, override_reason=body.override_reason, generate=body.generate,
        instructions=body.instructions,
    )
    return service.application_detail(db, app)


@router.get("/{application_id}")
def get_application(application_id: str, db: DbSession) -> dict[str, Any]:
    return service.application_detail(db, service.get_application(db, application_id))


@router.post("/{application_id}/generate", status_code=202)
def generate(application_id: str, body: GenerateRequest, request: Request, db: DbSession) -> dict[str, Any]:
    app = service.get_application(db, application_id)
    service.refresh_snapshot(db, app)  # regenerate with everything verified so far
    service.set_instructions(app, body.instructions)
    service.enqueue_generation(db, _queue(request), app, document=body.document, section=body.section)
    return service.application_detail(db, app)


@router.put("/{application_id}/documents/{document_type}")
def edit_document(
    application_id: str,
    document_type: Literal["resume", "cover_letter"],
    body: DocumentEdit,
    request: Request,
    db: DbSession,
) -> dict[str, Any]:
    app = service.get_application(db, application_id)
    service.edit_document(db, app, DocumentType(document_type), body.content)
    service.auto_export(db, _queue(request), app)
    return service.application_detail(db, app)


@router.get("/{application_id}/documents/{document_type}/versions/{version}")
def get_document_version(application_id: str, document_type: Literal["resume", "cover_letter"], version: int, db: DbSession) -> dict[str, Any]:
    app = service.get_application(db, application_id)
    return service.document_version(db, app, DocumentType(document_type), version)


@router.post("/{application_id}/approve")
def approve(application_id: str, body: ApproveRequest, db: DbSession) -> dict[str, Any]:
    app = service.get_application(db, application_id)
    service.approve(db, app, body.acknowledge_warnings)
    return service.application_detail(db, app)


@router.post("/{application_id}/export", status_code=202)
def export(application_id: str, request: Request, db: DbSession) -> dict[str, Any]:
    app = service.get_application(db, application_id)
    service.request_export(db, _queue(request), app)
    return service.application_detail(db, app)


@router.post("/{application_id}/retry-pdf", status_code=202)
def retry_pdf(application_id: str, request: Request, db: DbSession) -> dict[str, Any]:
    app = service.get_application(db, application_id)
    service.request_pdf_retry(db, _queue(request), app)
    return service.application_detail(db, app)


@router.post("/{application_id}/suggest-additions")
def suggest_additions(application_id: str, db: DbSession) -> dict[str, Any]:
    """v1.8 GEN-11: missing job skills to answer and drafted bullets awaiting approval (never in exported content)."""
    return service.suggest_additions(db, service.get_application(db, application_id))


class ApproveAdditions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_ids: list[str] = Field(default_factory=list, max_length=200)
    edits: dict[str, str] = Field(default_factory=dict)
    acknowledge: bool = False
    regenerate: bool = True  # "Regenerate with approved additions"


@router.post("/{application_id}/approve-additions", status_code=202)
def approve_additions(application_id: str, body: ApproveAdditions, request: Request, db: DbSession) -> dict[str, Any]:
    app = service.get_application(db, application_id)
    result = service.approve_additions(
        db, request.app.state.queue, app, body.record_ids, acknowledge=body.acknowledge, edits=body.edits, regenerate=body.regenerate
    )
    return {**result, "application": service.application_detail(db, app)}


@router.get("/{application_id}/coverage")
def coverage(application_id: str, db: DbSession) -> dict[str, Any]:
    """v1.8 VAL-09/10: job-skill coverage before and after, changes against the imported resume and the prior version."""
    return service.coverage_view(db, service.get_application(db, application_id))


@router.post("/{application_id}/outcome")
def set_outcome(application_id: str, body: OutcomeRequest, db: DbSession) -> dict[str, Any]:
    app = service.get_application(db, application_id)
    service.set_outcome(db, app, PipelineStatus(body.status))
    return service.application_detail(db, app)
