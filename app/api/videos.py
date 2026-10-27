import logging
from collections.abc import Callable
from pathlib import PurePosixPath

from fastapi import APIRouter, Depends, Header, Query, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_enqueue, get_prober, get_storage_dep
from app.api.serializers import job_out
from app.auth import Principal, get_principal
from app.config import Settings, get_settings
from app.db import get_db
from app.errors import AppError, Conflict, InvalidMedia, NotFound
from app.metrics import UPLOADS_REJECTED
from app.models import JobStatus, Video, VideoStatus
from app.schemas import (
    JobCreate,
    JobOut,
    UploadInstructions,
    VideoCreate,
    VideoCreated,
    VideoDetail,
    VideoList,
    VideoOut,
)
from app.services import jobs as job_service
from app.services.ffprobe import ProbeError, validate_probe
from app.storage import StorageService

router = APIRouter(prefix="/v1/videos", tags=["videos"])
logger = logging.getLogger(__name__)

ALLOWED_CONTENT_TYPE_PREFIXES = ("video/",)
ALLOWED_CONTENT_TYPES = {"application/octet-stream", "application/x-matroska", "application/mp4"}


def load_video(db: Session, video_id: str, principal: Principal) -> Video:
    video = db.get(Video, video_id)
    if video is None or not principal.can_access(video.owner):
        raise NotFound("Video not found")
    return video


def _reject(reason: str, message: str, status_code: int = 422) -> AppError:
    UPLOADS_REJECTED.labels(reason).inc()
    return AppError(message, status_code=status_code, code=reason)


@router.post("", status_code=201, response_model=VideoCreated)
def create_video(
    body: VideoCreate,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
    storage: StorageService = Depends(get_storage_dep),
    settings: Settings = Depends(get_settings),
):
    ext = PurePosixPath(body.filename).suffix.lower()
    if ext not in settings.extension_allowlist:
        raise _reject("unsupported_extension", f"File extension '{ext or '(none)'}' is not allowed")
    content_type = body.content_type.lower()
    if not (content_type.startswith(ALLOWED_CONTENT_TYPE_PREFIXES) or content_type in ALLOWED_CONTENT_TYPES):
        raise _reject("unsupported_content_type", f"Content type '{body.content_type}' is not allowed")
    if body.size_bytes > settings.max_upload_bytes:
        raise _reject(
            "file_too_large", f"File exceeds the {settings.max_upload_bytes}-byte upload limit", 413
        )

    video = Video(
        owner=principal.name,
        filename=body.filename,
        content_type=body.content_type,
        declared_size_bytes=body.size_bytes,
        status=VideoStatus.PENDING_UPLOAD,
        source_key="",
    )
    db.add(video)
    db.flush()
    video.source_key = f"{video.id}/source{ext}"
    db.commit()

    upload = storage.presigned_upload(video.source_key, body.content_type, settings.max_upload_bytes)
    return VideoCreated(
        video=VideoOut.model_validate(video),
        upload=UploadInstructions(
            method=upload.method,
            url=upload.url,
            fields=upload.fields,
            put_url=upload.put_url,
            expires_at=upload.expires_at,
            max_bytes=settings.max_upload_bytes,
        ),
    )


