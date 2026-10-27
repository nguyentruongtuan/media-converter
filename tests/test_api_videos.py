from app.services.ffprobe import ProbeError
from tests.conftest import ALICE, BOB, OPS, make_probe


def create(client, headers=ALICE, **overrides):
    body = {"filename": "holiday.mp4", "content_type": "video/mp4", "size_bytes": 11} | overrides
    return client.post("/v1/videos", json=body, headers=headers)


def upload(storage, video_id, data=b"x" * 11):
    storage.put(storage.source_bucket, f"{video_id}/source.mp4", data)


def test_health_endpoints(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/health/live").status_code == 200
    ready = client.get("/health/ready")
    assert ready.status_code == 200
    assert ready.json()["checks"] == {"database": "ok", "redis": "ok", "storage": "ok"}


def test_metrics_endpoint(client):
    client.get("/health")
    body = client.get("/metrics/").text
    assert "http_requests_total" in body


def test_requires_api_key(client):
    assert client.get("/v1/videos").status_code == 401
    assert client.get("/v1/videos", headers={"X-API-Key": "nope"}).status_code == 401


def test_create_video_returns_presigned_upload(client):
    resp = create(client)
    assert resp.status_code == 201
    data = resp.json()
    assert data["video"]["status"] == "pending_upload"
    assert data["video"]["owner"] == "alice"
    assert data["upload"]["method"] == "POST"
    assert data["upload"]["fields"]["key"] == f"{data['video']['id']}/source.mp4"
    assert data["upload"]["put_url"]
    assert resp.headers["X-Request-ID"]


def test_create_video_validation(client):
    assert create(client, filename="notes.txt").json()["error"]["code"] == "unsupported_extension"
    assert create(client, content_type="text/plain").json()["error"]["code"] == "unsupported_content_type"
    too_big = create(client, size_bytes=10**12)
    assert too_big.status_code == 413
    assert create(client, filename="../evil.mp4").status_code == 422
    assert create(client, size_bytes=0).status_code == 422


def test_complete_upload_stores_metadata(client, storage, prober):
    video_id = create(client).json()["video"]["id"]
    upload(storage, video_id)
    prober.result = make_probe(width=1280, height=720, rotation=0)

    resp = client.post(f"/v1/videos/{video_id}/complete", headers=ALICE)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["status"] == "ready"
    assert (data["width"], data["height"]) == (1280, 720)
    assert data["size_bytes"] == 11
    assert data["has_audio"] is True
    assert prober.calls == [f"http://minio.test/videos-source/{video_id}/source.mp4?sig=1"]

    # Completing again is idempotent and does not re-probe.
    assert client.post(f"/v1/videos/{video_id}/complete", headers=ALICE).status_code == 200
    assert len(prober.calls) == 1


def test_complete_without_upload(client):
    video_id = create(client).json()["video"]["id"]
    resp = client.post(f"/v1/videos/{video_id}/complete", headers=ALICE)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "upload_missing"


def test_complete_rejects_size_mismatch(client, storage):
    video_id = create(client).json()["video"]["id"]
    upload(storage, video_id, b"short")
    resp = client.post(f"/v1/videos/{video_id}/complete", headers=ALICE)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "size_mismatch"
    assert storage.stat(storage.source_bucket, f"{video_id}/source.mp4") is None


def test_complete_rejects_corrupted_video(client, storage, prober):
    video_id = create(client).json()["video"]["id"]
    upload(storage, video_id)
    prober.result = ProbeError("Not a readable video file: moov atom not found")

    resp = client.post(f"/v1/videos/{video_id}/complete", headers=ALICE)
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_media"
    assert client.get(f"/v1/videos/{video_id}", headers=ALICE).json()["status"] == "invalid"
    assert storage.stat(storage.source_bucket, f"{video_id}/source.mp4") is None
    # A rejected upload stays rejected.
    assert client.post(f"/v1/videos/{video_id}/complete", headers=ALICE).status_code == 422


def test_complete_rejects_too_long_video(client, storage, prober, settings):
    video_id = create(client).json()["video"]["id"]
    upload(storage, video_id)
    prober.result = make_probe(duration=settings.max_video_duration_seconds + 1)
    resp = client.post(f"/v1/videos/{video_id}/complete", headers=ALICE)
    assert resp.status_code == 422
    assert "limit" in resp.json()["error"]["message"]


def test_list_and_detail_are_scoped_to_owner(client, storage):
    alice_id = create(client).json()["video"]["id"]
    create(client, headers=BOB)

    alice_list = client.get("/v1/videos", headers=ALICE).json()
    assert [v["id"] for v in alice_list["items"]] == [alice_id]
    assert client.get("/v1/videos", headers=OPS).json()["total"] == 2
    assert client.get(f"/v1/videos/{alice_id}", headers=BOB).status_code == 404
    assert client.get(f"/v1/videos/{alice_id}", headers=OPS).status_code == 200
    assert client.get("/v1/videos?status=ready", headers=ALICE).json()["total"] == 0


def test_detail_includes_source_url_when_ready(client, ready_video):
    data = client.get(f"/v1/videos/{ready_video}", headers=ALICE).json()
    assert data["source_url"].startswith("http://minio.test/videos-source/")


def test_delete_video_removes_objects(client, storage, ready_video):
    storage.put(storage.output_bucket, f"{ready_video}/job/hls/master.m3u8", b"#EXTM3U")
    resp = client.delete(f"/v1/videos/{ready_video}", headers=ALICE)
    assert resp.status_code == 204
    assert storage.objects[storage.source_bucket] == {}
    assert storage.objects[storage.output_bucket] == {}
    assert client.get(f"/v1/videos/{ready_video}", headers=ALICE).status_code == 404


def test_delete_blocked_while_processing(client, ready_video):
    from app.db import session_scope
    from app.models import TranscodeJob

    job_id = client.post(f"/v1/videos/{ready_video}/jobs", headers=ALICE).json()["id"]
    with session_scope() as db:
        db.get(TranscodeJob, job_id).status = "processing"
    resp = client.delete(f"/v1/videos/{ready_video}", headers=ALICE)
    assert resp.status_code == 409
