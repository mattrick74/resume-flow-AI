"""Usage analytics (PRD §3, §12): throughput, cost by model/operation, latency, retries.

All figures come from recorded runs and records; nothing is projected or guaranteed.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from resumeflow.db.models import (
    Application,
    CandidateMatch,
    CandidateRoleExperience,
    CandidateSkillEvidence,
    Document,
    GenerationRun,
    Job,
    ProcessingJob,
)
from resumeflow.domain.enums import AdditionStatus, DocumentFormat, DocumentType, ProcessingStatus, RunStatus, SkillDecision
from resumeflow.domain.skills import normalize_skill
from resumeflow.services.ai.service import budget_status
from resumeflow.services.settings import load_settings


def _local_day(value: datetime) -> date:
    return value.astimezone().date()


def _percentile(values: list[int], pct: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round(pct * (len(ordered) - 1))))
    return ordered[index]


def experience_metrics(db: Session, start: datetime) -> dict[str, Any]:
    """v1.8 OPS-08: skill gaps found and answered, bullets approved, jobs re-ranked, coverage before/after, time to answer."""
    answers = list(db.scalars(select(CandidateSkillEvidence).where(CandidateSkillEvidence.decided_at >= start)))
    records = list(db.scalars(select(CandidateRoleExperience).where(CandidateRoleExperience.created_at >= start)))
    paused = list(db.scalars(select(Job).where(Job.confirmation_requested_at >= start)))
    resolved = [j for j in paused if j.confirmation_resolved_at is not None]
    waits = [(j.confirmation_resolved_at - j.confirmation_requested_at).total_seconds() / 60 for j in resolved]
    # Distinct skills asked about: answered in the period, plus those still waiting for an answer.
    waiting = {
        normalize_skill(s)
        for j in paused
        if j.confirmation_resolved_at is None
        for w in (j.warnings_json or [])
        if w.get("code") == "needs_confirmation"
        for s in w.get("skills") or []
    }
    gaps_identified = len(waiting | {a.normalized_name for a in answers})
    coverage = [a.coverage_json for a in db.scalars(select(Application).where(Application.created_at >= start, Application.coverage_json.is_not(None)))]
    before = [c["original_coverage_pct"] for c in coverage if c.get("original_coverage_pct") is not None]
    after = [c["keyword_coverage_pct"] for c in coverage if c.get("keyword_coverage_pct") is not None]
    reranked = db.scalar(select(func.count()).select_from(CandidateMatch).where(CandidateMatch.previous_score.is_not(None), CandidateMatch.created_at >= start)) or 0
    return {
        "gaps_identified": gaps_identified,
        "skills_confirmed": sum(1 for a in answers if a.decision == SkillDecision.USED),
        "skills_rejected": sum(1 for a in answers if a.decision == SkillDecision.NOT_USED),
        "skills_unsure": sum(1 for a in answers if a.decision == SkillDecision.UNSURE),
        "bullets_suggested": len(records),
        "bullets_approved": sum(1 for r in records if r.status == AdditionStatus.APPROVED),
        "jobs_paused": len(paused),
        "jobs_resumed": len(resolved),
        "matches_rescored": reranked,
        "avg_minutes_to_answer": round(sum(waits) / len(waits), 1) if waits else None,
        "coverage_before_pct": round(sum(before) / len(before)) if before else None,
        "coverage_after_pct": round(sum(after) / len(after)) if after else None,
    }


def analytics(db: Session, days: int = 30) -> dict[str, Any]:
    days = max(1, min(days, 365))
    today = date.today()
    start_day = today - timedelta(days=days - 1)
    start = datetime.combine(start_day, time.min).astimezone().astimezone(UTC)

    series: dict[date, dict[str, Any]] = {
        start_day + timedelta(days=i): {"applications": 0, "generated": 0, "exported": 0, "cost_usd": 0.0, "ai_runs": 0, "ai_failures": 0}
        for i in range(days)
    }
    for day, count in db.execute(
        select(Application.application_date, func.count()).where(Application.application_date >= start_day).group_by(Application.application_date)
    ):
        if day in series:
            series[day]["applications"] = count
    for (exported_at,) in db.execute(select(Application.exported_at).where(Application.exported_at >= start)):
        day = _local_day(exported_at)
        if day in series:
            series[day]["exported"] += 1
    for (created_at,) in db.execute(
        select(Document.created_at).where(
            Document.document_type == DocumentType.RESUME,
            Document.format == DocumentFormat.STRUCTURED,
            Document.version == 1,
            Document.created_at >= start,
        )
    ):
        day = _local_day(created_at)
        if day in series:
            series[day]["generated"] += 1

    by_model: dict[str, dict[str, Any]] = defaultdict(lambda: {"runs": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "failures": 0})
    by_operation: dict[str, dict[str, Any]] = defaultdict(lambda: {"runs": 0, "failures": 0, "durations": [], "cost_usd": 0.0})
    total_cost = 0.0
    for run in db.scalars(select(GenerationRun).where(GenerationRun.created_at >= start)):
        cost = (run.actual_cost_micros if run.actual_cost_micros is not None else run.estimated_cost_micros) / 1_000_000
        total_cost += cost
        failed = run.status != RunStatus.SUCCEEDED
        day = _local_day(run.created_at)
        if day in series:
            series[day]["cost_usd"] += cost
            series[day]["ai_runs"] += 1
            series[day]["ai_failures"] += int(failed)
        model = by_model[run.model]
        model["runs"] += 1
        model["input_tokens"] += run.input_tokens
        model["output_tokens"] += run.output_tokens
        model["cost_usd"] += cost
        model["failures"] += int(failed)
        op = by_operation[run.operation.value]
        op["runs"] += 1
        op["failures"] += int(failed)
        op["durations"].append(run.duration_ms)
        op["cost_usd"] += cost

    jobs = list(db.scalars(select(ProcessingJob).where(ProcessingJob.created_at >= start)))
    retried = sum(1 for j in jobs if j.retry_count > 0)
    exported_total = sum(d["exported"] for d in series.values())
    settings = load_settings(db)
    return {
        "experience": experience_metrics(db, start),
        "days": [{"date": day.isoformat(), **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in values.items()}} for day, values in series.items()],
        "totals": {
            "applications": sum(d["applications"] for d in series.values()),
            "generated": sum(d["generated"] for d in series.values()),
            "exported": exported_total,
            "cost_usd": round(total_cost, 4),
            "cost_per_export_usd": round(total_cost / exported_total, 4) if exported_total else None,
            "daily_goal": settings.daily_goal,
        },
        "by_model": [{"model": name, **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in values.items()}} for name, values in sorted(by_model.items())],
        "by_operation": [
            {
                "operation": name,
                "runs": values["runs"],
                "failures": values["failures"],
                "failure_rate": round(values["failures"] / values["runs"], 3) if values["runs"] else 0,
                "avg_duration_ms": round(sum(values["durations"]) / len(values["durations"])) if values["durations"] else None,
                "p95_duration_ms": _percentile(values["durations"], 0.95),
                "cost_usd": round(values["cost_usd"], 4),
            }
            for name, values in sorted(by_operation.items())
        ],
        "queue": {
            "jobs": len(jobs),
            "retried": retried,
            "retry_rate": round(retried / len(jobs), 3) if jobs else 0,
            "failed": sum(1 for j in jobs if j.status == ProcessingStatus.FAILED),
            "cancelled": sum(1 for j in jobs if j.status == ProcessingStatus.CANCELLED),
            "pending": sum(1 for j in jobs if j.status in (ProcessingStatus.QUEUED, ProcessingStatus.RUNNING)),
        },
        "budget": budget_status(db, settings),
    }
