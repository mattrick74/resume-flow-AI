"""Batch job intake and one-click processing.

Saved jobs (status DRAFT) wait until the operator queues them. Each queued job
runs the whole pipeline in the background: analyze -> match -> create the
application for the top eligible candidate -> generate the tailored documents,
which then wait in the Review Queue for a person to approve.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from uuid import uuid4

from sqlalchemy import ColumnElement, and_, select
from sqlalchemy.orm import Session

from resumeflow.db.models import Application, Candidate, CandidateMatch, Job, ProcessingJob
from resumeflow.domain.enums import PipelineStatus, ProcessingJobType, ProcessingStatus
from resumeflow.errors import AppError
from resumeflow.services.ai.service import AIService
from resumeflow.services.applications import create_application, earlier_application_for_url
from resumeflow.services.experience import needs_confirmation, pause_for_confirmation, resolve_confirmation
from resumeflow.services.jobs import analyze_job, create_job, get_job, job_summary
from resumeflow.services.matching import run_matching
from resumeflow.services.queue import JobContext, ProcessingQueue
from resumeflow.services.settings import load_settings

MAX_BATCH = 100
_ACTIVE = (ProcessingStatus.QUEUED, ProcessingStatus.RUNNING)
_CLOSED_APPLICATION = (PipelineStatus.FAILED, PipelineStatus.REJECTED)


def save_jobs(db: Session, items: list[dict[str, Any]], *, confirm_duplicates: bool) -> list[dict[str, Any]]:
    """Saves each job without analyzing it. One bad item never blocks the others."""
    if len(items) > MAX_BATCH:
        raise AppError(422, "batch_too_large", f"Save at most {MAX_BATCH} jobs at a time.")
    results: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        try:
            job = create_job(db, item, confirm_duplicate=confirm_duplicates)
        except AppError as exc:
            db.rollback()
            results.append(
                {
                    "index": index,
                    "status": {"duplicate_job": "duplicate", "url_in_history": "skipped"}.get(exc.code, "invalid"),
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                }
            )
            continue
        results.append({"index": index, "status": "created", "job": job_summary(job)})
    return results


def active_processing(db: Session, job_id: str) -> ProcessingJob | None:
    return db.scalar(
        select(ProcessingJob)
        .where(ProcessingJob.job_id == job_id, ProcessingJob.job_type == ProcessingJobType.PROCESS_JOB, ProcessingJob.status.in_(_ACTIVE))
        .limit(1)
    )


def open_application(db: Session, job_id: str) -> Application | None:
    return db.scalar(select(Application).where(Application.job_id == job_id, Application.status.not_in(_CLOSED_APPLICATION)).limit(1))


def waiting_clause() -> ColumnElement[bool]:
    """Saved jobs nobody has queued yet."""
    queued = (
        select(ProcessingJob.id)
        .where(ProcessingJob.job_id == Job.id, ProcessingJob.job_type == ProcessingJobType.PROCESS_JOB, ProcessingJob.status.in_(_ACTIVE))
        .exists()
    )
    return and_(Job.status == PipelineStatus.DRAFT, ~queued)


def saved_job_ids(db: Session) -> list[str]:
    return list(db.scalars(select(Job.id).where(waiting_clause()).order_by(Job.created_at)))


def save_and_queue(db: Session, queue: ProcessingQueue, items: list[dict[str, Any]], *, confirm_duplicates: bool, process: bool) -> dict[str, Any]:
    results = save_jobs(db, items, confirm_duplicates=confirm_duplicates)
    created = [r["job"]["id"] for r in results if r["status"] == "created"]
    if not process:
        return {"results": results, "queued": [], "skipped": []}
    # A posting skipped because it was saved earlier but never generated is generated now, from that saved copy.
    earlier = {r["index"]: r["details"]["matches"][0]["job_id"] for r in results if r["status"] == "skipped" and r["details"].get("matches")}
    waiting = set(db.scalars(select(Job.id).where(Job.id.in_(list(earlier.values())), waiting_clause()))) - set(created) if earlier else set()
    queued = queue_jobs(db, queue, [*created, *(job_id for job_id in earlier.values() if job_id in waiting)])
    for result in results:
        job_id = earlier.get(result["index"])
        if job_id in waiting and job_id in queued["queued"]:
            result["queued_job_id"] = job_id
            result["message"] = "Already saved earlier but never generated, so that saved job is being generated now."
    return {"results": results, **queued}


def queue_jobs(db: Session, queue: ProcessingQueue, job_ids: list[str]) -> dict[str, Any]:
    queued: list[str] = []
    skipped: list[dict[str, str]] = []
    skip_known = load_settings(db).skip_known_urls
    for job_id in dict.fromkeys(job_ids):  # de-duplicate, keep order
        job = db.get(Job, job_id)
        if job is None:
            skipped.append({"job_id": job_id, "reason": "Job not found"})
        elif active_processing(db, job.id):
            skipped.append({"job_id": job_id, "reason": "Already queued"})
        elif open_application(db, job.id):
            skipped.append({"job_id": job_id, "reason": "Already has an application"})
        elif skip_known and earlier_application_for_url(db, job) is not None:
            skipped.append({"job_id": job_id, "reason": "Already generated for this job URL"})
        elif job.status == PipelineStatus.ANALYZING:
            skipped.append({"job_id": job_id, "reason": "Being analyzed right now"})
        else:
            queue.enqueue(db, ProcessingJobType.PROCESS_JOB, idempotency_key=f"process:{job.id}:{uuid4().hex[:12]}", job_id=job.id)
            queued.append(job.id)
    return {"queued": queued, "skipped": skipped}


def handle_process_job(ai: AIService) -> Callable[[JobContext], None]:
    def run(ctx: JobContext) -> None:
        with ctx.session() as db:
            job = get_job(db, ctx.job_id)  # type: ignore[arg-type]
            if open_application(db, job.id):
                return  # someone already created it; nothing to do
            if job.parsed_requirements_json is None or job.status in (PipelineStatus.DRAFT, PipelineStatus.FAILED):
                analyze_job(db, ai, job)
            ctx.progress(40)
            ctx.check_cancelled()
            run_matching(db, ai, job)
            ctx.progress(70)
            if job.status != PipelineStatus.MATCHED:
                return  # NO_ELIGIBLE_CANDIDATE is recorded on the job; nothing is fabricated
            top = db.scalar(select(CandidateMatch).where(CandidateMatch.job_id == job.id, CandidateMatch.rank == 1))
            if top is None:
                return
            if load_settings(db).gap_confirmation == "ask":
                # v1.8: skills the job asks for that the top candidate's profile doesn't show are asked about first.
                # The job waits (other jobs carry on); answering continues it automatically.
                report = needs_confirmation(db, job, db.get(Candidate, top.candidate_id))
                if report is not None:
                    pause_for_confirmation(job, report)
                    db.commit()
                    return
            resolve_confirmation(job)
            db.commit()
            try:
                create_application(db, ctx.queue, job_id=job.id, candidate_id=top.candidate_id, override_reason=None, generate=True)
            except AppError as exc:
                if exc.code == "url_already_generated":
                    # The same posting was generated before under another job entry: skip, and say so on the job.
                    job.warnings_json = [*(w for w in (job.warnings_json or []) if w.get("code") != exc.code), {"code": exc.code, "message": exc.message}]
                    db.commit()
                elif exc.code != "duplicate_application":
                    raise

    return run


def process_failed(ctx: JobContext, exc: Exception) -> None:
    with ctx.session() as db:
        job = db.get(Job, ctx.job_id)
        if job is None:
            return
        if exc.__class__.__name__ == "JobCancelled":
            if job.status == PipelineStatus.ANALYZING:
                job.status = PipelineStatus.DRAFT
        else:
            job.status = PipelineStatus.FAILED
            job.warnings_json = [
                *(w for w in (job.warnings_json or []) if w.get("code") != "processing_failed"),
                {"code": "processing_failed", "message": f"Automatic processing failed: {exc}"[:500]},
            ]
        db.commit()


def register_batch_handlers(queue: ProcessingQueue, ai: AIService) -> None:
    queue.register(ProcessingJobType.PROCESS_JOB, handle_process_job(ai), process_failed)
