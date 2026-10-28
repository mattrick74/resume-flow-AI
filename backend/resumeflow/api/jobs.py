from __future__ import annotations

import logging
import time
from typing import Any, Literal

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select

from resumeflow.api.deps import AI, DbSession
from resumeflow.db.models import Application, Candidate, CandidateMatch, Job, ProcessingJob
from resumeflow.domain.enums import PipelineStatus, ProcessingJobType
from resumeflow.domain.requirements import JobRequirements
from resumeflow.errors import AppError
from resumeflow.services import batch, experience
from resumeflow.services import jobs as service
from resumeflow.services.matching import run_matching
from resumeflow.services.queue import record_stage_event

router = APIRouter(prefix="/jobs", tags=["jobs"])
logger = logging.getLogger(__name__)


class JobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_url: str = Field(default="", max_length=2000)
    raw_jd: str = Field(min_length=1, max_length=100_000)
    company: str | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, max_length=200)
    recruiter: str | None = Field(default=None, max_length=200)
    rate: str | None = Field(default=None, max_length=100)
    location: str | None = Field(default=None, max_length=200)
    employment_type: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=5000)
    confirm_duplicate: bool = False
    analyze: bool = True
    # v2.0 Chrome extension capture: where it came from, the job site, and the page title (a title fallback).
    source: Literal["manual", "extension"] = "manual"
    site: str | None = Field(default=None, max_length=100)
    page_title: str | None = Field(default=None, max_length=300)


class JobItem(BaseModel):
    """One job in a batch. Checked per item by the service, so one bad item doesn't reject the rest."""

    model_config = ConfigDict(extra="forbid")

    job_url: str = Field(default="", max_length=2000)
    raw_jd: str = Field(default="", max_length=100_000)
    company: str | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, max_length=200)
    recruiter: str | None = Field(default=None, max_length=200)
    rate: str | None = Field(default=None, max_length=100)
    location: str | None = Field(default=None, max_length=200)
    employment_type: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=5000)


class BatchCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[JobItem] = Field(min_length=1, max_length=batch.MAX_BATCH)
    confirm_duplicates: bool = False
    process: bool = False  # queue the saved jobs right away


class ProcessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_ids: list[str] = Field(default_factory=list, max_length=500)
    all_saved: bool = False


class JobUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    company: str | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, max_length=200)
    recruiter: str | None = Field(default=None, max_length=200)
    rate: str | None = Field(default=None, max_length=100)
    location: str | None = Field(default=None, max_length=200)
    employment_type: str | None = Field(default=None, max_length=100)
    notes: str | None = Field(default=None, max_length=5000)


def _analyze_and_match(db, ai, job: Job) -> None:
    started = time.perf_counter()
    service.analyze_job(db, ai, job)
    record_stage_event(
        db,
        job_id=job.id,
        stage="jd_analysis",
        message="Job description analysis completed",
        duration_ms=round((time.perf_counter() - started) * 1000),
        details={"source": job.requirements_source},
    )
    started = time.perf_counter()
    result = run_matching(db, ai, job)
    record_stage_event(
        db,
        job_id=job.id,
        stage="candidate_matching",
        message="Candidate matching completed",
        duration_ms=round((time.perf_counter() - started) * 1000),
        details=result,
    )


@router.get("")
def list_jobs(
    db: DbSession, status: str | None = None, q: str | None = None, needs_answers: bool = False, limit: int = 100, offset: int = 0
) -> dict[str, Any]:
    """One page of jobs, newest first: ``items`` plus ``total`` (all jobs matching the filters), ``offset`` and ``limit``.
    ``needs_answers``: only jobs waiting for answers about missing job skills."""
    stmt = select(Job)
    if status in {s.value for s in PipelineStatus}:
        stmt = stmt.where(Job.status == PipelineStatus(status))
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(Job.title.ilike(like) | Job.company.ilike(like) | Job.job_url.ilike(like))
    if needs_answers:
        stmt = stmt.where(Job.confirmation_requested_at.is_not(None), Job.confirmation_resolved_at.is_(None))
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    limit, offset = min(max(limit, 1), 500), max(offset, 0)
    jobs = list(db.scalars(stmt.order_by(Job.created_at.desc(), Job.id).offset(offset).limit(limit)))
    ids = [j.id for j in jobs]
    top = {
        job_id: name
        for job_id, name in db.execute(
            select(CandidateMatch.job_id, Candidate.full_name)
            .join(Candidate, Candidate.id == CandidateMatch.candidate_id)
            .where(CandidateMatch.job_id.in_(ids), CandidateMatch.rank == 1)
        )
    }
    eligible = dict(
        db.execute(
            select(CandidateMatch.job_id, func.count())
            .where(CandidateMatch.job_id.in_(ids), CandidateMatch.rank.is_not(None))
            .group_by(CandidateMatch.job_id)
        ).all()
    )
    applications = dict(
        db.execute(select(Application.job_id, func.count()).where(Application.job_id.in_(ids)).group_by(Application.job_id)).all()
    )
    processing = _latest_processing(db, ids)
    latest_app = {a.job_id: a for a in db.scalars(select(Application).where(Application.job_id.in_(ids)).order_by(Application.created_at))}
    items = [
        {
            **service.job_summary(j),
            "top_candidate": top.get(j.id),
            "eligible_count": eligible.get(j.id, 0),
            "applications": applications.get(j.id, 0),
            "processing": processing.get(j.id),
            "application": (
                {"id": latest_app[j.id].id, "status": latest_app[j.id].status, "candidate_id": latest_app[j.id].candidate_id}
                if j.id in latest_app
                else None
            ),
        }
        for j in jobs
    ]
    return {"items": items, "total": total, "offset": offset, "limit": limit}


