"""Application workflow (PRD §4, §8, §9, §14): create → generate → validate → review →
approve → export, with versioned documents and operator-only outcomes."""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from resumeflow import __version__
from resumeflow.db.models import (
    Application,
    Candidate,
    CandidateMatch,
    CandidateRoleExperience,
    CandidateSkillEvidence,
    Document,
    GenerationRun,
    Job,
    ProcessingJob,
)
from resumeflow.domain.content import CoverLetterContent, ResumeContent
from resumeflow.domain.enums import (
    DocumentFormat,
    DocumentStatus,
    DocumentType,
    PipelineStatus,
    ProcessingJobType,
    ProcessingStatus,
    SelectionMethod,
)
from resumeflow.domain.status import Actor, InvalidTransition, check_application_transition
from resumeflow.errors import AppError
from resumeflow.services import experience
from resumeflow.services.ai.service import AIService
from resumeflow.services.candidates import candidate_readiness, get_candidate, verified_snapshot
from resumeflow.services.files import MAX_PATH_LENGTH, replace_file, sanitize_component, sha256_bytes, write_unique_file
from resumeflow.services.generation import generate_documents
from resumeflow.services.inference import semantic_evidence
from resumeflow.services.matching import rejected_skills
from resumeflow.services.jobs import get_job, requirements_of
from resumeflow.services.pdf import PdfConversionError, convert_documents_to_pdf, resolve_converter
from resumeflow.services.queue import JobContext, ProcessingQueue
from resumeflow.services.render import render_cover_letter, render_resume
from resumeflow.services.templates import resolve_style
from resumeflow.services.settings import load_settings
from resumeflow.services.validation import issue

logger = logging.getLogger(__name__)
CONTENT_MODELS = {DocumentType.RESUME: ResumeContent, DocumentType.COVER_LETTER: CoverLetterContent}
IN_PROGRESS = {PipelineStatus.GENERATING, PipelineStatus.VALIDATING}


def _now() -> datetime:
    return datetime.now(UTC)


def _transition(app: Application, target: PipelineStatus, actor: Actor) -> None:
    if app.status == target:
        return
    try:
        check_application_transition(app.status, target, actor)
    except InvalidTransition as exc:
        raise AppError(409, "invalid_transition", str(exc)) from exc
    app.status = target


def get_application(db: Session, application_id: str) -> Application:
    app = db.get(Application, application_id)
    if app is None:
        raise AppError(404, "application_not_found", "Application not found")
    return app


def latest_document(db: Session, app_id: str, doc_type: DocumentType, fmt: DocumentFormat = DocumentFormat.STRUCTURED) -> Document | None:
    return db.scalar(
        select(Document)
        .where(Document.application_id == app_id, Document.document_type == doc_type, Document.format == fmt)
        .order_by(Document.version.desc())
        .limit(1)
    )


def _content(doc: Document | None, doc_type: DocumentType):
    if doc is None or doc.content_json is None:
        return None
    return CONTENT_MODELS[doc_type].model_validate(doc.content_json)


# ---------------------------------------------------------------- create & generate


def create_application(
    db: Session,
    queue: ProcessingQueue,
    *,
    job_id: str,
    candidate_id: str,
    override_reason: str | None,
    generate: bool = True,
    instructions: str | None = None,
) -> Application:
    job = get_job(db, job_id)
    candidate = get_candidate(db, candidate_id)
    ready = candidate_readiness(db, candidate)
    if candidate.consent_confirmed_at is None:
        raise AppError(409, "candidate_not_authorized", "Confirm the candidate's authorization before creating an application.")
    if not ready["ready_for_matching"]:
        raise AppError(409, "candidate_not_ready", "Candidate profile isn't ready: " + "; ".join(ready["blockers"]))

    # A candidate may have several applications for the same job (each is a fresh generation with its own number).

    match = db.scalar(select(CandidateMatch).where(CandidateMatch.job_id == job.id, CandidateMatch.candidate_id == candidate.id))
    is_top = match is not None and match.rank == 1
    reason = (override_reason or "").strip() or None
    if not is_top and not reason:
        raise AppError(422, "override_reason_required", "Explain why you're choosing a candidate other than the top-ranked match.")

    snapshot = verified_snapshot(db, candidate)
    today = date.today()
    # Provenance (v1.8 EXP-07): which profile revision, and which approved additions, this application is built on.
    revision, additions = snapshot.get("profile_revision"), snapshot.get("addition_ids") or []
    for _ in range(5):
        sequence = (db.scalar(select(func.max(Application.sequence_number)).where(Application.application_date == today)) or 0) + 1
        app = Application(
            job_id=job.id,
            candidate_id=candidate.id,
            application_date=today,
            sequence_number=sequence,
            status=PipelineStatus.MATCHED,
            selection_method=SelectionMethod.AUTOMATIC if is_top else SelectionMethod.MANUAL_OVERRIDE,
            override_reason=reason,
            profile_snapshot_json=snapshot,
            profile_revision=revision,
            addition_ids_json=additions,
            instructions=(instructions or "").strip()[:MAX_INSTRUCTIONS] or None,
        )
        db.add(app)
        try:
            db.commit()
            break
        except IntegrityError:  # another application took this sequence number
            db.rollback()
    else:
        raise AppError(409, "sequence_conflict", "Couldn't allocate an application number; try again.")
    if generate:
        enqueue_generation(db, queue, app, document="both", section="all")
    return app


MAX_INSTRUCTIONS = 2000


def combined_instructions(default: str | None, own: str | None) -> str:
    parts = []
    if (default or "").strip():
        parts.append(f"For every application: {default.strip()}")
    if (own or "").strip():
        parts.append(f"For this application: {own.strip()}")
    return "\n".join(parts)


