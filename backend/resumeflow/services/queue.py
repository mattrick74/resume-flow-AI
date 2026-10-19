"""ProcessingQueue (PRD §12): persistent, SQLite-backed, restart-safe background work.

- Jobs are rows in ``processing_jobs``; claiming is an atomic conditional UPDATE.
- ``idempotency_key`` is unique, so enqueueing the same work twice returns the same job.
- Transient failures (rate limits, timeouts) retry with exponential backoff.
- On startup, jobs left RUNNING by a crash are re-queued.
- Worker count follows the ``worker_concurrency`` setting live (1–8).
"""

from __future__ import annotations

import logging
import random
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from resumeflow.db.models import ProcessingJob
from resumeflow.domain.enums import ProcessingJobType, ProcessingStatus
from resumeflow.errors import AppError

logger = logging.getLogger(__name__)
MAX_WORKERS = 8


class JobCancelled(Exception):
    pass


class RetryableError(Exception):
    transient = True


def _now() -> datetime:
    return datetime.now(UTC)


def backoff_seconds(retry_count: int) -> float:
    return min(300.0, 5.0 * (2 ** max(retry_count - 1, 0))) * random.uniform(0.8, 1.2)


@dataclass
class JobContext:
    queue: ProcessingQueue
    id: str
    job_type: ProcessingJobType
    application_id: str | None
    job_id: str | None
    payload: dict[str, Any] = field(default_factory=dict)

    def session(self) -> Session:
        return self.queue.session_factory()

    def progress(self, percent: int) -> None:
        with self.session() as db:
            db.execute(update(ProcessingJob).where(ProcessingJob.id == self.id).values(progress=max(0, min(100, percent))))
            db.commit()

    def check_cancelled(self) -> None:
        with self.session() as db:
            if db.scalar(select(ProcessingJob.cancel_requested).where(ProcessingJob.id == self.id)):
                raise JobCancelled()


Handler = Callable[[JobContext], None]
FailureHook = Callable[[JobContext, Exception], None]


