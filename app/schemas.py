from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.services.ffmpeg import PRESET_ORDER

PresetName = Literal["1080p", "720p", "480p", "360p"]
OutputFormat = Literal["mp4", "hls"]
X264Preset = Literal["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower"]


class VideoCreate(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=127)
    size_bytes: int = Field(gt=0)

    @field_validator("filename")
    @classmethod
    def no_path(cls, v: str) -> str:
        if "/" in v or "\\" in v or v in (".", ".."):
            raise ValueError("filename must not contain path separators")
        return v


class UploadInstructions(BaseModel):
    method: str
    url: str
    fields: dict[str, str]
    put_url: str
    expires_at: datetime
    max_bytes: int


class VideoOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    owner: str
    filename: str
    content_type: str
    status: str
    size_bytes: int | None
    declared_size_bytes: int
    duration_seconds: float | None
    width: int | None
    height: int | None
    frame_rate: float | None
    video_codec: str | None
    audio_codec: str | None
    bitrate: int | None
    has_audio: bool
    rotation: int
    container: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime


class VideoCreated(BaseModel):
    video: VideoOut
    upload: UploadInstructions


class VideoDetail(VideoOut):
    source_url: str | None = None


class VideoList(BaseModel):
    items: list[VideoOut]
    total: int
    limit: int
    offset: int


class JobCreate(BaseModel):
    presets: list[PresetName] = Field(default_factory=lambda: list(PRESET_ORDER), min_length=1)
    formats: list[OutputFormat] = Field(default_factory=lambda: ["mp4", "hls"], min_length=1)
    crf: int | None = Field(default=None, ge=16, le=35, description="x264 CRF; lower is higher quality")
    x264_preset: X264Preset | None = None

    @field_validator("presets", "formats")
    @classmethod
    def dedupe(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(v))


class OutputOut(BaseModel):
    kind: str
    rendition: str | None
    object_key: str
    content_type: str
    width: int | None
    height: int | None
    bitrate: int | None
    size_bytes: int | None
    url: str


class JobOut(BaseModel):
    id: str
    video_id: str
    owner: str
    status: str
    progress: float
    presets: list[str]
    formats: list[str]
    crf: int
    x264_preset: str
    attempts: int
    max_attempts: int
    error: str | None
    worker_id: str | None
    idempotency_key: str | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime
    hls_url: str | None = None
    thumbnail_url: str | None = None
    outputs: list[OutputOut] = []


class JobList(BaseModel):
    items: list[JobOut]
    total: int
    limit: int
    offset: int