def set_instructions(app: Application, instructions: str | None) -> None:
    """``None`` keeps what the application has; an empty string clears it."""
    if instructions is not None:
        app.instructions = instructions.strip()[:MAX_INSTRUCTIONS] or None


def refresh_snapshot(db: Session, app: Application) -> bool:
    """Before an operator-requested regeneration, use the profile as it is now (facts added or verified since).

    Skipped while the candidate's profile is being reviewed again; the earlier snapshot is used then.
    """
    candidate = get_candidate(db, app.candidate_id)
    ready = candidate_readiness(db, candidate)
    if not ready["ready_for_matching"]:
        return False
    app.profile_snapshot_json = verified_snapshot(db, candidate)
    app.profile_revision = app.profile_snapshot_json.get("profile_revision")
    app.addition_ids_json = app.profile_snapshot_json.get("addition_ids") or []
    return True


def enqueue_generation(db: Session, queue: ProcessingQueue, app: Application, *, document: str, section: str) -> ProcessingJob:
    if app.status in IN_PROGRESS:
        raise AppError(409, "generation_in_progress", "Documents are already being generated for this application.")
    _transition(app, PipelineStatus.GENERATING, Actor.SYSTEM)
    app.error = None
    db.commit()
    return queue.enqueue(
        db,
        ProcessingJobType.GENERATE_DOCUMENTS,
        idempotency_key=f"generate:{app.id}:{uuid4().hex[:12]}",
        application_id=app.id,
        job_id=app.job_id,
        payload={"document": document, "section": section},
    )


def _save_version(db: Session, app_id: str, doc_type: DocumentType, content: Any, issues: list, origin: str, meta: dict | None = None) -> Document:
    version = (
        db.scalar(
            select(func.max(Document.version)).where(
                Document.application_id == app_id, Document.document_type == doc_type, Document.format == DocumentFormat.STRUCTURED
            )
        )
        or 0
    ) + 1
    db.execute(
        update(Document)
        .where(
            Document.application_id == app_id,
            Document.document_type == doc_type,
            Document.format == DocumentFormat.STRUCTURED,
            Document.status == DocumentStatus.DRAFT,
        )
        .values(status=DocumentStatus.SUPERSEDED)
    )
    payload = content.model_dump(mode="json")
    if meta:
        payload["_meta"] = meta
    doc = Document(
        application_id=app_id,
        document_type=doc_type,
        format=DocumentFormat.STRUCTURED,
        version=version,
        status=DocumentStatus.DRAFT,
        content_json=payload,
        warnings_json=issues,
        origin=origin,
    )
    db.add(doc)
    return doc


def handle_generate(ai: AIService) -> Callable[[JobContext], None]:
    def run(ctx: JobContext) -> None:
        document = ctx.payload.get("document", "both")
        section = ctx.payload.get("section", "all")
        with ctx.session() as db:
            app = get_application(db, ctx.application_id)  # type: ignore[arg-type]
            if app.status in IN_PROGRESS:
                app.status = PipelineStatus.GENERATING  # resumed after a restart
            else:
                _transition(app, PipelineStatus.GENERATING, Actor.SYSTEM)
            job = db.get(Job, app.job_id)
            req = requirements_of(job)
            match = db.scalar(select(CandidateMatch).where(CandidateMatch.job_id == app.job_id, CandidateMatch.candidate_id == app.candidate_id))
            base = app.profile_snapshot_json or {}
            semantic = semantic_evidence(match.matching_details_json if match else None)
            not_used = rejected_skills(db, [app.candidate_id]).get(app.candidate_id, set())
            previous_resume = _content(latest_document(db, app.id, DocumentType.RESUME), DocumentType.RESUME)
            previous_cover = _content(latest_document(db, app.id, DocumentType.COVER_LETTER), DocumentType.COVER_LETTER)
            company, title, raw_jd, app_id = job.company, job.title, job.raw_jd, app.id
            settings = load_settings(db)
            instructions = combined_instructions(settings.writing_instructions, app.instructions)
            density = settings.bullet_density
            db.commit()
        ctx.progress(5)

        # Resume generation now returns its evidence-checked skill placements in the same response. The cover-letter
        # request runs concurrently from the original evidence and does not wait for those resume-specific decisions.
        resume, cover, notes, mode, snapshot, expansion_notes = generate_documents(
            ai,
            base,
            req,
            company=company,
            job_title=title,
            raw_jd=raw_jd,
            application_id=app_id,
            document=document,
            section=section,
            previous_resume=previous_resume,
            previous_cover=previous_cover,
            instructions=instructions,
            density=density,
            semantic=semantic,
            exclude=not_used,
            on_stage_complete=lambda stage, duration_ms, details: ctx.complete_stage(
                stage,
                message="Resume writing completed" if stage == "resume_writing" else "Cover-letter writing completed",
                duration_ms=duration_ms,
                details={**details, "section": section},
            ),
        )
        ctx.check_cancelled()
        ctx.progress(80)

        if snapshot != base:
            with ctx.session() as db:
                get_application(db, app_id).profile_snapshot_json = snapshot  # edits are checked against the same record
                db.commit()

        # No checking step: the documents written from the API response are saved as they are and exported straight
        # away. The generation notes are kept with the version for reference.
        results = []
        if resume is not None and document in ("both", "resume"):
            results.append((DocumentType.RESUME, resume))
        if cover is not None and document in ("both", "cover_letter"):
            results.append((DocumentType.COVER_LETTER, cover))
        gap_notes = skill_gap_notes(snapshot.get("skill_gap_report") or []) + expansion_notes
        with ctx.session() as db:
            app = get_application(db, app_id)
            for doc_type, content in results:
                first = latest_document(db, app_id, doc_type) is None
                notes_for_doc = [issue("info", "generation_note", n, doc_type.value) for n in notes]
                if doc_type == DocumentType.RESUME:
                    notes_for_doc += [issue("info", "skill_gap_report", n, "experience") for n in gap_notes]
                _save_version(db, app_id, doc_type, content, notes_for_doc, "generated" if first else "regenerated", {"mode": mode, "section": section})
            _transition(app, PipelineStatus.AWAITING_REVIEW, Actor.SYSTEM)
            db.commit()
            auto_export(db, ctx.queue, app)
            store_coverage(db, app)  # the Coverage tab's numbers; after the export is queued so it never delays the files
            db.commit()

    return run


