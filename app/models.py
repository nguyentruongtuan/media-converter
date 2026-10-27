import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class VideoStatus(StrEnum):
    PENDING_UPLOAD = "pending_upload"
    READY = "ready"
    INVALID = "invalid"


class JobStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class OutputKind(StrEnum):
    MP4 = "mp4"
    HLS_MASTER = "hls_master"
    HLS_VARIANT = "hls_variant"
    THUMBNAIL = "thumbnail"


class Video(Base):
    __tablename__ = "videos"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    owner: Mapped[str] = mapped_column(String(64), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(127))
    declared_size_bytes: Mapped[int] = mapped_column(BigInteger)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    source_key: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(32), default=VideoStatus.PENDING_UPLOAD, index=True)

    # Display dimensions: rotation and sample aspect ratio already applied.
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    frame_rate: Mapped[float | None] = mapped_column(Float)
    video_codec: Mapped[str | None] = mapped_column(String(32))
    audio_codec: Mapped[str | None] = mapped_column(String(32))
    bitrate: Mapped[int | None] = mapped_column(BigInteger)
    has_audio: Mapped[bool] = mapped_column(Boolean, default=False)
    rotation: Mapped[int] = mapped_column(Integer, default=0)
    container: Mapped[str | None] = mapped_column(String(64))
    probe: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    jobs: Mapped[list["TranscodeJob"]] = relationship(
        back_populates="video", cascade="all, delete-orphan", passive_deletes=True
    )


class TranscodeJob(Base):
    __tablename__ = "transcode_jobs"
    __table_args__ = (UniqueConstraint("owner", "idempotency_key", name="uq_jobs_owner_idempotency_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    video_id: Mapped[str] = mapped_column(ForeignKey("videos.id", ondelete="CASCADE"), index=True)
    owner: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default=JobStatus.PENDING, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    request_hash: Mapped[str] = mapped_column(String(64))

    presets: Mapped[list] = mapped_column(JSON)
    formats: Mapped[list] = mapped_column(JSON)
    crf: Mapped[int] = mapped_column(Integer)
    x264_preset: Mapped[str] = mapped_column(String(16))

    progress: Mapped[float] = mapped_column(Float, default=0.0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    worker_id: Mapped[str | None] = mapped_column(String(255))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    video: Mapped[Video] = relationship(back_populates="jobs")
    outputs: Mapped[list["JobOutput"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", passive_deletes=True, order_by="JobOutput.id"
    )


class JobOutput(Base):
    __tablename__ = "job_outputs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("transcode_jobs.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    rendition: Mapped[str | None] = mapped_column(String(16))
    object_key: Mapped[str] = mapped_column(String(1024))
    content_type: Mapped[str] = mapped_column(String(127))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    bitrate: Mapped[int | None] = mapped_column(BigInteger)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)

    job: Mapped[TranscodeJob] = relationship(back_populates="outputs")
