import json

from app.db import session_scope
from app.models import JobOutput, TranscodeJob
from tests.conftest import ALICE, BOB, OPS


def test_create_job_defaults(client, ready_video, enqueued):
    resp = client.post(f"/v1/videos/{ready_video}/jobs", headers=ALICE)
    assert resp.status_code == 202, resp.text
    job = resp.json()
    assert job["status"] == "pending"
    assert job["presets"] == ["1080p", "360p", "480p", "720p"]
    assert job["formats"] == ["hls", "mp4"]
    assert job["crf"] == 23
    assert enqueued == [job["id"]]


def test_create_job_custom_quality(client, ready_video):
    body = {"presets": ["720p", "360p", "720p"], "formats": ["hls"], "crf": 20, "x264_preset": "medium"}
    job = client.post(f"/v1/videos/{ready_video}/jobs", json=body, headers=ALICE).json()
    assert job["presets"] == ["360p", "720p"]
    assert (job["crf"], job["x264_preset"]) == (20, "medium")


def test_create_job_validation(client, ready_video):
    assert (
        client.post(f"/v1/videos/{ready_video}/jobs", json={"presets": ["4k"]}, headers=ALICE).status_code
        == 422
    )
    assert client.post(f"/v1/videos/{ready_video}/jobs", json={"crf": 60}, headers=ALICE).status_code == 422
    assert (
        client.post(f"/v1/videos/{ready_video}/jobs", json={"formats": []}, headers=ALICE).status_code == 422
    )


def test_create_job_requires_ready_video(client):
    video_id = client.post(
        "/v1/videos", json={"filename": "a.mp4", "content_type": "video/mp4", "size_bytes": 5}, headers=ALICE
    ).json()["video"]["id"]
    resp = client.post(f"/v1/videos/{video_id}/jobs", headers=ALICE)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "video_not_ready"


def test_idempotency_key_returns_same_job(client, ready_video, enqueued):
    headers = ALICE | {"Idempotency-Key": "req-1"}
    first = client.post(f"/v1/videos/{ready_video}/jobs", json={"presets": ["720p"]}, headers=headers)
    second = client.post(f"/v1/videos/{ready_video}/jobs", json={"presets": ["720p"]}, headers=headers)
    assert first.status_code == 202
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert len(enqueued) == 1

    conflict = client.post(f"/v1/videos/{ready_video}/jobs", json={"presets": ["480p"]}, headers=headers)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_mismatch"


def test_job_access_is_scoped(client, ready_video):
    job_id = client.post(f"/v1/videos/{ready_video}/jobs", headers=ALICE).json()["id"]
    assert client.get(f"/v1/jobs/{job_id}", headers=BOB).status_code == 404
    assert client.get(f"/v1/jobs/{job_id}", headers=OPS).status_code == 200
    assert client.post(f"/v1/videos/{ready_video}/jobs", headers=BOB).status_code == 404
    assert client.get("/v1/jobs", headers=BOB).json()["total"] == 0
    assert client.get(f"/v1/jobs?video_id={ready_video}", headers=ALICE).json()["total"] == 1


def test_job_detail_exposes_output_urls(client, ready_video):
    job_id = client.post(f"/v1/videos/{ready_video}/jobs", headers=ALICE).json()["id"]
    with session_scope() as db:
        job = db.get(TranscodeJob, job_id)
        job.status = "completed"
        prefix = f"{ready_video}/{job_id}/"
        db.add_all(
            [
                JobOutput(
                    job_id=job_id,
                    kind="hls_master",
                    object_key=prefix + "hls/master.m3u8",
                    content_type="application/vnd.apple.mpegurl",
                ),
                JobOutput(
                    job_id=job_id,
                    kind="thumbnail",
                    object_key=prefix + "thumbnail.jpg",
                    content_type="image/jpeg",
                ),
                JobOutput(
                    job_id=job_id,
                    kind="mp4",
                    rendition="720p",
                    object_key=prefix + "mp4/720p.mp4",
                    content_type="video/mp4",
                    width=1280,
                    height=720,
                ),
            ]
        )
    job = client.get(f"/v1/jobs/{job_id}", headers=ALICE).json()
    assert job["hls_url"] == f"http://media.test/media/{ready_video}/{job_id}/hls/master.m3u8"
    assert job["thumbnail_url"].endswith("thumbnail.jpg")
    assert {o["kind"] for o in job["outputs"]} == {"hls_master", "thumbnail", "mp4"}


def test_retry_failed_job(client, ready_video, enqueued):
    job_id = client.post(f"/v1/videos/{ready_video}/jobs", headers=ALICE).json()["id"]
    assert client.post(f"/v1/jobs/{job_id}/retry", headers=ALICE).status_code == 409

    with session_scope() as db:
        job = db.get(TranscodeJob, job_id)
        job.status, job.attempts, job.error = "failed", 3, "boom"
    resp = client.post(f"/v1/jobs/{job_id}/retry", headers=ALICE)
    assert resp.status_code == 202
    assert (resp.json()["status"], resp.json()["attempts"], resp.json()["error"]) == ("pending", 0, None)
    assert enqueued == [job_id, job_id]


def test_dead_letter_admin_only(client, redis_client):
    redis_client.lpush("transcode:dead_letter", json.dumps({"job_id": "j1", "error": "x"}))
    assert client.get("/v1/admin/dead-letter", headers=ALICE).status_code == 403
    resp = client.get("/v1/admin/dead-letter", headers=OPS)
    assert resp.status_code == 200
    assert resp.json()["items"][0]["job_id"] == "j1"