def _latest_processing(db, ids: list[str]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    rows = db.scalars(
        select(ProcessingJob)
        .where(ProcessingJob.job_id.in_(ids), ProcessingJob.job_type == ProcessingJobType.PROCESS_JOB)
        .order_by(ProcessingJob.created_at)
    )
    for p in rows:
        latest[p.job_id] = {"id": p.id, "status": p.status, "progress": p.progress, "error": p.error}
    return latest


@router.post("", status_code=201)
def create_job(body: JobCreate, db: DbSession, ai: AI) -> dict[str, Any]:
    job = service.create_job(db, body.model_dump(exclude={"confirm_duplicate", "analyze"}), confirm_duplicate=body.confirm_duplicate)
    if body.analyze:
        _analyze_and_match(db, ai, job)
    # Earlier jobs with the same posting URL, and who they were generated for (saving again stays allowed).
    return {**service.job_detail(db, job), "same_url": service.same_url_history(db, job.normalized_url, exclude_job_id=job.id)}


@router.get("/by-url")
def jobs_by_url(url: str, db: DbSession) -> dict[str, Any]:
    """The jobs and applications already saved for a posting URL (the extension checks before saving)."""
    try:
        normalized = service.normalize_url(url)
    except AppError:
        return {"normalized_url": None, "same_url": []}
    return {"normalized_url": normalized, "same_url": service.same_url_history(db, normalized)}


@router.post("/batch", status_code=201)
def create_jobs_batch(body: BatchCreate, request: Request, db: DbSession) -> dict[str, Any]:
    """Saves several job descriptions at once; with ``process`` they are queued immediately."""
    items = [item.model_dump() for item in body.items]
    return batch.save_and_queue(db, request.app.state.queue, items, confirm_duplicates=body.confirm_duplicates, process=body.process)


@router.post("/process", status_code=202)
def process_jobs(body: ProcessRequest, request: Request, db: DbSession) -> dict[str, Any]:
    """One click: queue analysis, matching and document generation for the chosen (or all saved) jobs."""
    ids = list(body.job_ids)
    if body.all_saved:
        ids += batch.saved_job_ids(db)
    if not ids:
        raise AppError(422, "nothing_to_process", "Choose at least one job, or save some jobs first.")
    return batch.queue_jobs(db, request.app.state.queue, ids)


class BatchGapsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Empty: every job waiting for skill answers.
    job_ids: list[str] = Field(default_factory=list, max_length=500)


@router.post("/skill-gaps/batch")
def batch_skill_gaps(body: BatchGapsRequest, db: DbSession) -> dict[str, Any]:
    """v1.8 JOB-11/UX-04: missing skills across jobs, per top candidate, most frequent required skills first."""
    return experience.batch_gaps(db, body.job_ids or None)


@router.get("/{job_id}/skill-gaps")
def job_skill_gaps(job_id: str, db: DbSession, candidate_id: str | None = None) -> dict[str, Any]:
    """v1.8 JOB-09/10: every job skill classified for one candidate (default: the top-ranked one)."""
    job = service.get_job(db, job_id)
    if job.parsed_requirements_json is None:
        raise AppError(409, "not_analyzed", "Analyze this job first.")
    if candidate_id is None:
        top = experience.top_match(db, job)
        if top is None:
            raise AppError(409, "no_ranked_candidate", "No candidate is ranked for this job yet.")
        candidate_id = top.candidate_id
    candidate = db.get(Candidate, candidate_id)
    if candidate is None:
        raise AppError(404, "candidate_not_found", "Candidate not found")
    return experience.analyze_gaps(db, job, candidate)


@router.post("/{job_id}/rematch")
def rematch_job(job_id: str, db: DbSession, ai: AI) -> dict[str, Any]:
    """v1.8 MAT-09: ranks again with the current profiles; each match keeps its previous score."""
    job = service.get_job(db, job_id)
    if job.parsed_requirements_json is None:
        raise AppError(409, "not_analyzed", "Analyze this job first.")
    started = time.perf_counter()
    result = run_matching(db, ai, job)
    record_stage_event(
        db,
        job_id=job.id,
        stage="candidate_matching",
        message="Candidate matching completed",
        duration_ms=round((time.perf_counter() - started) * 1000),
        details=result,
    )
    return service.job_detail(db, job)


@router.get("/{job_id}")
def get_job(job_id: str, db: DbSession) -> dict[str, Any]:
    job = service.get_job(db, job_id)
    return {**service.job_detail(db, job), "processing": _latest_processing(db, [job.id]).get(job.id)}


@router.patch("/{job_id}")
def update_job(job_id: str, body: JobUpdate, db: DbSession) -> dict[str, Any]:
    job = service.get_job(db, job_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(job, key, value or None)
    db.commit()
    return service.job_detail(db, job)


@router.delete("/{job_id}", status_code=204)
def delete_job(job_id: str, db: DbSession) -> Response:
    job = service.get_job(db, job_id)
    if db.scalar(select(func.count()).select_from(Application).where(Application.job_id == job.id)):
        raise AppError(409, "job_has_applications", "This job has applications and can't be deleted.")
    db.delete(job)
    db.commit()
    return Response(status_code=204)


@router.post("/{job_id}/analyze")
def analyze_job(job_id: str, db: DbSession, ai: AI) -> dict[str, Any]:
    job = service.get_job(db, job_id)
    job.parsed_requirements_json = None
    started = time.perf_counter()

    # This endpoint intentionally measures only a fresh JD extraction. Candidate
    # matching is a separate step and must not be included in this duration.
    service.analyze_job(db, ai, job, use_cache=False)
    analysis_duration_ms = round((time.perf_counter() - started) * 1000)

    # Any previous ranks were calculated from the old requirements and are now
    # stale. Leave the job ready for an explicit /match request.
    db.execute(delete(CandidateMatch).where(CandidateMatch.job_id == job.id))
    job.status = PipelineStatus.DRAFT
    db.commit()

    record_stage_event(
        db,
        job_id=job.id,
        stage="jd_analysis",
        message="Job description analysis completed",
        duration_ms=analysis_duration_ms,
        details={"source": job.requirements_source},
    )

    logger.info(
        "JD analysis finished for job %s in %d ms (source=%s); matching stopped",
        job.id,
        analysis_duration_ms,
        job.requirements_source,
    )
    return {**service.job_detail(db, job), "analysis_duration_ms": analysis_duration_ms}


@router.put("/{job_id}/requirements")
def update_requirements(job_id: str, body: JobRequirements, db: DbSession, ai: AI) -> dict[str, Any]:
    job = service.get_job(db, job_id)
    service.update_requirements(db, job, body)
    started = time.perf_counter()
    result = run_matching(db, ai, job)
    record_stage_event(
        db,
        job_id=job.id,
        stage="candidate_matching",
        message="Candidate matching completed",
        duration_ms=round((time.perf_counter() - started) * 1000),
        details=result,
    )
    return service.job_detail(db, job)


@router.post("/{job_id}/match")
def match_job(job_id: str, db: DbSession, ai: AI) -> dict[str, Any]:
    job = service.get_job(db, job_id)
    if job.parsed_requirements_json is None:
        started = time.perf_counter()
        service.analyze_job(db, ai, job)
        record_stage_event(
            db,
            job_id=job.id,
            stage="jd_analysis",
            message="Job description analysis completed",
            duration_ms=round((time.perf_counter() - started) * 1000),
            details={"source": job.requirements_source},
        )
    started = time.perf_counter()
    result = run_matching(db, ai, job)
    record_stage_event(
        db,
        job_id=job.id,
        stage="candidate_matching",
        message="Candidate matching completed",
        duration_ms=round((time.perf_counter() - started) * 1000),
        details=result,
    )
    return service.job_detail(db, job)