def skill_gap_notes(report: list[dict[str, Any]]) -> list[str]:
    """The gap report as notes on the resume: what was added under which employer, and what wasn't added and why."""
    notes = []
    added = [f"{r['skill']} ({r['employer']})" for r in report if r["status"] == "added_to_experience"]
    if added:
        notes.append("Job skills the candidate's experience shows, named in that employer's bullets: " + ", ".join(added) + ".")
    related = [
        f"{r['skill']} (related: {', '.join(r['related'])} at {r['employer']})" if r["related"] else f"{r['skill']} (related work at {r['employer']})"
        for r in report
        if r["status"] == "transferable"
    ]
    if related:
        notes.append("Not claimed; related experience emphasised instead: " + "; ".join(related) + ".")
    missing = [r["skill"] for r in report if r["status"] == "not_supported"]
    if missing:
        notes.append("Not added, because nothing in the candidate's experience shows them: " + ", ".join(missing) + ".")
    return notes


def generation_failed(ctx: JobContext, exc: Exception) -> None:
    with ctx.session() as db:
        app = db.get(Application, ctx.application_id)
        if app is None:
            return
        app.status = PipelineStatus.FAILED
        app.error = ("Cancelled by operator" if exc.__class__.__name__ == "JobCancelled" else str(exc) or type(exc).__name__)[:2000]
        db.commit()


# ---------------------------------------------------------------- automatic finalization and editing


def edit_document(db: Session, app: Application, doc_type: DocumentType, content: dict[str, Any]) -> Document:
    if app.status not in (PipelineStatus.AWAITING_REVIEW, PipelineStatus.APPROVED, PipelineStatus.EXPORTED):
        raise AppError(409, "not_editable", "Documents can only be edited after generation finishes.")
    model = CONTENT_MODELS[doc_type].model_validate(content)
    doc = _save_version(db, app.id, doc_type, model, [], "edited", {"mode": "edited"})  # saved as written: no checking step
    if app.status != PipelineStatus.AWAITING_REVIEW:
        _transition(app, PipelineStatus.AWAITING_REVIEW, Actor.OPERATOR)
    db.flush()
    store_coverage(db, app)
    db.commit()
    return doc


def _blocking_errors(db: Session, app: Application) -> list[dict[str, Any]]:
    docs = [latest_document(db, app.id, t) for t in (DocumentType.RESUME, DocumentType.COVER_LETTER)]
    if any(d is None for d in docs):
        raise AppError(409, "documents_missing", "Both the resume and cover letter must be generated first.")
    return [i for d in docs for i in (d.warnings_json or []) if i.get("severity") == "error"]  # type: ignore[union-attr]


def approve(db: Session, app: Application, acknowledge_warnings: bool, *, actor: Actor = Actor.OPERATOR) -> None:
    if app.status != PipelineStatus.AWAITING_REVIEW:
        raise AppError(409, "not_reviewable", "Only applications awaiting review can be approved.")
    errors = _blocking_errors(db, app)
    if errors and not acknowledge_warnings:
        raise AppError(409, "unresolved_issues", f"{len(errors)} validation error(s) need attention or explicit acknowledgement.", {"errors": errors})
    now = _now()
    for doc_type in (DocumentType.RESUME, DocumentType.COVER_LETTER):
        doc = latest_document(db, app.id, doc_type)
        doc.status = DocumentStatus.APPROVED  # type: ignore[union-attr]
        doc.approved_at = now  # type: ignore[union-attr]
    _transition(app, PipelineStatus.APPROVED, actor)
    app.approved_at = now
    app.approved_by = "automatic" if actor is Actor.AUTOMATION else "operator"
    db.commit()


def auto_export(db: Session, queue: ProcessingQueue, app: Application) -> str:
    """Automatically approve and export every complete generated application."""
    if app.status != PipelineStatus.AWAITING_REVIEW:
        return "not_ready"
    try:
        _blocking_errors(db, app)
    except AppError:
        return "incomplete"  # only one document so far (for example, during a partial regeneration)
    approve(db, app, acknowledge_warnings=True, actor=Actor.AUTOMATION)
    request_export(db, queue, app)
    return "exported"


def resume_automatic_exports(db: Session, queue: ProcessingQueue) -> int:
    """Continue applications left in the former manual-review states.

    Export jobs are idempotent by document version, so restarting the app cannot
    queue duplicate output for the same resume and cover-letter versions.
    """
    resumed = 0
    pending = list(
        db.scalars(
            select(Application)
            .where(Application.status.in_((PipelineStatus.AWAITING_REVIEW, PipelineStatus.APPROVED)))
            .order_by(Application.created_at)
        )
    )
    for app in pending:
        try:
            active_generation = db.scalar(
                select(ProcessingJob.id).where(
                    ProcessingJob.application_id == app.id,
                    ProcessingJob.job_type == ProcessingJobType.GENERATE_DOCUMENTS,
                    ProcessingJob.status.in_((ProcessingStatus.QUEUED, ProcessingStatus.RUNNING)),
                )
            )
            if active_generation is not None:
                continue
            if app.status == PipelineStatus.AWAITING_REVIEW:
                resumed += auto_export(db, queue, app) == "exported"
            else:
                export_job = request_export(db, queue, app)
                if export_job.status in (ProcessingStatus.FAILED, ProcessingStatus.CANCELLED):
                    queue.retry(db, export_job.id)
                resumed += 1
        except AppError as exc:
            logger.warning("Could not resume automatic export for application %s: %s", app.id, exc)
    return resumed