class ProcessingQueue:
    def __init__(self, session_factory: sessionmaker[Session], concurrency: Callable[[], int]) -> None:
        self.session_factory = session_factory
        self._concurrency = concurrency
        self._handlers: dict[ProcessingJobType, Handler] = {}
        self._failure_hooks: dict[ProcessingJobType, FailureHook] = {}
        self._threads: list[threading.Thread] = []
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._paused = threading.Event()
        self._active = 0
        self._active_lock = threading.Lock()
        self._concurrency_cache = (0.0, 2)

    def pause(self, timeout: float = 120.0) -> None:
        """Stops claiming new work and waits for running work to finish (used by restore)."""
        self._paused.set()
        deadline = _now() + timedelta(seconds=timeout)
        while _now() < deadline:
            with self._active_lock:
                if self._active == 0:
                    return
            self._stop.wait(0.2)

    def resume(self) -> None:
        self._paused.clear()
        self._wake.set()

    # ------------------------------------------------------------ registration

    def register(self, job_type: ProcessingJobType, handler: Handler, on_failure: FailureHook | None = None) -> None:
        self._handlers[job_type] = handler
        if on_failure:
            self._failure_hooks[job_type] = on_failure

    # ------------------------------------------------------------ producer API

    def enqueue(
        self,
        db: Session,
        job_type: ProcessingJobType,
        *,
        idempotency_key: str,
        application_id: str | None = None,
        job_id: str | None = None,
        payload: dict[str, Any] | None = None,
        max_retries: int = 3,
    ) -> ProcessingJob:
        existing = db.scalar(select(ProcessingJob).where(ProcessingJob.idempotency_key == idempotency_key))
        if existing is not None:
            return existing
        job = ProcessingJob(
            job_type=job_type,
            idempotency_key=idempotency_key,
            application_id=application_id,
            job_id=job_id,
            payload_json=payload or {},
            max_retries=max_retries,
            run_after=_now(),
        )
        db.add(job)
        db.commit()
        self._wake.set()
        return job

    def retry(self, db: Session, processing_job_id: str) -> ProcessingJob:
        job = self.get(db, processing_job_id)
        if job.status not in (ProcessingStatus.FAILED, ProcessingStatus.CANCELLED):
            raise AppError(409, "not_retryable", "Only failed or cancelled work can be retried")
        job.status = ProcessingStatus.QUEUED
        job.retry_count = 0
        job.error = None
        job.cancel_requested = False
        job.progress = 0
        job.run_after = _now()
        job.finished_at = None
        db.commit()
        self._wake.set()
        return job

    def cancel(self, db: Session, processing_job_id: str) -> ProcessingJob:
        job = self.get(db, processing_job_id)
        if job.status == ProcessingStatus.QUEUED:
            job.status = ProcessingStatus.CANCELLED
            job.finished_at = _now()
        elif job.status == ProcessingStatus.RUNNING:
            job.cancel_requested = True
        else:
            raise AppError(409, "not_cancellable", "This work has already finished")
        db.commit()
        return job

    @staticmethod
    def get(db: Session, processing_job_id: str) -> ProcessingJob:
        job = db.get(ProcessingJob, processing_job_id)
        if job is None:
            raise AppError(404, "processing_job_not_found", "Processing job not found")
        return job

    # ------------------------------------------------------------ worker side

    def recover(self) -> int:
        """Re-queues work interrupted by a crash or shutdown."""
        with self.session_factory() as db:
            result = db.execute(
                update(ProcessingJob)
                .where(ProcessingJob.status == ProcessingStatus.RUNNING)
                .values(status=ProcessingStatus.QUEUED, locked_by=None, locked_at=None, run_after=_now(), error="Interrupted by restart; re-queued")
            )
            db.commit()
            if result.rowcount:
                logger.info("Re-queued %s interrupted processing job(s)", result.rowcount)
            return result.rowcount

    def claim(self, worker_id: str, *, ignore_delay: bool = False) -> str | None:
        with self.session_factory() as db:
            now = _now()
            stmt = select(ProcessingJob.id).where(ProcessingJob.status == ProcessingStatus.QUEUED)
            if not ignore_delay:
                stmt = stmt.where(ProcessingJob.run_after <= now)
            candidate = db.scalar(stmt.order_by(ProcessingJob.created_at).limit(1))
            if candidate is None:
                return None
            result = db.execute(
                update(ProcessingJob)
                .where(ProcessingJob.id == candidate, ProcessingJob.status == ProcessingStatus.QUEUED)
                .values(status=ProcessingStatus.RUNNING, locked_by=worker_id, locked_at=now, started_at=now, progress=0)
            )
            db.commit()
            return candidate if result.rowcount == 1 else None

    def run(self, processing_job_id: str) -> None:
        with self.session_factory() as db:
            row = self.get(db, processing_job_id)
            ctx = JobContext(self, row.id, row.job_type, row.application_id, row.job_id, dict(row.payload_json or {}))
        handler = self._handlers.get(ctx.job_type)
        try:
            if handler is None:
                raise RuntimeError(f"No handler registered for {ctx.job_type}")
            handler(ctx)
        except JobCancelled as exc:
            self._finish(ctx.id, ProcessingStatus.CANCELLED, "Cancelled by operator")
            hook = self._failure_hooks.get(ctx.job_type)
            if hook:
                try:
                    hook(ctx, exc)
                except Exception:
                    logger.exception("Failure hook for %s raised", ctx.job_type)
            return
        except Exception as exc:  # noqa: BLE001 - every failure is recorded on the job row
            self._handle_failure(ctx, exc)
            return
        self._finish(ctx.id, ProcessingStatus.SUCCEEDED, None)

    def _finish(self, job_id: str, status: ProcessingStatus, error: str | None) -> None:
        with self.session_factory() as db:
            db.execute(
                update(ProcessingJob)
                .where(ProcessingJob.id == job_id)
                .values(status=status, error=error, finished_at=_now(), progress=100 if status == ProcessingStatus.SUCCEEDED else ProcessingJob.progress, locked_by=None)
            )
            db.commit()

    def _handle_failure(self, ctx: JobContext, exc: Exception) -> None:
        transient = bool(getattr(exc, "transient", False))
        message = str(exc) or type(exc).__name__
        if not transient:
            logger.exception("Processing job %s (%s) failed", ctx.id, ctx.job_type)
        with self.session_factory() as db:
            row = self.get(db, ctx.id)
            row.retry_count += 1
            row.error = message[:2000]
            row.locked_by = None
            if transient and row.retry_count <= row.max_retries:
                row.status = ProcessingStatus.QUEUED
                row.run_after = _now() + timedelta(seconds=backoff_seconds(row.retry_count))
                db.commit()
                self._wake.set()
                return
            row.status = ProcessingStatus.FAILED
            row.finished_at = _now()
            db.commit()
        hook = self._failure_hooks.get(ctx.job_type)
        if hook:
            try:
                hook(ctx, exc)
            except Exception:
                logger.exception("Failure hook for %s raised", ctx.job_type)

    def drain(self, *, ignore_delay: bool = True, limit: int = 200) -> int:
        """Runs queued work synchronously (tests and diagnostics)."""
        processed = 0
        while processed < limit:
            job_id = self.claim("inline", ignore_delay=ignore_delay)
            if job_id is None:
                break
            self.run(job_id)
            processed += 1
        return processed

    def _current_concurrency(self) -> int:
        stamp, value = self._concurrency_cache
        now = _now().timestamp()
        if now - stamp > 5:
            try:
                value = max(1, min(MAX_WORKERS, int(self._concurrency())))
            except Exception:
                value = 2
            self._concurrency_cache = (now, value)
        return value

    def _worker(self, index: int) -> None:
        name = f"worker-{index}"
        while not self._stop.is_set():
            if self._paused.is_set() or index >= self._current_concurrency():
                self._stop.wait(1)
                continue
            with self._active_lock:
                self._active += 1
            job_id = None
            try:
                job_id = self.claim(name)
                if job_id is not None:
                    self.run(job_id)
            except Exception:
                logger.exception("Queue worker error")
                self._stop.wait(2)
            finally:
                with self._active_lock:
                    self._active -= 1
            if job_id is None:
                self._wake.wait(timeout=1.0)
                self._wake.clear()

    def start(self) -> None:
        if self._threads:
            return
        self.recover()
        self._stop.clear()
        for index in range(MAX_WORKERS):
            thread = threading.Thread(target=self._worker, args=(index,), name=f"queue-worker-{index}", daemon=True)
            thread.start()
            self._threads.append(thread)

    def stop(self, timeout: float = 15.0) -> None:
        self._stop.set()
        self._wake.set()
        for thread in self._threads:
            thread.join(timeout=timeout / max(len(self._threads), 1))
        self._threads.clear()
