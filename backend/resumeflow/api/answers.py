"""v2.0 Chrome extension routes: the answer bank, answers to application forms, captured confirmation screens and the
generated files (for attaching to forms)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, File, Form, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from resumeflow.api.deps import AI, DbSession
from resumeflow.db.models import AnswerBankEntry, Document
from resumeflow.domain.enums import DocumentFormat, DocumentType
from resumeflow.errors import AppError
from resumeflow.services import answers as service
from resumeflow.services import submission
from resumeflow.services.applications import application_detail, get_application
from resumeflow.services.candidates import get_candidate

router = APIRouter(tags=["extension"])


class AnswerBankSave(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str | None = None  # None: for every candidate
    key: str | None = Field(default=None, max_length=80)
    question: str | None = Field(default=None, max_length=2000)
    answer: str = Field(min_length=1, max_length=5000)


class AnswersRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    page_url: str | None = Field(default=None, max_length=2000)
    fields: list[service.FormField] = Field(min_length=1, max_length=200)


@router.get("/answer-bank")
def get_answer_bank(db: DbSession, candidate_id: str | None = None) -> dict[str, Any]:
    if candidate_id:
        get_candidate(db, candidate_id)
    return service.bank_view(db, candidate_id)


@router.put("/answer-bank")
def save_answer(body: AnswerBankSave, db: DbSession) -> dict[str, Any]:
    if body.candidate_id:
        get_candidate(db, body.candidate_id)
    return service.entry_view(service.save_entry(db, candidate_id=body.candidate_id, key=body.key, question=body.question, answer=body.answer))


@router.delete("/answer-bank/{entry_id}", status_code=204)
def delete_answer(entry_id: str, db: DbSession) -> Response:
    row = db.get(AnswerBankEntry, entry_id)
    if row is None:
        raise AppError(404, "not_found", "That saved answer doesn't exist.")
    db.delete(row)
    db.commit()
    return Response(status_code=204)


@router.post("/applications/{application_id}/answers")
def answer_form(application_id: str, body: AnswersRequest, db: DbSession, ai: AI) -> dict[str, Any]:
    """Answers for the fields on one application-form page (requirements section 6)."""
    app = get_application(db, application_id)
    return service.answer_fields(db, ai, app, body.fields, body.page_url)


@router.get("/applications/{application_id}/answers")
def list_answers(application_id: str, db: DbSession) -> list[dict[str, Any]]:
    return service.answers_view(db, get_application(db, application_id))


@router.post("/applications/{application_id}/submission", status_code=201)
def capture_submission(
    application_id: str,
    db: DbSession,
    image: UploadFile = File(...),
    page_url: str | None = Form(default=None),
    page_title: str | None = Form(default=None),
    detected_by: Literal["auto", "manual"] = Form(default="manual"),
    captured_at: datetime | None = Form(default=None),
) -> dict[str, Any]:
    """Saves the confirmation screen after the user submitted, and records the application as submitted."""
    app = get_application(db, application_id)
    data = image.file.read(submission.MAX_SCREENSHOT_BYTES + 1)
    submission.save_submission(db, app, data, page_url=page_url, page_title=page_title, detected_by=detected_by, captured_at=captured_at)
    return {**application_detail(db, app), "submission": submission.submission_view(db, app)}


@router.delete("/applications/{application_id}/submission/{document_id}")
def delete_submission(application_id: str, document_id: str, db: DbSession) -> dict[str, Any]:
    app = get_application(db, application_id)
    submission.delete_submission(db, app, document_id)
    return {**application_detail(db, app), "submission": submission.submission_view(db, app)}


@router.get("/applications/{application_id}/submission")
def get_submission(application_id: str, db: DbSession) -> dict[str, Any]:
    return submission.submission_view(db, get_application(db, application_id))


_MEDIA = {
    DocumentFormat.PDF: "application/pdf",
    DocumentFormat.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    DocumentFormat.PNG: "image/png",
}


@router.get("/applications/{application_id}/files/{document_type}")
def download_file(
    application_id: str,
    document_type: Literal["resume", "cover_letter", "submission_screenshot"],
    db: DbSession,
    format: Literal["pdf", "docx", "png"] = "pdf",
) -> FileResponse:
    """The latest exported Resume or CoverLetter (or captured screen), e.g. to attach it to an application form."""
    app = get_application(db, application_id)
    rows = db.scalars(
        select(Document)
        .where(Document.application_id == app.id, Document.document_type == DocumentType(document_type), Document.format == DocumentFormat(format))
        .order_by(Document.version.desc())
    )
    row = next((r for r in rows if r.file_path and Path(r.file_path).exists()), None)
    if row is None:
        raise AppError(404, "file_not_found", f"No exported {document_type.replace('_', ' ')} {format.upper()} for this application yet.")
    return FileResponse(row.file_path, media_type=_MEDIA[DocumentFormat(format)], filename=Path(row.file_path).name)