# ---------------------------------------------------------------- confirmed experience (v1.8)


def coverage_report(db: Session, app: Application) -> dict[str, Any]:
    """VAL-10 for the latest resume version."""
    job = db.get(Job, app.job_id)
    candidate = db.get(Candidate, app.candidate_id)
    resume_doc = latest_document(db, app.id, DocumentType.RESUME)
    gaps = experience.analyze_gaps(db, job, candidate)
    return experience.coverage(
        requirements_of(job),
        _content(resume_doc, DocumentType.RESUME),
        app.profile_snapshot_json or {},
        experience.baseline_for(db, candidate),
        (resume_doc.warnings_json or []) if resume_doc else [],
        [i["skill"] for i in gaps["items"] if i["needs_answer"]],
    )


def store_coverage(db: Session, app: Application) -> None:
    """Keeps the headline coverage numbers on the application (metadata, analytics). Never blocks generation."""
    try:
        report = coverage_report(db, app)
    except Exception:  # a report is informational; generation and export must not fail because of it
        logger.exception("Coverage report failed for application %s", app.id)
        return
    app.coverage_json = {
        **{key: report[key] for key in ("required", "preferred", "keyword_coverage_pct", "confirmed_coverage_pct", "original_coverage_pct", "unsupported_claims")},
        "duplicate_pairs": len(report["duplicates"]),
        "computed_at": _now().isoformat(),
    }


def coverage_view(db: Session, app: Application) -> dict[str, Any]:
    """GET /applications/{id}/coverage: the quality report plus what changed against the imported resume and the prior version."""
    candidate = db.get(Candidate, app.candidate_id)
    versions = list(
        db.scalars(
            select(Document)
            .where(Document.application_id == app.id, Document.document_type == DocumentType.RESUME, Document.format == DocumentFormat.STRUCTURED)
            .order_by(Document.version.desc())
            .limit(2)
        )
    )
    current = _content(versions[0], DocumentType.RESUME) if versions else None
    previous = _content(versions[1], DocumentType.RESUME) if len(versions) > 1 else None
    return {
        "coverage": coverage_report(db, app),
        "changes": experience.resume_changes(current, previous, app.profile_snapshot_json or {}, experience.baseline_for(db, candidate)),
        "profile_revision": app.profile_revision,
        "current_profile_revision": candidate.profile_revision,
    }


def suggest_additions(db: Session, app: Application) -> dict[str, Any]:
    """GEN-11: the internal panel of suggested skills (gaps to answer) and drafted bullets awaiting approval."""
    job = db.get(Job, app.job_id)
    candidate = db.get(Candidate, app.candidate_id)
    overview = experience.evidence_overview(db, candidate)
    return {
        "gaps": experience.analyze_gaps(db, job, candidate),
        "suggestions": [r for r in overview["records"] if r["status"] == "suggested"],
        "profile_revision": app.profile_revision,
        "current_profile_revision": candidate.profile_revision,
    }


def approve_additions(
    db: Session, queue: ProcessingQueue, app: Application, record_ids: list[str], *, acknowledge: bool, edits: dict[str, str] | None, regenerate: bool
) -> dict[str, Any]:
    """Approves suggested bullets and, with ``regenerate``, regenerates both documents from the refreshed profile
    ("Regenerate with approved additions", GEN-10)."""
    candidate = get_candidate(db, app.candidate_id)
    result = (
        experience.approve_records(db, queue, candidate, record_ids, acknowledge=acknowledge, edits=edits)
        if record_ids
        else {"records": [], "resumed": [], "rematched": []}
    )
    regenerating = False
    if regenerate:
        if not refresh_snapshot(db, app):
            raise AppError(409, "candidate_not_ready", "The candidate's profile isn't ready for matching, so it can't be used yet.")
        enqueue_generation(db, queue, app, document="both", section="all")
        regenerating = True
    return {**result, "regenerating": regenerating}


def set_outcome(db: Session, app: Application, status: PipelineStatus) -> None:
    if status not in (PipelineStatus.SUBMITTED, PipelineStatus.INTERVIEW, PipelineStatus.REJECTED, PipelineStatus.OFFER):
        raise AppError(422, "invalid_outcome", "Outcome must be SUBMITTED, INTERVIEW, REJECTED or OFFER.")
    _transition(app, status, Actor.OPERATOR)
    if status == PipelineStatus.SUBMITTED:
        app.submitted_at = _now()
    db.commit()


# ---------------------------------------------------------------- export


def request_export(db: Session, queue: ProcessingQueue, app: Application) -> ProcessingJob:
    if app.status != PipelineStatus.APPROVED:
        raise AppError(409, "not_approved", "Approve the documents before exporting.")
    resume = latest_document(db, app.id, DocumentType.RESUME)
    cover = latest_document(db, app.id, DocumentType.COVER_LETTER)
    if not resume or not cover or resume.status != DocumentStatus.APPROVED or cover.status != DocumentStatus.APPROVED:
        raise AppError(409, "not_approved", "The latest resume and cover letter must both be approved.")
    return queue.enqueue(
        db,
        ProcessingJobType.EXPORT_APPLICATION,
        idempotency_key=f"export:{app.id}:r{resume.version}:c{cover.version}",
        application_id=app.id,
        job_id=app.job_id,
    )


