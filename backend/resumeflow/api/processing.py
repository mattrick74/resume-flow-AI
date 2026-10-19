from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import select

from resumeflow.api.deps import DbSession
from resumeflow.db.models import Application, ProcessingJob
from resumeflow.domain.enums import PipelineStatus, ProcessingJobType, ProcessingStatus

router = APIRouter(prefix="/processing-jobs", tags=["processing"])


def _view(job: ProcessingJob) -> dict[str, Any]:
    return {
        "id": job.id,
        "job_type": job.job_type,
        "status": job.status,
        "application_id": job.application_id,
        "job_id": job.job_id,
        "progress": job.progress,
        "retry_count": job.retry_count,
        "max_retries": job.max_retries,
        "error": job.error,
        "cancel_requested": job.cancel_requested,
        "run_after": job.run_after,
        "created_at": job.created_at,
        "started_at": job.started_at,
        "finished_at": job.finished_at,
    }


@router.get("")
def list_processing_jobs(db: DbSession, status: str | None = None, application_id: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    stmt = select(ProcessingJob)
    if status in {s.value for s in ProcessingStatus}:
        stmt = stmt.where(ProcessingJob.status == ProcessingStatus(status))
    if application_id:
        stmt = stmt.where(ProcessingJob.application_id == application_id)
    return [_view(j) for j in db.scalars(stmt.order_by(ProcessingJob.created_at.desc()).limit(min(max(limit, 1), 500)))]


@router.get("/{job_id}")
def get_processing_job(job_id: str, request: Request, db: DbSession) -> dict[str, Any]:
    return _view(request.app.state.queue.get(db, job_id))


@router.post("/{job_id}/retry")
def retry_processing_job(job_id: str, request: Request, db: DbSession) -> dict[str, Any]:
    job = request.app.state.queue.retry(db, job_id)
    # A retried generation puts its application back into GENERATING.
    if job.application_id and job.job_type == ProcessingJobType.GENERATE_DOCUMENTS:
        app = db.get(Application, job.application_id)
        if app is not None and app.status == PipelineStatus.FAILED:
            app.status = PipelineStatus.GENERATING
            app.error = None
            db.commit()
    return _view(job)


@router.post("/{job_id}/cancel")
def cancel_processing_job(job_id: str, request: Request, db: DbSession) -> dict[str, Any]:
    return _view(request.app.state.queue.cancel(db, job_id))
