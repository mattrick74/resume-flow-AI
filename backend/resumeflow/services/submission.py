"""The confirmation screen captured after the user submits an application (v2.0, requirements section 7).

The Chrome extension sends a PNG of the confirmation page; it is saved in the application folder as
``Submission_<date>_<time>.png`` and the application is recorded as submitted (the user submitted it themselves).
If the files are still being exported, the status is set when the export finishes.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from resumeflow.db.models import Application, Document
from resumeflow.domain.enums import DocumentFormat, DocumentStatus, DocumentType, PipelineStatus
from resumeflow.domain.status import Actor
from resumeflow.errors import AppError
from resumeflow.services.files import replace_file, sha256_bytes, write_unique_file

MAX_SCREENSHOT_BYTES = 15 * 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
OUTCOMES = (PipelineStatus.SUBMITTED, PipelineStatus.INTERVIEW, PipelineStatus.OFFER, PipelineStatus.REJECTED)


def screenshots(db: Session, app_id: str) -> list[Document]:
    return list(
        db.scalars(
            select(Document)
            .where(Document.application_id == app_id, Document.document_type == DocumentType.SUBMISSION_SCREENSHOT)
            .order_by(Document.version)
        )
    )


def submission_view(db: Session, app: Application) -> dict[str, Any]:
    return {
        "submitted_at": app.submitted_at.isoformat() if app.submitted_at else None,
        "screenshots": [
            {"id": d.id, "path": d.file_path, "exists": bool(d.file_path and Path(d.file_path).exists()), **(d.content_json or {})}
            for d in screenshots(db, app.id)
        ],
    }


def save_submission(
    db: Session,
    app: Application,
    image: bytes,
    *,
    page_url: str | None,
    page_title: str | None,
    detected_by: str,
    captured_at: datetime | None,
) -> Document:
    from resumeflow.services.applications import _now, _output_layout, _transition

    if not image.startswith(PNG_SIGNATURE):
        raise AppError(422, "not_png", "The screenshot must be a PNG image.")
    if len(image) > MAX_SCREENSHOT_BYTES:
        raise AppError(413, "screenshot_too_large", "The screenshot is larger than 15 MB.")
    if app.output_directory:
        folder = Path(app.output_directory)
    else:
        folder, _ = _output_layout(db, app)
    folder.mkdir(parents=True, exist_ok=True)
    when = (captured_at or datetime.now().astimezone()).astimezone()
    path = write_unique_file(folder / f"Submission_{when:%Y-%m-%d_%H%M}.png", image)
    version = (db.scalar(select(func.max(Document.version)).where(Document.application_id == app.id, Document.document_type == DocumentType.SUBMISSION_SCREENSHOT)) or 0) + 1
    doc = Document(
        application_id=app.id,
        document_type=DocumentType.SUBMISSION_SCREENSHOT,
        format=DocumentFormat.PNG,
        version=version,
        status=DocumentStatus.APPROVED,
        origin="captured",
        file_path=str(path),
        sha256=sha256_bytes(image),
        content_json={
            "page_url": (page_url or "")[:2000] or None,
            "page_title": (page_title or "")[:300] or None,
            "detected_by": detected_by,
            "captured_at": when.isoformat(),
        },
    )
    db.add(doc)
    if app.status == PipelineStatus.EXPORTED:
        _transition(app, PipelineStatus.SUBMITTED, Actor.OPERATOR)  # the user submitted it on the job site
    if app.status not in OUTCOMES[1:] and app.submitted_at is None:
        app.submitted_at = _now()  # if the export is still running, SUBMITTED is set when it finishes
    db.commit()
    refresh_metadata_file(db, app)
    return doc


def delete_submission(db: Session, app: Application, doc_id: str) -> None:
    """Deletes one captured screen; with none left, the Submitted status is undone (back to Exported)."""
    from resumeflow.services.applications import _transition

    doc = db.get(Document, doc_id)
    if doc is None or doc.application_id != app.id or doc.document_type != DocumentType.SUBMISSION_SCREENSHOT:
        raise AppError(404, "not_found", "That captured screen doesn't exist.")
    if doc.file_path:
        Path(doc.file_path).unlink(missing_ok=True)
    db.delete(doc)
    db.flush()
    if not screenshots(db, app.id):
        if app.status == PipelineStatus.SUBMITTED:
            _transition(app, PipelineStatus.EXPORTED, Actor.OPERATOR)
        if app.status not in OUTCOMES:
            app.submitted_at = None
    db.commit()
    refresh_metadata_file(db, app)


def apply_pending_submission(db: Session, app: Application) -> None:
    """After an export: a confirmation captured while the files were being written marks the application submitted."""
    from resumeflow.services.applications import _transition

    if app.status == PipelineStatus.EXPORTED and app.submitted_at is not None and screenshots(db, app.id):
        _transition(app, PipelineStatus.SUBMITTED, Actor.OPERATOR)
        db.commit()
        refresh_metadata_file(db, app)


def refresh_metadata_file(db: Session, app: Application) -> None:
    """Rewrites the application's metadata.json (if it was exported) so it lists the captured screens."""
    from resumeflow.services.applications import build_metadata

    row = db.scalar(select(Document).where(Document.application_id == app.id, Document.document_type == DocumentType.METADATA, Document.format == DocumentFormat.JSON))
    if row is None or not row.file_path or not Path(row.file_path).exists():
        return
    data = json.dumps(build_metadata(db, app), indent=2, ensure_ascii=False).encode("utf-8")
    replace_file(Path(row.file_path), data)
    row.sha256 = sha256_bytes(data)
    db.commit()
