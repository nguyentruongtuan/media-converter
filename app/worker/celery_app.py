from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery("transcoder", broker=settings.redis_url, include=["app.worker.tasks"])
celery_app.conf.update(
    task_default_queue=settings.transcode_queue,
    task_routes={
        "transcode.run": {"queue": settings.transcode_queue},
        "transcode.recover": {"queue": "maintenance"},
    },
    task_serializer="json",
    accept_content=["json"],
    task_ignore_result=True,
    # Ack only after the task finishes; a killed worker's message is redelivered.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    worker_cancel_long_running_tasks_on_connection_loss=True,
    broker_connection_retry_on_startup=True,
    # Must exceed the longest job, or Redis redelivers in-flight messages (harmless: claims are atomic).
    broker_transport_options={"visibility_timeout": settings.job_timeout_seconds + 3600},
    worker_send_task_events=True,
    task_send_sent_event=True,
    beat_schedule={
        "recover-stale-jobs": {
            "task": "transcode.recover",
            "schedule": float(settings.recovery_interval_seconds),
        },
    },
)
