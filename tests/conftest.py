import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

os.environ.update(
    AUTH_ENABLED="true",
    API_KEYS="alice:alice-key,bob:bob-key,ops:ops-key:admin",
    LOG_JSON="false",
    LOG_LEVEL="WARNING",
    DATABASE_URL="sqlite://",
    WORK_DIR=tempfile.mkdtemp(prefix="transcode-test-"),
    MAX_UPLOAD_BYTES=str(100 * 1024 * 1024),
    PUBLIC_MEDIA_BASE_URL="http://media.test/media",
)

import fakeredis  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api import deps  # noqa: E402
from app.config import get_settings  # noqa: E402
from app.db import init_engine, session_scope  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base, Video, VideoStatus  # noqa: E402
from app.services.ffprobe import ProbeResult  # noqa: E402
from app.storage import ObjectStat, PresignedUpload  # noqa: E402

ALICE = {"X-API-Key": "alice-key"}
BOB = {"X-API-Key": "bob-key"}
OPS = {"X-API-Key": "ops-key"}


class FakeStorage:
    def __init__(self):
        self.source_bucket = "videos-source"
        self.output_bucket = "videos-output"
        self.objects: dict[str, dict[str, bytes]] = {self.source_bucket: {}, self.output_bucket: {}}

    def put(self, bucket: str, key: str, data: bytes) -> None:
        self.objects[bucket][key] = data

    def presigned_upload(self, key, content_type, max_bytes):
        return PresignedUpload(
            method="POST",
            url=f"http://minio.test/{self.source_bucket}",
            fields={"key": key, "Content-Type": content_type, "policy": "x"},
            put_url=f"http://minio.test/{self.source_bucket}/{key}?sig=1",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )

    def presigned_download(self, bucket, key, *, public=True):
        return f"http://minio.test/{bucket}/{key}?sig=1"

    def stat(self, bucket, key):
        data = self.objects[bucket].get(key)
        return None if data is None else ObjectStat(size=len(data), content_type=None, etag="e")

    def download_file(self, bucket, key, path: Path):
        if key not in self.objects[bucket]:
            raise FileNotFoundError(key)
        Path(path).write_bytes(self.objects[bucket][key])

    def upload_file(self, bucket, key, path: Path):
        self.objects[bucket][key] = Path(path).read_bytes()

    def delete_object(self, bucket, key):
        self.objects[bucket].pop(key, None)

    def delete_prefix(self, bucket, prefix):
        keys = [k for k in self.objects[bucket] if k.startswith(prefix)]
        for k in keys:
            del self.objects[bucket][k]
        return len(keys)

    def ping(self):
        return None


def make_probe(**overrides) -> ProbeResult:
    values = dict(
        duration=12.5,
        width=1920,
        height=1080,
        frame_rate=29.97,
        video_codec="h264",
        audio_codec="aac",
        bitrate=8_000_000,
        rotation=0,
        container="mov,mp4,m4a,3gp,3g2,mj2",
        raw={"format": {}},
    )
    values.update(overrides)
    return ProbeResult(**values)


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture(autouse=True)
def db(tmp_path):
    engine = init_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def storage():
    return FakeStorage()


@pytest.fixture
def redis_client(monkeypatch):
    client = fakeredis.FakeRedis(decode_responses=True)
    import app.api.health
    import app.api.jobs

    monkeypatch.setattr(app.api.health, "get_redis", lambda: client)
    monkeypatch.setattr(app.api.jobs, "get_redis", lambda: client)
    return client


class ProbeStub:
    def __init__(self):
        self.result: ProbeResult | Exception = make_probe()
        self.calls: list[str] = []

    def __call__(self, url, settings):
        self.calls.append(url)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@pytest.fixture
def prober():
    return ProbeStub()


@pytest.fixture
def enqueued():
    return []


@pytest.fixture
def client(storage, prober, enqueued, redis_client):
    def enqueue(job_id, countdown=None):
        enqueued.append(job_id)
        return True

    app.dependency_overrides[deps.get_storage_dep] = lambda: storage
    app.dependency_overrides[deps.get_prober] = lambda: prober
    app.dependency_overrides[deps.get_enqueue] = lambda: enqueue
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def ready_video(storage):
    """A validated 1080p video owned by alice, with its source object in storage."""
    with session_scope() as session:
        video = Video(
            owner="alice",
            filename="clip.mp4",
            content_type="video/mp4",
            declared_size_bytes=4,
            size_bytes=4,
            source_key="",
            status=VideoStatus.READY,
            duration_seconds=12.5,
            width=1920,
            height=1080,
            frame_rate=30.0,
            video_codec="h264",
            audio_codec="aac",
            has_audio=True,
        )
        session.add(video)
        session.flush()
        video.source_key = f"{video.id}/source.mp4"
        video_id, key = video.id, video.source_key
    storage.put(storage.source_bucket, key, b"data")
    return video_id
