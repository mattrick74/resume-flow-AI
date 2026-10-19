from __future__ import annotations

from datetime import date, timedelta

from resumeflow.db.models import Application, Candidate, GenerationRun, Job, ProcessingJob
from resumeflow.domain.enums import (
    GenerationOperation,
    PipelineStatus,
    ProcessingJobType,
    RunStatus,
)


def test_empty_dashboard(client):
    body = client.get("/dashboard/summary").json()
    assert body["daily_goal"] == 100
    assert body["generated_today"] == 0
    assert body["awaiting_review"] == 0
    assert body["cost_today_usd"] == 0
    assert body["avg_generation_ms"] is None
    assert body["recent_activity"] == []


def test_dashboard_counts_today(client, db):
    candidate = Candidate(full_name="Ada Example")
    job = Job(job_url="https://jobs.example/1", normalized_url="jobs.example/1", raw_jd="JD", jd_hash="h1")
    db.add_all([candidate, job])
    db.flush()

    today, yesterday = date.today(), date.today() - timedelta(days=1)
    statuses = [
        (today, PipelineStatus.AWAITING_REVIEW),
        (today, PipelineStatus.EXPORTED),
        (today, PipelineStatus.FAILED),
        (today, PipelineStatus.GENERATING),
        (yesterday, PipelineStatus.AWAITING_REVIEW),
    ]
    for seq, (day, status) in enumerate(statuses, start=1):
        db.add(
            Application(
                job_id=job.id,
                candidate_id=candidate.id,
                application_date=day,
                sequence_number=seq,
                status=status,
            )
        )
    db.add_all(
        [
            GenerationRun(
                job_id=job.id,
                operation=GenerationOperation.RESUME_GENERATION,
                provider="openai",
                model="m",
                estimated_cost_micros=12_500,
                duration_ms=1000,
                status=RunStatus.SUCCEEDED,
            ),
            GenerationRun(
                job_id=job.id,
                operation=GenerationOperation.JD_ANALYSIS,
                provider="openai",
                model="m",
                estimated_cost_micros=99_999,
                actual_cost_micros=7_500,
                duration_ms=3000,
                status=RunStatus.SUCCEEDED,
            ),
            ProcessingJob(job_id=job.id, job_type=ProcessingJobType.ANALYZE_JOB, idempotency_key="k1"),
        ]
    )
    db.commit()

    body = client.get("/dashboard/summary").json()
    assert body["jobs_today"] == 1
    assert body["generated_today"] == 2  # awaiting review + exported
    assert body["exported_today"] == 1
    assert body["failed_today"] == 1
    assert body["awaiting_review"] == 2  # includes yesterday's backlog
    assert body["queue_depth"] == 1
    assert body["cost_today_usd"] == 0.02  # actual cost preferred over estimate
    assert body["avg_generation_ms"] == 2000
    assert len(body["recent_activity"]) == 5
    assert body["recent_activity"][0]["candidate_name"] == "Ada Example"
