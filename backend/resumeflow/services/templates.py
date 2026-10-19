"""Document templates: the built-in styles plus styles copied from existing resumes."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from resumeflow.config import AppConfig
from resumeflow.db.models import ResumeTemplate, ResumeVersion
from resumeflow.domain.content import Contact, CoverLetterContent, CoverParagraph, ResumeBullet, ResumeContent, ResumeRole
from resumeflow.domain.templates import BUILTIN_TEMPLATES, DEFAULT_TEMPLATE, TemplateStyle, custom_key
from resumeflow.errors import AppError
from resumeflow.services.files import replace_file, sanitize_component
from resumeflow.services.pdf import PdfConversionError, convert_to_pdf_bytes, resolve_converter
from resumeflow.services.render import render_cover_letter, render_resume
from resumeflow.services.resume_parser import detect_format
from resumeflow.services.settings import AppSettingsUpdate, load_settings, update_settings
from resumeflow.services.template_extract import extract_style

MAX_TEMPLATES = 50


def _builtin_dict(key: str) -> dict[str, Any]:
    name, style = BUILTIN_TEMPLATES[key]
    return {"key": key, "id": None, "name": name, "builtin": True, "style": style.model_dump(), "source_filename": None, "notes": [], "created_at": None}


def template_dict(row: ResumeTemplate) -> dict[str, Any]:
    return {
        "key": custom_key(row.id),
        "id": row.id,
        "name": row.name,
        "builtin": False,
        "style": TemplateStyle.model_validate(row.style_json).model_dump(),
        "source_filename": row.source_filename,
        "notes": row.notes_json or [],
        "created_at": row.created_at,
    }


def list_templates(db: Session) -> dict[str, Any]:
    custom = db.scalars(select(ResumeTemplate).order_by(ResumeTemplate.created_at))
    return {
        "selected": load_settings(db).resume_template,
        "templates": [*(_builtin_dict(key) for key in BUILTIN_TEMPLATES), *(template_dict(row) for row in custom)],
    }


def get_template(db: Session, template_id: str) -> ResumeTemplate:
    row = db.get(ResumeTemplate, template_id)
    if row is None:
        raise AppError(404, "template_not_found", "Template not found")
    return row


def resolve_style(db: Session, key: str | None) -> TemplateStyle:
    """The style for a settings key; a deleted custom template falls back to the default."""
    if key in BUILTIN_TEMPLATES:
        return BUILTIN_TEMPLATES[key][1]
    if key and key.startswith("custom:"):
        row = db.get(ResumeTemplate, key.removeprefix("custom:"))
        if row is not None:
            return TemplateStyle.model_validate(row.style_json)
    return BUILTIN_TEMPLATES[DEFAULT_TEMPLATE][1]


def _unique_name(db: Session, wanted: str) -> str:
    taken = {name.lower() for name in db.scalars(select(ResumeTemplate.name))} | {name.lower() for name, _ in BUILTIN_TEMPLATES.values()}
    name, counter = wanted, 2
    while name.lower() in taken:
        name = f"{wanted} ({counter})"
        counter += 1
    return name


def _default_name(filename: str) -> str:
    stem = re.sub(r"[_\-.]+", " ", Path(filename).stem).strip()
    stem = re.sub(r"\b(resume|cv|final|v\d+)\b", "", stem, flags=re.IGNORECASE).strip()
    return (f"{stem} style" if stem else "Imported style")[:90]


def create_from_file(db: Session, *, filename: str, data: bytes, name: str | None = None) -> ResumeTemplate:
    if (db.query(ResumeTemplate).count()) >= MAX_TEMPLATES:
        raise AppError(422, "too_many_templates", f"Keep at most {MAX_TEMPLATES} templates; delete one you no longer use.")
    fmt = detect_format(filename, data)
    try:
        extracted = extract_style(fmt, data)
    except ValueError as exc:
        raise AppError(422, "no_style_found", str(exc)) from exc
    except Exception as exc:  # damaged files: python-docx / PyMuPDF raise many types
        raise AppError(422, "unreadable_file", "This file couldn't be read. Open it in Word, save it again, and retry.") from exc
    row = ResumeTemplate(
        name=_unique_name(db, (name or "").strip()[:90] or _default_name(filename)),
        style_json=extracted.style.model_dump(),
        source_filename=Path(filename).name[:255],
        source_format=fmt,
        notes_json=extracted.notes,
    )
    db.add(row)
    db.commit()
    return row


def create_from_resume_version(db: Session, *, candidate_id: str, version_id: str, name: str | None = None) -> ResumeTemplate:
    version = db.get(ResumeVersion, version_id)
    if version is None or version.candidate_id != candidate_id:
        raise AppError(404, "resume_version_not_found", "Resume version not found")
    path = Path(version.original_file_path)
    if not path.exists():
        raise AppError(410, "original_missing", "The original file for this resume version is no longer on disk.")
    return create_from_file(db, filename=version.original_filename, data=path.read_bytes(), name=name)


def update_template(db: Session, row: ResumeTemplate, *, name: str | None = None, style: dict[str, Any] | None = None) -> ResumeTemplate:
    if name is not None:
        cleaned = name.strip()[:90]
        if not cleaned:
            raise AppError(422, "invalid_name", "Give the template a name.")
        if cleaned.lower() != row.name.lower():
            row.name = _unique_name(db, cleaned)
    if style is not None:
        row.style_json = TemplateStyle.model_validate(style).model_dump()
    db.commit()
    return row


def delete_template(db: Session, row: ResumeTemplate) -> None:
    if load_settings(db).resume_template == custom_key(row.id):
        update_settings(db, AppSettingsUpdate(resume_template=DEFAULT_TEMPLATE))
    db.delete(row)
    db.commit()


# ---------------------------------------------------------------- sample documents


def sample_resume() -> ResumeContent:
    """Fictional content used to show what a template looks like."""
    return ResumeContent(
        contact=Contact(name="Jordan Avery", email="jordan.avery@example.com", phone="(555) 010-4477", location="Austin, TX"),
        headline="Senior Data Engineer",
        summary="Data engineer with 9 years building reliable batch and streaming pipelines on AWS for analytics and machine-learning teams.",
        skills=["Python", "SQL", "Apache Spark", "Apache Airflow", "AWS", "dbt", "Snowflake", "Terraform", "Kafka"],
        experience=[
            ResumeRole(
                role_id="R-1",
                employer="Northwind Analytics Inc",
                title="Senior Data Engineer",
                location="Austin, TX",
                start="2020-03",
                end="present",
                bullets=[
                    ResumeBullet(text="Built streaming pipelines with Kafka and Spark processing 2 billion events a day"),
                    ResumeBullet(text="Moved 40 nightly batch jobs to Airflow, cutting failures by 60%"),
                    ResumeBullet(text="Modeled the analytics warehouse in Snowflake with dbt and automated tests"),
                ],
            ),
            ResumeRole(
                role_id="R-2",
                employer="Bluebonnet Logistics LLC",
                title="Data Engineer",
                location="Dallas, TX",
                start="2016-06",
                end="2020-02",
                bullets=[
                    ResumeBullet(text="Designed the shipment data lake on AWS S3 and Glue"),
                    ResumeBullet(text="Managed infrastructure as code with Terraform across three environments"),
                ],
            ),
        ],
        education=[{"degree": "B.S.", "field": "Computer Science", "institution": "University of Texas at Austin", "end_year": 2016}],
        certifications=[{"name": "AWS Certified Data Engineer – Associate", "year": 2022}],
    )


def sample_cover_letter() -> CoverLetterContent:
    contact = sample_resume().contact
    return CoverLetterContent(
        date="October 14, 2026",
        company="Acme Data Co",
        job_title="Senior Data Engineer",
        paragraphs=[
            CoverParagraph(text="I am writing to apply for the Senior Data Engineer position at Acme Data Co. I bring 9 years of experience building data platforms."),
            CoverParagraph(text="At Northwind Analytics I built streaming pipelines with Kafka and Spark processing 2 billion events a day, and moved 40 nightly batch jobs to Airflow."),
            CoverParagraph(text="Thank you for considering my application. I would welcome the chance to discuss how I can support your team."),
        ],
        signature=contact.name,
        contact=contact,
    )


def write_samples(db: Session, config: AppConfig, key: str) -> dict[str, str | None]:
    """Writes a sample resume and cover letter in this template for opening in Word."""
    style = resolve_style(db, key)
    label = BUILTIN_TEMPLATES[key][0] if key in BUILTIN_TEMPLATES else get_template(db, key.removeprefix("custom:")).name
    folder = config.data_dir / "template-samples"
    stem = sanitize_component(label, max_length=50, fallback="Template")
    settings = load_settings(db)
    out: dict[str, str | None] = {}
    for kind, data in (("resume", render_resume(sample_resume(), style)), ("cover_letter", render_cover_letter(sample_cover_letter(), style))):
        try:
            docx = replace_file(folder / f"{stem}_sample_{kind}.docx", data)
        except PermissionError as exc:
            raise AppError(409, "file_in_use", "A sample from this template is open in Word. Close it and try again.") from exc
        out[f"{kind}_docx"] = str(docx)
        out[f"{kind}_pdf"] = None
        if settings.pdf_converter != "none" and resolve_converter(settings):
            try:
                out[f"{kind}_pdf"] = str(replace_file(docx.with_suffix(".pdf"), convert_to_pdf_bytes(docx, settings)))
            except PdfConversionError:
                pass
    return out
