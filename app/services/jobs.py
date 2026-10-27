"""Job state transitions. Every transition that races with other workers is a single conditional UPDATE."""

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import Settings
from app.errors import Conflict
from app.models import JobOutput, JobStatus, TranscodeJob, Video

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(UTC)


def request_hash(video_id: str, params: dict) -> str:
    return hashlib.sha256(json.dumps({"video_id": video_id, **params}, sort_keys=True).encode()).hexdigest()


def create_job(
    db: Session,
    video: Video,
    *,
    owner: str,
    presets: list[str],
    formats: list[str],
    crf: int,
    x264_preset: str,
    idempotency_key: str | None,
    settings: Settings,
) -> tuple[TranscodeJob, bool]:
    """Return (job, created). Replaying an idempotency key returns the original job."""
    params = {"presets": sorted(presets), "formats": sorted(formats), "crf": crf, "x264_preset": x264_preset}
    digest = request_hash(video.id, params)

    if idempotency_key:
        existing = db.scalar(
            select(TranscodeJob).where(
                TranscodeJob.owner == owner, TranscodeJob.idempotency_key == idempotency_key
            )
        )
        if existing:
            if existing.request_hash != digest:
                raise Conflict(
                    "Idempotency-Key was already used with a different request", code="idempotency_mismatch"
                )
            return existing, False

    job = TranscodeJob(
        video_id=video.id,
        owner=owner,
        status=JobStatus.PENDING,
        idempotency_key=idempotency_key,
        request_hash=digest,
        presets=params["presets"],
        formats=params["formats"],
        crf=crf,
        x264_preset=x264_preset,
        max_attempts=settings.job_max_attempts,
    )
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        # A concurrent request with the same key won the insert.
        db.rollback()
        existing = db.scalar(
            select(TranscodeJob).where(
                TranscodeJob.owner == owner, TranscodeJob.idempotency_key == idempotency_key
            )
        )
        if existing is None or existing.request_hash != digest:
            raise
        return existing, False
    return job, True


def claim_job(db: Session, job_id: str, worker_id: str, lease_seconds: int) -> bool:
    """Atomically move a job to PROCESSING. Fails if another live worker holds the lease."""
    now = utcnow()
    result = db.execute(
        update(TranscodeJob)
        .where(
            TranscodeJob.id == job_id,
            TranscodeJob.attempts < TranscodeJob.max_attempts,
            or_(
                TranscodeJob.status == JobStatus.PENDING,
                and_(TranscodeJob.status == JobStatus.PROCESSING, TranscodeJob.lease_expires_at < now),
            ),
        )
        .values(
            status=JobStatus.PROCESSING,
            worker_id=worker_id,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            attempts=TranscodeJob.attempts + 1,
            started_at=now,
            progress=0.0,
            error=None,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return result.rowcount == 1


def _owned(job_id: str, worker_id: str):
    return and_(
        TranscodeJob.id == job_id,
        TranscodeJob.worker_id == worker_id,
        TranscodeJob.status == JobStatus.PROCESSING,
    )


def renew_lease(db: Session, job_id: str, worker_id: str, lease_seconds: int, progress: float) -> bool:
    now = utcnow()
    result = db.execute(
        update(TranscodeJob)
        .where(_owned(job_id, worker_id))
        .values(
            lease_expires_at=now + timedelta(seconds=lease_seconds),
            progress=round(progress, 4),
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return result.rowcount == 1


def release_claim(db: Session, job_id: str, worker_id: str) -> None:
    """Hand a claimed job back without counting the attempt (e.g. throttled)."""
    db.execute(
        update(TranscodeJob)
        .where(_owned(job_id, worker_id))
        .values(
            status=JobStatus.PENDING,
            worker_id=None,
            lease_expires_at=None,
            attempts=TranscodeJob.attempts - 1,
            updated_at=utcnow(),
        )
        .execution_options(synchronize_session=False)
    )
    db.commit()


def complete_job(db: Session, job_id: str, worker_id: str, artifacts) -> bool:
    now = utcnow()
    result = db.execute(
        update(TranscodeJob)
        .where(_owned(job_id, worker_id))
        .values(
            status=JobStatus.COMPLETED,
            progress=1.0,
            lease_expires_at=None,
            finished_at=now,
            error=None,
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        db.rollback()
        return False
    db.query(JobOutput).filter(JobOutput.job_id == job_id).delete(synchronize_session=False)
    db.add_all(
        JobOutput(
            job_id=job_id,
            kind=a.kind,
            rendition=a.rendition,
            object_key=a.object_key,
            content_type=a.content_type,
            width=a.width,
            height=a.height,
            bitrate=a.bitrate,
            size_bytes=a.size_bytes,
        )
        for a in artifacts
    )
    db.commit()
    return True


def schedule_retry(db: Session, job_id: str, worker_id: str, error: str) -> bool:
    result = db.execute(
        update(TranscodeJob)
        .where(_owned(job_id, worker_id))
        .values(
            status=JobStatus.PENDING,
            worker_id=None,
            lease_expires_at=None,
            error=error[:4000],
            updated_at=utcnow(),
        )
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return result.rowcount == 1


def fail_job(db: Session, job_id: str, error: str, worker_id: str | None = None) -> bool:
    now = utcnow()
    condition = _owned(job_id, worker_id) if worker_id else TranscodeJob.id == job_id
    result = db.execute(
        update(TranscodeJob)
        .where(condition, TranscodeJob.status != JobStatus.COMPLETED)
        .values(
            status=JobStatus.FAILED,
            lease_expires_at=None,
            finished_at=now,
            error=error[:4000],
            updated_at=now,
        )
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return result.rowcount == 1


def reset_failed_job(db: Session, job: TranscodeJob) -> None:
    if job.status != JobStatus.FAILED:
        raise Conflict(f"Only failed jobs can be retried (job is {job.status})")
    job.status = JobStatus.PENDING
    job.attempts = 0
    job.progress = 0.0
    job.error = None
    job.worker_id = None
    job.finished_at = None
    db.commit()


def retry_delay(attempts: int, settings: Settings) -> int:
    return min(
        settings.job_retry_backoff_seconds * 2 ** max(0, attempts - 1), settings.job_retry_backoff_max_seconds
    )


def expired_jobs(db: Session, now: datetime | None = None) -> list[TranscodeJob]:
    now = now or utcnow()
    return list(
        db.scalars(
            select(TranscodeJob).where(
                TranscodeJob.status == JobStatus.PROCESSING, TranscodeJob.lease_expires_at < now
            )
        )
    )


def stale_pending_jobs(
    db: Session, older_than_seconds: int, now: datetime | None = None
) -> list[TranscodeJob]:
    cutoff = (now or utcnow()) - timedelta(seconds=older_than_seconds)
    return list(
        db.scalars(
            select(TranscodeJob).where(
                TranscodeJob.status == JobStatus.PENDING, TranscodeJob.updated_at < cutoff
            )
        )
    )
