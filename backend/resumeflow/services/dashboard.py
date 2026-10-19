from __future__ import annotations

from datetime import UTC, date, datetime, time
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from resumeflow.db.models import Application, Candidate, GenerationRun, Job, ProcessingJob
from resumeflow.domain.enums import PipelineStatus as S
from resumeflow.domain.enums import ProcessingJobType, ProcessingStatus
from resumeflow.services.batch import waiting_clause
from resumeflow.services.candidates import readiness, skill_counts, usable_skill_count
from resumeflow.services.settings import load_settings

# An application counts toward the daily goal once documents reached review.
GENERATED_STATES = frozenset(
    {S.AWAITING_REVIEW, S.APPROVED, S.EXPORTED, S.SUBMITTED, S.INTERVIEW, S.REJECTED, S.OFFER}
)


def _local_midnight_utc(day: date) -> datetime:
    return datetime.combine(day, time.min).astimezone().astimezone(UTC)


def build_summary(db: Session, daily_goal: int, today: date | None = None) -> dict[str, Any]:
    today = today or date.today()
    day_start = _local_midnight_utc(today)

    status_counts = dict(
        db.execute(
            select(Application.status, func.count())
            .where(Application.application_date == today)
            .group_by(Application.status)
        ).all()
    )

    def count(states) -> int:
        return sum(status_counts.get(s, 0) for s in states)

    jobs_today = db.scalar(select(func.count()).select_from(Job).where(Job.created_at >= day_start)) or 0
    awaiting_review = (
        db.scalar(select(func.count()).select_from(Application).where(Application.status == S.AWAITING_REVIEW))
        or 0
    )
    cost_micros, avg_ms = db.execute(
        select(
            func.coalesce(func.sum(func.coalesce(GenerationRun.actual_cost_micros, GenerationRun.estimated_cost_micros)), 0),
            func.avg(GenerationRun.duration_ms),
        ).where(GenerationRun.created_at >= day_start)
    ).one()
    queue_depth = (
        db.scalar(
            select(func.count())
            .select_from(ProcessingJob)
            .where(ProcessingJob.status.in_([ProcessingStatus.QUEUED, ProcessingStatus.RUNNING]))
        )
        or 0
    )

    recent_rows = db.execute(
        select(Application, Candidate.full_name, Job.company, Job.title)
        .join(Candidate, Candidate.id == Application.candidate_id)
        .join(Job, Job.id == Application.job_id)
        .order_by(Application.updated_at.desc())
        .limit(8)
    ).all()

    # Failed whole-job runs are counted by job status instead: a retry queues a new run, so old rows stay "failed".
    processing_failed = (
        db.scalar(
            select(func.count())
            .select_from(ProcessingJob)
            .where(ProcessingJob.status == ProcessingStatus.FAILED, ProcessingJob.job_type != ProcessingJobType.PROCESS_JOB)
        )
        or 0
    )
    jobs_failed = db.scalar(select(func.count()).select_from(Job).where(Job.status == S.FAILED)) or 0
    # The same readiness rule matching uses (with verification off, no review step is needed).
    require = load_settings(db).require_verification
    stats = skill_counts(db)
    candidates_ready = sum(
        1
        for c in db.scalars(select(Candidate).where(Candidate.consent_confirmed_at.is_not(None)))
        if readiness(c, usable_skill_count(stats.get(c.id, {}), require), require_verification=require)["ready_for_matching"]
    )
    candidates_total = db.scalar(select(func.count()).select_from(Candidate)) or 0
    saved_jobs = db.scalar(select(func.count()).select_from(Job).where(waiting_clause())) or 0
    jobs_needing_confirmation = (
        db.scalar(select(func.count()).select_from(Job).where(Job.confirmation_requested_at.is_not(None), Job.confirmation_resolved_at.is_(None))) or 0
    )
    return {
        "date": today,
        "daily_goal": daily_goal,
        "processing_failed": processing_failed,
        "candidates_total": candidates_total,
        "candidates_ready": candidates_ready,
        "saved_jobs": saved_jobs,
        "jobs_needing_confirmation": jobs_needing_confirmation,
        "jobs_failed": jobs_failed,
        "jobs_today": jobs_today,
        "generated_today": count(GENERATED_STATES),
        "exported_today": count({S.EXPORTED, S.SUBMITTED, S.INTERVIEW, S.REJECTED, S.OFFER}),
        "failed_today": count({S.FAILED}),
        "awaiting_review": awaiting_review,
        "queue_depth": queue_depth,
        "cost_today_usd": round(cost_micros / 1_000_000, 4),
        "avg_generation_ms": round(avg_ms) if avg_ms is not None else None,
        "recent_activity": [
            {
                "application_id": app.id,
                "candidate_name": candidate_name,
                "company": company,
                "title": title,
                "status": app.status,
                "updated_at": app.updated_at,
            }
            for app, candidate_name, company, title in recent_rows
        ],
    }
