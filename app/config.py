from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "media-converter"
    environment: str = "development"
    log_level: str = "INFO"
    log_json: bool = True

    database_url: str = "postgresql+psycopg://transcoder:transcoder@localhost:5432/transcoder"
    redis_url: str = "redis://localhost:6379/0"

    # MinIO. `minio_public_endpoint` is the host clients use for presigned URLs
    # (it is part of the signature, so it must match what the client connects to).
    minio_endpoint: str = "localhost:9000"
    minio_secure: bool = False
    minio_public_endpoint: str | None = None
    minio_public_secure: bool | None = None
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_region: str = "us-east-1"
    source_bucket: str = "videos-source"
    output_bucket: str = "videos-output"
    output_bucket_public_read: bool = True
    presigned_upload_expiry_seconds: int = 3600
    presigned_download_expiry_seconds: int = 3600
    public_media_base_url: str = "http://localhost:8080/media"

    # Auth: comma-separated "name:key" or "name:key:admin" entries.
    auth_enabled: bool = True
    api_keys: str = ""

    # Upload validation
    max_upload_bytes: int = 10 * 1024**3
    allowed_extensions: str = ".mp4,.mov,.m4v,.mkv,.webm,.avi,.mpg,.mpeg,.ts,.flv,.wmv,.3gp"
    max_video_duration_seconds: float = 4 * 3600
    max_source_width: int = 7680
    max_source_height: int = 4320

    # FFmpeg
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"
    ffprobe_timeout_seconds: int = 60
    x264_preset: str = "veryfast"
    default_crf: int = 23
    keyframe_interval_seconds: int = 2
    hls_segment_seconds: int = 6
    hls_segment_type: str = "mpegts"
    max_fps: int = 60
    thumbnail_width: int = 640

    # Worker / jobs
    work_dir: str = "/tmp/transcode"
    disk_space_factor: float = 4.0
    job_max_attempts: int = 3
    job_retry_backoff_seconds: int = 30
    job_retry_backoff_max_seconds: int = 600
    job_lease_seconds: int = 300
    job_heartbeat_seconds: int = 30
    job_timeout_seconds: int = 4 * 3600
    max_concurrent_jobs: int = 0  # cluster-wide; 0 = unlimited (bounded by worker replicas)
    concurrency_retry_seconds: int = 30
    transcode_queue: str = "transcode"
    dead_letter_key: str = "transcode:dead_letter"
    dead_letter_max_length: int = 10000
    recovery_interval_seconds: int = 60
    stale_pending_seconds: int = 900
    worker_metrics_port: int = 9101
    worker_liveness_file: str = "/tmp/worker-alive"

    @property
    def extension_allowlist(self) -> set[str]:
        return {e.strip().lower() for e in self.allowed_extensions.split(",") if e.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
