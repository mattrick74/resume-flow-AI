from __future__ import annotations

from sqlalchemy import select

from resumeflow.db.models import ProcessingJob, ProcessingStageEvent
from resumeflow.domain.enums import ProcessingJobType, ProcessingStatus
from resumeflow.services.queue import ProcessingQueue, RetryableError


def _idle_events(factory) -> list[ProcessingStageEvent]:
    with factory() as db:
        return list(
            db.scalars(
                select(ProcessingStageEvent)
                .where(ProcessingStageEvent.stage == "queue_idle")
                .order_by(ProcessingStageEvent.id)
            )
        )


def test_queue_idle_event_is_recorded_only_after_the_whole_queue_drains(client):
    factory = client.app.state.session_factory
    queue = ProcessingQueue(factory, lambda: 1)
    queue.register(ProcessingJobType.PROCESS_JOB, lambda _ctx: None)
    with factory() as db:
        first = queue.enqueue(
            db,
            ProcessingJobType.PROCESS_JOB,
            idempotency_key="queue-idle:first",
        )
        second = queue.enqueue(
            db,
            ProcessingJobType.PROCESS_JOB,
            idempotency_key="queue-idle:second",
        )
        first_id, second_id = first.id, second.id

    assert queue.claim("test") == first_id
    queue.run(first_id)
    assert _idle_events(factory) == []
    feed = client.get("/processing-jobs/events", params={"after_id": 0}).json()
    assert feed["queue_depth"] == 1
    assert all(event["stage"] != "queue_idle" for event in feed["events"])

    assert queue.claim("test") == second_id
    queue.run(second_id)
    events = _idle_events(factory)
    assert len(events) == 1
    assert events[0].processing_job_id == second_id
    assert events[0].details_json == {
        "last_job_type": "process_job",
        "last_job_status": "succeeded",
    }
    feed = client.get("/processing-jobs/events", params={"after_id": 0}).json()
    assert feed["queue_depth"] == 0
    assert sum(event["stage"] == "queue_idle" for event in feed["events"]) == 1


def test_retry_keeps_queue_busy_until_the_final_attempt_finishes(client):
    factory = client.app.state.session_factory
    queue = ProcessingQueue(factory, lambda: 1)

    def fail(_ctx):
        raise RetryableError("temporary")

    queue.register(ProcessingJobType.CONVERT_PDF, fail)
    with factory() as db:
        job = queue.enqueue(
            db,
            ProcessingJobType.CONVERT_PDF,
            idempotency_key="queue-idle:retry",
            max_retries=1,
        )
        job_id = job.id

    assert queue.claim("test") == job_id
    queue.run(job_id)
    with factory() as db:
        row = db.get(ProcessingJob, job_id)
        assert row is not None
        assert row.status == ProcessingStatus.QUEUED
        assert row.retry_count == 1
    assert _idle_events(factory) == []
    assert client.get("/processing-jobs/events", params={"after_id": 0}).json()["queue_depth"] == 1

    assert queue.claim("test", ignore_delay=True) == job_id
    queue.run(job_id)
    with factory() as db:
        row = db.get(ProcessingJob, job_id)
        assert row is not None
        assert row.status == ProcessingStatus.FAILED
        assert row.retry_count == 2
    events = _idle_events(factory)
    assert len(events) == 1
    assert events[0].details_json["last_job_status"] == "failed"
    assert client.get("/processing-jobs/events", params={"after_id": 0}).json()["queue_depth"] == 0


def test_cancelled_running_retryable_work_is_not_requeued(client):
    factory = client.app.state.session_factory
    queue = ProcessingQueue(factory, lambda: 1)

    def fail(_ctx):
        raise RetryableError("request timed out after cancellation")

    queue.register(ProcessingJobType.GENERATE_DOCUMENTS, fail)
    with factory() as db:
        job = queue.enqueue(
            db,
            ProcessingJobType.GENERATE_DOCUMENTS,
            idempotency_key="queue-idle:cancelled-retry",
        )
        job_id = job.id

    assert queue.claim("test") == job_id
    with factory() as db:
        cancelled = queue.cancel(db, job_id)
        assert cancelled.cancel_requested is True
    queue.run(job_id)

    with factory() as db:
        row = db.get(ProcessingJob, job_id)
        assert row is not None
        assert row.status == ProcessingStatus.CANCELLED
        assert row.retry_count == 0
    events = _idle_events(factory)
    assert len(events) == 1
    assert events[0].details_json["last_job_status"] == "cancelled"
