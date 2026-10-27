import logging
import os
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from celery.signals import worker_ready, worker_shutting_down
from prometheus_client import start_http_server

from app.config import Settings, get_settings
from app.db import session_scope
from app.logging_config import configure_logging, job_id_var
from app.metrics import JOB_DURATION, JOB_SPEED, JOBS_ACTIVE, JOBS_FINISHED
from app.models import JobStatus, TranscodeJob, VideoStatus
from app.redis_client import get_redis
from app.services import dead_letter
from app.services import jobs as job_service
from app.services.concurrency import ConcurrencyLimiter
from app.services.transcoder import PermanentError, TranscodePipeline, TranscodeRequest
from app.storage import get_storage
from app.worker.celery_app import celery_app
from app.worker.dispatch import enqueue_transcode

logger = logging.getLogger(__name__)

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


class JobLost(Exception):
    """Another worker took over the job (our lease expired)."""


class LeaseKeeper:
    """Renews the job lease and limiter slot in the background, independent of FFmpeg progress output."""

    def __init__(self, job_id: str, worker_id: str, settings: Settings, limiter: ConcurrencyLimiter):
        self.job_id = job_id
        self.worker_id = worker_id
        self.settings = settings
        self.limiter = limiter
        self.progress = 0.0
        self.lost = threading.Event()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True, name=f"lease-{job_id}")

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=10)

    def _loop(self):
        while not self._stop.wait(self.settings.job_heartbeat_seconds):
            try:
                with session_scope() as db:
                    owned = job_service.renew_lease(
                        db, self.job_id, self.worker_id, self.settings.job_lease_seconds, self.progress
                    )
                self.limiter.refresh(self.job_id)
            except Exception:
                logger.warning("Lease renewal failed; will retry", exc_info=True)
                continue
            if not owned:
                logger.error("Lost ownership of job")
                self.lost.set()
                return

    def on_progress(self, fraction: float) -> None:
        if self.lost.is_set():
            raise JobLost(self.job_id)
        self.progress = max(self.progress, fraction)


@dataclass
class WorkerDeps:
    settings: Settings
    pipeline: TranscodePipeline
    limiter: ConcurrencyLimiter
    redis: object
    enqueue: Callable[..., bool] = enqueue_transcode
    worker_id: str = WORKER_ID


def default_deps() -> WorkerDeps:
    settings = get_settings()
    redis_client = get_redis()
    return WorkerDeps(
        settings=settings,
        pipeline=TranscodePipeline(get_storage(), settings),
        limiter=ConcurrencyLimiter(redis_client, settings.max_concurrent_jobs, settings.job_lease_seconds),
        redis=redis_client,
    )


def _build_request(job: TranscodeJob) -> TranscodeRequest:
    v = job.video
    return TranscodeRequest(
        job_id=job.id,
        video_id=v.id,
        source_key=v.source_key,
        width=v.width,
        height=v.height,
        duration=v.duration_seconds,
        frame_rate=v.frame_rate or 30.0,
        has_audio=v.has_audio,
        presets=list(job.presets),
        formats=list(job.formats),
        crf=job.crf,
        x264_preset=job.x264_preset,
    )


def _dead_letter(deps: WorkerDeps, job_id: str, error: str) -> None:
    with session_scope() as db:
        job = db.get(TranscodeJob, job_id)
        if job is None:
            return
        dead_letter.push(
            deps.redis,
            deps.settings,
            job_id=job.id,
            video_id=job.video_id,
            attempts=job.attempts,
            error=error,
        )


def process_job(job_id: str, deps: WorkerDeps) -> str:
    """Run one job end to end. Returns an outcome label; never raises for job-level failures."""
    s = deps.settings
    token = job_id_var.set(job_id)
    try:
        with session_scope() as db:
            job = db.get(TranscodeJob, job_id)
            if job is None:
                logger.warning("Job no longer exists")
                return "missing"
            if job.status in (JobStatus.COMPLETED, JobStatus.FAILED):
                return "skipped"

        if not deps.limiter.acquire(job_id):
            deps.enqueue(job_id, countdown=s.concurrency_retry_seconds)
            JOBS_FINISHED.labels("throttled").inc()
            return "throttled"

        try:
            return _run_claimed(job_id, deps)
        finally:
            deps.limiter.release(job_id)
    finally:
        job_id_var.reset(token)