def request_pdf_retry(db: Session, queue: ProcessingQueue, app: Application) -> ProcessingJob:
    if app.exported_at is None:
        raise AppError(409, "not_exported", "Export the application first.")
    return queue.enqueue(
        db,
        ProcessingJobType.CONVERT_PDF,
        idempotency_key=f"pdf:{app.id}:{uuid4().hex[:12]}",
        application_id=app.id,
        job_id=app.job_id,
    )


def _output_layout(db: Session, app: Application) -> tuple[Path, str]:
    """``<output root>/YYYY-MM-DD/NNN Profile Name`` for every application."""
    settings = load_settings(db)
    candidate = db.get(Candidate, app.candidate_id)
    job = db.get(Job, app.job_id)
    prefix = f"{app.sequence_number:03d}_{sanitize_component(job.company or job.title or 'Job', max_length=40)}"
    longest_file = len(prefix) + 1 + max(len(name) for name in _FILE_NAMES.values()) + len("_v99")
    parent = Path(settings.output_root) / app.application_date.isoformat()
    number = f"{app.sequence_number:03d}"
    # Size the profile portion so the longest file name (plus a "_v99" suffix) stays within the Windows path limit.
    available = max(12, min(80, MAX_PATH_LENGTH - len(str(parent)) - len(number) - longest_file - 3))
    profile = sanitize_component(candidate.full_name, max_length=available, fallback="Profile").replace("_", " ")
    return parent / f"{number} {profile}", prefix


_FILE_NAMES = {
    (DocumentType.RESUME, DocumentFormat.DOCX): "Resume.docx",
    (DocumentType.RESUME, DocumentFormat.PDF): "Resume.pdf",
    (DocumentType.COVER_LETTER, DocumentFormat.DOCX): "Cover_Letter.docx",
    (DocumentType.COVER_LETTER, DocumentFormat.PDF): "Cover_Letter.pdf",
    (DocumentType.JOB_DESCRIPTION, DocumentFormat.TXT): "JD.txt",
    (DocumentType.JOB_URL, DocumentFormat.TXT): "Job_URL.txt",
    (DocumentType.METADATA, DocumentFormat.JSON): "Metadata.json",
}


def _file_doc(db: Session, app_id: str, doc_type: DocumentType, fmt: DocumentFormat, version: int) -> Document | None:
    return db.scalar(
        select(Document).where(
            Document.application_id == app_id, Document.document_type == doc_type, Document.format == fmt, Document.version == version
        )
    )


def _ensure_file(ctx: JobContext, app_id: str, doc_type: DocumentType, fmt: DocumentFormat, version: int, folder: Path, prefix: str, produce: Callable[[], bytes]) -> Path:
    """Writes one output file once. Re-running after a crash reuses what was already written."""
    with ctx.session() as db:
        existing = _file_doc(db, app_id, doc_type, fmt, version)
        if existing and existing.file_path and Path(existing.file_path).exists():
            return Path(existing.file_path)
    data = produce()
    path = write_unique_file(folder / f"{prefix}_{_FILE_NAMES[(doc_type, fmt)]}", data)
    with ctx.session() as db:
        row = _file_doc(db, app_id, doc_type, fmt, version)
        if row is None:
            row = Document(application_id=app_id, document_type=doc_type, format=fmt, version=version, origin="exported")
            db.add(row)
        row.status = DocumentStatus.APPROVED
        row.file_path = str(path)
        row.sha256 = sha256_bytes(data)
        db.commit()
    return path


