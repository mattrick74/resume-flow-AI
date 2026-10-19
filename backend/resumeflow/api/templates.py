from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Form, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from resumeflow.api.candidates import _read_upload
from resumeflow.api.deps import Config, DbSession
from resumeflow.domain.templates import TemplateStyle, is_template_key
from resumeflow.errors import AppError
from resumeflow.services import templates as service

router = APIRouter(prefix="/templates", tags=["templates"])


class FromResume(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1, max_length=36)
    version_id: str = Field(min_length=1, max_length=36)
    name: str | None = Field(default=None, max_length=90)


class TemplateUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=90)
    style: TemplateStyle | None = None


class SampleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=60)


@router.get("")
def list_templates(db: DbSession) -> dict[str, Any]:
    """Built-in templates first, then styles copied from resumes; ``selected`` is the one in use."""
    return service.list_templates(db)


@router.post("/import", status_code=201)
def import_template(db: DbSession, file: UploadFile = File(...), name: str | None = Form(default=None, max_length=90)) -> dict[str, Any]:
    """Copies the look of a DOCX or text-based PDF resume into a new template."""
    row = service.create_from_file(db, filename=file.filename or "resume", data=_read_upload(file), name=name)
    return service.template_dict(row)


@router.post("/from-resume", status_code=201)
def template_from_resume(body: FromResume, db: DbSession) -> dict[str, Any]:
    """Copies the look of a candidate's stored original resume."""
    row = service.create_from_resume_version(db, candidate_id=body.candidate_id, version_id=body.version_id, name=body.name)
    return service.template_dict(row)


@router.patch("/{template_id}")
def update_template(template_id: str, body: TemplateUpdate, db: DbSession) -> dict[str, Any]:
    row = service.get_template(db, template_id)
    style = body.style.model_dump() if body.style is not None else None
    return service.template_dict(service.update_template(db, row, name=body.name, style=style))


@router.delete("/{template_id}", status_code=204)
def delete_template(template_id: str, db: DbSession) -> Response:
    """If it was in use, documents go back to the Professional template."""
    service.delete_template(db, service.get_template(db, template_id))
    return Response(status_code=204)


@router.post("/sample")
def write_sample(body: SampleRequest, db: DbSession, config: Config) -> dict[str, Any]:
    """Writes a sample resume and cover letter in this template (DOCX, plus PDF when a converter is available)."""
    if not is_template_key(body.key):
        raise AppError(404, "template_not_found", "Template not found")
    return service.write_samples(db, config, body.key)
