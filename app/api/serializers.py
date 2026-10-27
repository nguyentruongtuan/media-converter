from app.config import Settings
from app.models import OutputKind, TranscodeJob
from app.schemas import JobOut, OutputOut


def media_url(settings: Settings, key: str) -> str:
    return f"{settings.public_media_base_url.rstrip('/')}/{key}"


def job_out(job: TranscodeJob, settings: Settings) -> JobOut:
    outputs = [
        OutputOut(
            kind=o.kind,
            rendition=o.rendition,
            object_key=o.object_key,
            content_type=o.content_type,
            width=o.width,
            height=o.height,
            bitrate=o.bitrate,
            size_bytes=o.size_bytes,
            url=media_url(settings, o.object_key),
        )
        for o in job.outputs
    ]
    first = {o.kind: o.url for o in reversed(outputs)}
    return JobOut(
        id=job.id,
        video_id=job.video_id,
        owner=job.owner,
        status=job.status,
        progress=job.progress,
        presets=job.presets,
        formats=job.formats,
        crf=job.crf,
        x264_preset=job.x264_preset,
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        error=job.error,
        worker_id=job.worker_id,
        idempotency_key=job.idempotency_key,
        started_at=job.started_at,
        finished_at=job.finished_at,
        created_at=job.created_at,
        updated_at=job.updated_at,
        hls_url=first.get(OutputKind.HLS_MASTER),
        thumbnail_url=first.get(OutputKind.THUMBNAIL),
        outputs=outputs,
    )