def build_metadata(db: Session, app: Application) -> dict[str, Any]:
    job = db.get(Job, app.job_id)
    candidate = db.get(Candidate, app.candidate_id)
    settings = load_settings(db)
    files = list(
        db.scalars(
            select(Document)
            .where(Document.application_id == app.id, Document.format != DocumentFormat.STRUCTURED, Document.document_type != DocumentType.METADATA)
            .order_by(Document.document_type, Document.format, Document.version)
        )
    )
    runs = list(db.scalars(select(GenerationRun).where((GenerationRun.application_id == app.id) | (GenerationRun.job_id == app.job_id))))
    resume = latest_document(db, app.id, DocumentType.RESUME)
    cover = latest_document(db, app.id, DocumentType.COVER_LETTER)
    # v1.8 EXP-07: which confirmed additions and answers this application is built on. Ids, skills and times only;
    # the operator's own descriptions stay in the app.
    additions = [r for r in (db.get(CandidateRoleExperience, rid) for rid in app.addition_ids_json or []) if r is not None]
    job_skills = {i["normalized"] for i in experience.job_skill_items(requirements_of(job))[0]}
    answers = db.scalars(
        select(CandidateSkillEvidence).where(CandidateSkillEvidence.candidate_id == app.candidate_id, CandidateSkillEvidence.normalized_name.in_(job_skills))
    )

    def counts(doc: Document | None) -> dict[str, int] | None:
        if doc is None:
            return None
        return {s: sum(1 for i in doc.warnings_json or [] if i.get("severity") == s) for s in ("error", "warning")}

    return {
        "schema": "resumeflow.application/1",
        "application_id": app.id,
        "job_id": app.job_id,
        "candidate_id": app.candidate_id,
        "candidate_name": candidate.full_name,
        "company": job.company,
        "job_title": job.title,
        "source_url": job.job_url or None,
        "application_date": app.application_date.isoformat(),
        "sequence_number": app.sequence_number,
        "generation_date": (resume.created_at.isoformat() if resume else None),
        "approved_at": app.approved_at.isoformat() if app.approved_at else None,
        "exported_at": (app.exported_at or _now()).isoformat(),
        "selection_method": app.selection_method.value,
        "override_reason": app.override_reason,
        # An automatic export (Settings: export automatically) is recorded as such, never as a person's review.
        "review_status": "AUTO_APPROVED" if app.approved_by == "automatic" else "APPROVED",
        "writing_instructions": app.instructions,
        "generation_mode": ((resume.content_json or {}).get("_meta", {}).get("mode") if resume else None),
        "models": {
            "analysis": settings.analysis_model,
            "writing": settings.writing_model,
            "used": sorted({r.model for r in runs if r.status.value == "succeeded"}),
        },
        "tokens": {"input": sum(r.input_tokens for r in runs), "output": sum(r.output_tokens for r in runs)},
        "estimated_cost_usd": round(sum((r.actual_cost_micros or r.estimated_cost_micros) for r in runs) / 1_000_000, 4),
        "document_versions": {"resume": resume.version if resume else None, "cover_letter": cover.version if cover else None},
        "files": [
            {"type": f.document_type.value, "format": f.format.value, "version": f.version, "path": f.file_path, "sha256": f.sha256}
            for f in files
        ],
        "pdf_status": app.pdf_status,
        "profile_revision": app.profile_revision,
        "approved_additions": [
            {
                "id": r.id,
                "role": r.role_label,
                "skills": r.skills_json or [],
                "approved_at": r.approved_at.isoformat() if r.approved_at else None,
                "source_job_ids": r.source_job_ids_json or [],
            }
            for r in additions
        ],
        "skill_confirmations": [
            {"skill": a.skill_name, "decision": a.decision.value, "confirmed_at": a.decided_at.isoformat()} for a in answers
        ],
        # Job skills placed at an employer because that employer's own bullets show them (v1.8.5).
        "skills_from_existing_experience": [
            {"skill": p["skill"], "employer": p["employer"], "method": p["method"], "bullet_ids": p["bullet_ids"], "evidence": p.get("evidence")}
            for p in (app.profile_snapshot_json or {}).get("inferred_skills", [])
        ],
        # Every job skill and what the resume does with it; unresolved ones were not added (v1.8.5).
        "skill_gap_report": (app.profile_snapshot_json or {}).get("skill_gap_report", []),
        "validation": {"resume": counts(resume), "cover_letter": counts(cover)},
        "coverage": app.coverage_json,
        "app_version": __version__,
    }


def _write_metadata(ctx: JobContext, app_id: str, folder: Path, prefix: str) -> None:
    with ctx.session() as db:
        app = get_application(db, app_id)
        data = json.dumps(build_metadata(db, app), indent=2, ensure_ascii=False).encode("utf-8")
        existing = _file_doc(db, app_id, DocumentType.METADATA, DocumentFormat.JSON, 1)
        if existing and existing.file_path and Path(existing.file_path).exists():
            replace_file(Path(existing.file_path), data)  # our own metadata for this application
            existing.sha256 = sha256_bytes(data)
            db.commit()
            return
    _ensure_file(ctx, app_id, DocumentType.METADATA, DocumentFormat.JSON, 1, folder, prefix, lambda: data)


def _existing_file(ctx: JobContext, app_id: str, doc_type: DocumentType, fmt: DocumentFormat, version: int) -> bool:
    with ctx.session() as db:
        row = _file_doc(db, app_id, doc_type, fmt, version)
        return bool(row and row.file_path and Path(row.file_path).exists())


def _convert_pdfs(ctx: JobContext, app_id: str, folder: Path, prefix: str, documents: list[tuple[DocumentType, int, bytes]]) -> tuple[str, str | None]:
    """Converts the rendered DOCX documents (bytes) that don't have a PDF yet, all in one converter call."""
    started = time.perf_counter()
    with ctx.session() as db:
        settings = load_settings(db)
    if settings.pdf_converter == "none":
        ctx.complete_stage(
            "pdf_conversion",
            message="PDF conversion skipped",
            started_at=started,
            status="skipped",
            details={"pdf_status": "skipped", "reason": "disabled", "document_count": len(documents)},
        )
        return "skipped", None
    converter = resolve_converter(settings)
    if converter is None:
        error = "No PDF converter available (install LibreOffice or Microsoft Word)."
        ctx.complete_stage(
            "pdf_conversion",
            message="PDF conversion failed",
            started_at=started,
            status="failed",
            details={"pdf_status": "failed", "error": error, "document_count": len(documents)},
        )
        return "failed", error
    todo = [(t, v, data) for t, v, data in documents if not _existing_file(ctx, app_id, t, DocumentFormat.PDF, v)]
    if not todo:
        ctx.complete_stage(
            "pdf_conversion",
            message="PDF files already available",
            started_at=started,
            status="skipped",
            details={"pdf_status": "succeeded", "reason": "already_exists", "converter": converter, "document_count": 0},
        )
        return "succeeded", None
    try:
        pdfs = convert_documents_to_pdf([data for _, _, data in todo], settings)
    except PdfConversionError as exc:
        error = str(exc)[:2000]
        ctx.complete_stage(
            "pdf_conversion",
            message="PDF conversion failed",
            started_at=started,
            status="failed",
            details={"pdf_status": "failed", "error": error, "converter": converter, "document_count": len(todo)},
        )
        return "failed", error
    for (doc_type, version, _), pdf in zip(todo, pdfs):
        _ensure_file(ctx, app_id, doc_type, DocumentFormat.PDF, version, folder, prefix, lambda p=pdf: p)
    ctx.complete_stage(
        "pdf_conversion",
        message="PDF conversion completed",
        started_at=started,
        details={"pdf_status": "succeeded", "converter": converter, "document_count": len(todo)},
    )
    return "succeeded", None