def _run_claimed(job_id: str, deps: WorkerDeps) -> str:
    s = deps.settings
    with session_scope() as db:
        if not job_service.claim_job(db, job_id, deps.worker_id, s.job_lease_seconds):
            logger.info("Job already claimed or finished elsewhere")
            return "already_claimed"
        job = db.get(TranscodeJob, job_id)
        db.refresh(job)
        if job.video is None or job.video.status != VideoStatus.READY:
            job_service.fail_job(db, job_id, "Source video is not ready", deps.worker_id)
            return "failed"
        request = _build_request(job)
        attempt, max_attempts = job.attempts, job.max_attempts

    logger.info("Transcoding started", extra={"attempt": attempt, "presets": request.presets})
    started = time.monotonic()
    JOBS_ACTIVE.inc()
    try:
        with LeaseKeeper(job_id, deps.worker_id, s, deps.limiter) as lease:
            artifacts = deps.pipeline.run(request, lease.on_progress)
    except JobLost:
        JOBS_FINISHED.labels("lost").inc()
        return "lost"
    except PermanentError as exc:
        logger.error("Job failed permanently", extra={"error": str(exc)})
        with session_scope() as db:
            job_service.fail_job(db, job_id, str(exc), deps.worker_id)
        _dead_letter(deps, job_id, str(exc))
        JOBS_FINISHED.labels("failed").inc()
        return "failed"
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        if attempt < max_attempts:
            delay = job_service.retry_delay(attempt, s)
            logger.warning(
                "Job attempt failed; retrying", extra={"error": error, "attempt": attempt, "delay": delay}
            )
            with session_scope() as db:
                job_service.schedule_retry(db, job_id, deps.worker_id, error)
            deps.enqueue(job_id, countdown=delay)
            JOBS_FINISHED.labels("retried").inc()
            return "retry_scheduled"
        logger.error("Job failed after final attempt", extra={"error": error, "attempt": attempt})
        with session_scope() as db:
            job_service.fail_job(db, job_id, error, deps.worker_id)
        _dead_letter(deps, job_id, error)
        JOBS_FINISHED.labels("failed").inc()
        return "failed"
    finally:
        JOBS_ACTIVE.dec()

    with session_scope() as db:
        if not job_service.complete_job(db, job_id, deps.worker_id, artifacts):
            JOBS_FINISHED.labels("lost").inc()
            return "lost"
    elapsed = time.monotonic() - started
    JOB_DURATION.observe(elapsed)
    JOB_SPEED.observe(request.duration / elapsed if elapsed > 0 else 0)
    JOBS_FINISHED.labels("completed").inc()
    logger.info(
        "Transcoding completed", extra={"elapsed_seconds": round(elapsed, 1), "outputs": len(artifacts)}
    )
    return "completed"


def recover_stale_jobs(deps: WorkerDeps) -> dict[str, int]:
    """Re-queue jobs whose worker died (expired lease) and pending jobs whose message was lost."""
    s = deps.settings
    requeue, dead = [], []
    with session_scope() as db:
        for job in job_service.expired_jobs(db):
            if job.attempts >= job.max_attempts:
                if job_service.fail_job(db, job.id, job.error or "Worker lost during final attempt"):
                    dead.append(job.id)
            else:
                job.status, job.worker_id, job.lease_expires_at = JobStatus.PENDING, None, None
                job.error = job.error or "Worker lost; re-queued"
                db.commit()
                requeue.append(job.id)
        for job in job_service.stale_pending_jobs(db, s.stale_pending_seconds):
            job.updated_at = job_service.utcnow()
            db.commit()
            requeue.append(job.id)
    for job_id in requeue:
        deps.enqueue(job_id)
    for job_id in dead:
        _dead_letter(deps, job_id, "Worker lost during final attempt")
    if requeue or dead:
        logger.warning("Recovered stale jobs", extra={"requeued": len(requeue), "dead_lettered": len(dead)})
    return {"requeued": len(requeue), "failed": len(dead)}


_deps: WorkerDeps | None = None


def _get_deps() -> WorkerDeps:
    global _deps
    if _deps is None:
        _deps = default_deps()
    return _deps


@celery_app.task(name="transcode.run", acks_late=True, reject_on_worker_lost=True)
def transcode(job_id: str) -> str:
    return process_job(job_id, _get_deps())


@celery_app.task(name="transcode.recover")
def recover() -> dict[str, int]:
    return recover_stale_jobs(_get_deps())


def _touch_liveness(path: str, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            with open(path, "w") as fh:
                fh.write(str(time.time()))
        except OSError:
            logger.warning("Could not write liveness file", exc_info=True)
        stop.wait(15)


_liveness_stop = threading.Event()


@worker_ready.connect
def _on_worker_ready(**_):
    s = get_settings()
    configure_logging(s.log_level, s.log_json)
    os.makedirs(s.work_dir, exist_ok=True)
    try:
        start_http_server(s.worker_metrics_port)
    except OSError:
        logger.warning("Metrics port already in use", extra={"port": s.worker_metrics_port})
    threading.Thread(
        target=_touch_liveness, args=(s.worker_liveness_file, _liveness_stop), daemon=True
    ).start()
    logger.info("Worker ready", extra={"worker_id": WORKER_ID})


@worker_shutting_down.connect
def _on_worker_shutdown(sig=None, how=None, **_):
    # Warm shutdown lets the current job finish; if the pod is killed first, the message is
    # redelivered (acks_late) and the expired lease lets another worker claim the job.
    logger.info("Worker shutting down", extra={"signal": str(sig), "how": how})