@router.post("/{video_id}/complete", response_model=VideoOut)
def complete_upload(
    video_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
    storage: StorageService = Depends(get_storage_dep),
    prober: Callable = Depends(get_prober),
    settings: Settings = Depends(get_settings),
):
    video = load_video(db, video_id, principal)
    if video.status == VideoStatus.READY:
        return video
    if video.status == VideoStatus.INVALID:
        raise InvalidMedia(video.error or "Video failed validation")

    stat = storage.stat(storage.source_bucket, video.source_key)
    if stat is None:
        raise Conflict(
            "Upload not found in storage; upload the file before completing", code="upload_missing"
        )

    def invalidate(reason: str, message: str, status_code: int = 422):
        video.status = VideoStatus.INVALID
        video.error = message
        video.size_bytes = stat.size
        db.commit()
        storage.delete_object(storage.source_bucket, video.source_key)
        logger.info("Upload rejected", extra={"video_id": video.id, "reason": reason})
        return _reject(reason, message, status_code)

    if stat.size > settings.max_upload_bytes:
        raise invalidate("file_too_large", "Uploaded file exceeds the upload size limit", 413)
    if stat.size != video.declared_size_bytes:
        raise invalidate(
            "size_mismatch", f"Uploaded {stat.size} bytes but {video.declared_size_bytes} were declared"
        )

    # ffprobe reads only the headers it needs over HTTP instead of downloading the file.
    url = storage.presigned_download(storage.source_bucket, video.source_key, public=False)
    try:
        result = prober(url, settings)
        validate_probe(result, settings)
    except ProbeError as exc:
        raise invalidate("invalid_media", str(exc)) from exc

    video.status = VideoStatus.READY
    video.size_bytes = stat.size
    video.duration_seconds = result.duration
    video.width, video.height = result.width, result.height
    video.frame_rate = result.frame_rate
    video.video_codec, video.audio_codec = result.video_codec, result.audio_codec
    video.has_audio = result.has_audio
    video.bitrate = result.bitrate
    video.rotation = result.rotation
    video.container = result.container
    video.probe = result.raw
    video.error = None
    db.commit()
    return video


@router.get("", response_model=VideoList)
def list_videos(
    status: VideoStatus | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
):
    query = select(Video)
    if not principal.is_admin:
        query = query.where(Video.owner == principal.name)
    if status:
        query = query.where(Video.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    items = db.scalars(query.order_by(Video.created_at.desc()).limit(limit).offset(offset)).all()
    return VideoList(
        items=[VideoOut.model_validate(v) for v in items], total=total, limit=limit, offset=offset
    )


@router.get("/{video_id}", response_model=VideoDetail)
def get_video(
    video_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
    storage: StorageService = Depends(get_storage_dep),
):
    video = load_video(db, video_id, principal)
    detail = VideoDetail.model_validate(video)
    if video.status == VideoStatus.READY:
        detail.source_url = storage.presigned_download(storage.source_bucket, video.source_key)
    return detail


@router.delete("/{video_id}", status_code=204)
def delete_video(
    video_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
    storage: StorageService = Depends(get_storage_dep),
):
    video = load_video(db, video_id, principal)
    if any(j.status == JobStatus.PROCESSING for j in video.jobs):
        raise Conflict("Video has a job in progress; retry after it finishes", code="job_in_progress")
    # Pending jobs are removed with the row; a worker that later receives their message finds nothing.
    storage.delete_prefix(storage.output_bucket, f"{video.id}/")
    storage.delete_prefix(storage.source_bucket, f"{video.id}/")
    db.delete(video)
    db.commit()
    return Response(status_code=204)


@router.post("/{video_id}/jobs", status_code=202, response_model=JobOut)
def create_job(
    video_id: str,
    response: Response,
    body: JobCreate | None = None,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key", max_length=128),
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_principal),
    settings: Settings = Depends(get_settings),
    enqueue: Callable[..., bool] = Depends(get_enqueue),
):
    body = body or JobCreate()
    video = load_video(db, video_id, principal)
    if video.status != VideoStatus.READY:
        raise Conflict(f"Video is not ready for transcoding (status: {video.status})", code="video_not_ready")

    job, created = job_service.create_job(
        db,
        video,
        owner=principal.name,
        presets=body.presets,
        formats=body.formats,
        crf=body.crf or settings.default_crf,
        x264_preset=body.x264_preset or settings.x264_preset,
        idempotency_key=idempotency_key,
        settings=settings,
    )
    if created:
        enqueue(job.id)
    else:
        response.status_code = 200
    return job_out(job, settings)