def _save_word_files(ctx: JobContext, app_id: str, folder: Path, prefix: str, documents: list[tuple[DocumentType, int, bytes]]) -> None:
    for doc_type, version, data in documents:
        _ensure_file(ctx, app_id, doc_type, DocumentFormat.DOCX, version, folder, prefix, lambda d=data: d)


def _render_document(ctx: JobContext, doc_type: DocumentType, version: int, content: Any, template: Any) -> bytes:
    started = time.perf_counter()
    data = render_resume(content, template) if doc_type == DocumentType.RESUME else render_cover_letter(content, template)
    ctx.complete_stage(
        "resume_render" if doc_type == DocumentType.RESUME else "cover_letter_render",
        message="Resume rendering completed" if doc_type == DocumentType.RESUME else "Cover-letter rendering completed",
        started_at=started,
        details={"document_type": doc_type.value, "version": version},
    )
    return data


def handle_export(ctx: JobContext) -> None:
    export_started = time.perf_counter()
    with ctx.session() as db:
        app = get_application(db, ctx.application_id)  # type: ignore[arg-type]
        job = db.get(Job, app.job_id)
        settings = load_settings(db)
        resume_doc = latest_document(db, app.id, DocumentType.RESUME)
        cover_doc = latest_document(db, app.id, DocumentType.COVER_LETTER)
        if not resume_doc or not cover_doc or resume_doc.status != DocumentStatus.APPROVED or cover_doc.status != DocumentStatus.APPROVED:
            raise AppError(409, "not_approved", "The latest documents are no longer approved; review them again.")
        resume = ResumeContent.model_validate(resume_doc.content_json)
        cover = CoverLetterContent.model_validate(cover_doc.content_json)
        versions = (resume_doc.version, cover_doc.version)
        folder, prefix = _output_layout(db, app)
        if app.output_directory:
            # Regeneration of an application exported by an older release must keep every version together.
            folder = Path(app.output_directory)
        raw_jd, url, app_id = job.raw_jd, job.job_url, app.id
        template = resolve_style(db, settings.resume_template)
        keep_word = settings.output_formats == "pdf_docx"

    # The Word documents are rendered in memory (milliseconds); they are only saved when asked for, or as a fallback.
    documents = [
        (DocumentType.RESUME, versions[0], _render_document(ctx, DocumentType.RESUME, versions[0], resume, template)),
        (
            DocumentType.COVER_LETTER,
            versions[1],
            _render_document(ctx, DocumentType.COVER_LETTER, versions[1], cover, template),
        ),
    ]
    if keep_word:
        _save_word_files(ctx, app_id, folder, prefix, documents)
    _ensure_file(ctx, app_id, DocumentType.JOB_DESCRIPTION, DocumentFormat.TXT, 1, folder, prefix, lambda: raw_jd.encode("utf-8"))
    if url:
        _ensure_file(ctx, app_id, DocumentType.JOB_URL, DocumentFormat.TXT, 1, folder, prefix, lambda: (url + "\n").encode("utf-8"))
    ctx.progress(30)

    pdf_status, pdf_error = _convert_pdfs(ctx, app_id, folder, prefix, documents)
    if pdf_status != "succeeded" and not keep_word:
        # No PDFs: save the Word files instead so the application is never left without documents.
        _save_word_files(ctx, app_id, folder, prefix, documents)
        if pdf_error:
            pdf_error = f"{pdf_error} The Word (.docx) files were saved instead."
    ctx.progress(85)

    with ctx.session() as db:
        app = get_application(db, app_id)
        app.output_directory = str(folder)
        app.exported_at = _now()
        app.pdf_status = pdf_status
        app.error = pdf_error
        db.commit()
    # Metadata first: once the application reads EXPORTED, every file is already on disk.
    _write_metadata(ctx, app_id, folder, prefix)
    with ctx.session() as db:
        app = get_application(db, app_id)
        _transition(app, PipelineStatus.EXPORTED, Actor.SYSTEM)
        db.commit()
    ctx.complete_stage(
        "document_export",
        message="Document export completed",
        started_at=export_started,
        details={
            "output_directory": str(folder),
            "pdf_status": pdf_status,
            "output_formats": "pdf_docx" if keep_word else "pdf",
        },
    )


def handle_convert_pdf(ctx: JobContext) -> None:
    with ctx.session() as db:
        app = get_application(db, ctx.application_id)  # type: ignore[arg-type]
        folder, prefix = _output_layout(db, app)
        if app.output_directory:
            folder = Path(app.output_directory)  # PDFs go next to the other files even if the job was renamed since
        template = resolve_style(db, load_settings(db).resume_template)
        documents: list[tuple[DocumentType, int, bytes]] = []
        for doc_type in (DocumentType.RESUME, DocumentType.COVER_LETTER):
            docx = latest_document(db, app.id, doc_type, DocumentFormat.DOCX)
            approved = latest_document(db, app.id, doc_type)
            if docx and docx.file_path and Path(docx.file_path).exists():
                documents.append((doc_type, docx.version, Path(docx.file_path).read_bytes()))
            elif approved is not None and approved.status == DocumentStatus.APPROVED:
                # PDF-only exports keep no Word file: render the approved version again.
                content = CONTENT_MODELS[doc_type].model_validate(approved.content_json)
                data = _render_document(ctx, doc_type, approved.version, content, template)
                documents.append((doc_type, approved.version, data))
        app_id = app.id
    if not documents:
        raise AppError(409, "nothing_to_convert", "No approved documents found for this application.")
    pdf_status, pdf_error = _convert_pdfs(ctx, app_id, folder, prefix, documents)
    with ctx.session() as db:
        app = get_application(db, app_id)
        app.pdf_status = pdf_status
        app.error = pdf_error
        db.commit()
    _write_metadata(ctx, app_id, folder, prefix)


