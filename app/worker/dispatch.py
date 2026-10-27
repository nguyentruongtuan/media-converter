import logging

logger = logging.getLogger(__name__)


def enqueue_transcode(job_id: str, countdown: int | None = None) -> bool:
    """Publish a transcode message. A failure is not fatal: the recovery task re-enqueues pending jobs."""
    from app.worker.celery_app import celery_app

    try:
        celery_app.send_task("transcode.run", args=[job_id], countdown=countdown)
        return True
    except Exception:
        logger.exception("Failed to enqueue transcode job", extra={"enqueue_job_id": job_id})
        return False
