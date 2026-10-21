from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from sqlalchemy import func, select

from resumeflow.api.deps import DbSession
from resumeflow.db.models import Application, ProcessingJob, ProcessingStageEvent
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


def _event_view(event: ProcessingStageEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "processing_job_id": event.processing_job_id,
        "application_id": event.application_id,
        "job_id": event.job_id,
        "stage": event.stage,
        "status": event.status,
        "message": event.message,
        "duration_ms": event.duration_ms,
        "details": event.details_json or {},
        "created_at": event.created_at,
    }


@router.get("/events")
def list_processing_events(db: DbSession, after_id: int | None = None, limit: int = 200) -> dict[str, Any]:
    """Cursor-polled stage completions.

    An omitted cursor establishes a baseline instead of replaying old desktop
    notifications.  Passing ``after_id=0`` explicitly reads retained history.
    """
    if after_id is None:
        latest = db.scalar(select(func.max(ProcessingStageEvent.id))) or 0
        return {"events": [], "next_after_id": latest}
    cursor = max(0, after_id)
    rows = list(
        db.scalars(
            select(ProcessingStageEvent)
            .where(ProcessingStageEvent.id > cursor)
            .order_by(ProcessingStageEvent.id)
            .limit(min(max(limit, 1), 500))
        )
    )
    return {
        "events": [_event_view(event) for event in rows],
        "next_after_id": rows[-1].id if rows else cursor,
    }


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