def export_failed(ctx: JobContext, exc: Exception) -> None:
    with ctx.session() as db:
        app = db.get(Application, ctx.application_id)
        if app is not None:
            app.error = f"Export failed: {exc}"[:2000]
            db.commit()


def register_handlers(queue: ProcessingQueue, ai: AIService) -> None:
    queue.register(ProcessingJobType.GENERATE_DOCUMENTS, handle_generate(ai), generation_failed)
    queue.register(ProcessingJobType.EXPORT_APPLICATION, handle_export, export_failed)
    queue.register(ProcessingJobType.CONVERT_PDF, handle_convert_pdf, export_failed)


# ---------------------------------------------------------------- read models


def _doc_view(doc: Document | None) -> dict[str, Any] | None:
    if doc is None:
        return None
    content = dict(doc.content_json or {})
    meta = content.pop("_meta", {})
    issues = doc.warnings_json or []
    return {
        "id": doc.id,
        "version": doc.version,
        "status": doc.status,
        "origin": doc.origin,
        "mode": meta.get("mode"),
        "content": content,
        "issues": issues,
        "issue_counts": {s: sum(1 for i in issues if i.get("severity") == s) for s in ("error", "warning", "info")},
        "approved_at": doc.approved_at,
        "created_at": doc.created_at,
    }


def application_summary(app: Application, candidate_name: str, job: Job) -> dict[str, Any]:
    return {
        "id": app.id,
        "job_id": app.job_id,
        "candidate_id": app.candidate_id,
        "candidate_name": candidate_name,
        "company": job.company,
        "title": job.title,
        "job_url": job.job_url,
        "application_date": app.application_date,
        "sequence_number": app.sequence_number,
        "status": app.status,
        "selection_method": app.selection_method,
        "output_directory": app.output_directory,
        "pdf_status": app.pdf_status,
        "error": app.error,
        "created_at": app.created_at,
        "updated_at": app.updated_at,
        "exported_at": app.exported_at,
    }


def application_detail(db: Session, app: Application) -> dict[str, Any]:
    job = db.get(Job, app.job_id)
    candidate = db.get(Candidate, app.candidate_id)
    match = db.scalar(select(CandidateMatch).where(CandidateMatch.job_id == app.job_id, CandidateMatch.candidate_id == app.candidate_id))
    files = list(
        db.scalars(
            select(Document)
            .where(Document.application_id == app.id, Document.format != DocumentFormat.STRUCTURED)
            .order_by(Document.created_at)
        )
    )
    processing = list(
        db.scalars(select(ProcessingJob).where(ProcessingJob.application_id == app.id).order_by(ProcessingJob.created_at.desc()).limit(10))
    )
    runs = list(db.scalars(select(GenerationRun).where(GenerationRun.application_id == app.id)))
    versions = {
        t.value: [
            {"id": d.id, "version": d.version, "status": d.status, "origin": d.origin, "created_at": d.created_at}
            for d in db.scalars(
                select(Document)
                .where(Document.application_id == app.id, Document.document_type == t, Document.format == DocumentFormat.STRUCTURED)
                .order_by(Document.version.desc())
            )
        ]
        for t in (DocumentType.RESUME, DocumentType.COVER_LETTER)
    }
    return {
        **application_summary(app, candidate.full_name, job),
        "override_reason": app.override_reason,
        "approved_at": app.approved_at,
        "approved_by": app.approved_by,
        "instructions": app.instructions,
        "submitted_at": app.submitted_at,
        # v1.8: newer approved additions than this application's snapshot → "Regenerate with approved additions".
        "profile_revision": app.profile_revision,
        "current_profile_revision": candidate.profile_revision,
        "addition_ids": app.addition_ids_json or [],
        "coverage": app.coverage_json,
        "requirements": job.parsed_requirements_json,
        "match": {"score": match.score, "rank": match.rank, "eligibility_status": match.eligibility_status, "details": match.matching_details_json} if match else None,
        "resume": _doc_view(latest_document(db, app.id, DocumentType.RESUME)),
        "cover_letter": _doc_view(latest_document(db, app.id, DocumentType.COVER_LETTER)),
        "versions": versions,
        "files": [
            {"id": f.id, "type": f.document_type, "format": f.format, "version": f.version, "path": f.file_path, "exists": bool(f.file_path and Path(f.file_path).exists())}
            for f in files
        ],
        "processing": [
            {"id": p.id, "job_type": p.job_type, "status": p.status, "progress": p.progress, "error": p.error, "retry_count": p.retry_count, "created_at": p.created_at, "finished_at": p.finished_at}
            for p in processing
        ],
        "cost": {
            "runs": len(runs),
            "input_tokens": sum(r.input_tokens for r in runs),
            "output_tokens": sum(r.output_tokens for r in runs),
            "estimated_usd": round(sum((r.actual_cost_micros or r.estimated_cost_micros) for r in runs) / 1_000_000, 4),
        },
    }


def document_version(db: Session, app: Application, doc_type: DocumentType, version: int) -> dict[str, Any]:
    doc = db.scalar(
        select(Document).where(
            Document.application_id == app.id, Document.document_type == doc_type, Document.format == DocumentFormat.STRUCTURED, Document.version == version
        )
    )
    if doc is None:
        raise AppError(404, "document_not_found", "Document version not found")
    return _doc_view(doc)  # type: ignore[return-value]
