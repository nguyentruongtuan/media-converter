"""API load test.

    pip install '.[load]'
    READY_VIDEO_ID=<id of a validated video> locust -f tests/load/locustfile.py \
        --host http://localhost:8000 --users 200 --spawn-rate 20 --run-time 10m --headless

`ApiUser` exercises metadata endpoints. `TranscodeSubmitter` (enabled only with READY_VIDEO_ID)
queues real transcode jobs to drive KEDA scaling; watch queue depth and worker pods in Grafana.
"""

import os
import random
import uuid

from locust import HttpUser, between, task

API_KEY = os.environ.get("API_KEY", "dev-key")
READY_VIDEO_ID = os.environ.get("READY_VIDEO_ID")
HEADERS = {"X-API-Key": API_KEY}


class ApiUser(HttpUser):
    weight = 10
    wait_time = between(0.5, 2)

    def on_start(self):
        self.video_ids: list[str] = []

    @task(3)
    def create_video(self):
        body = {
            "filename": f"load-{uuid.uuid4().hex[:8]}.mp4",
            "content_type": "video/mp4",
            "size_bytes": 1024,
        }
        with self.client.post("/v1/videos", json=body, headers=HEADERS, catch_response=True) as resp:
            if resp.status_code == 201:
                self.video_ids.append(resp.json()["video"]["id"])
            else:
                resp.failure(f"status {resp.status_code}")

    @task(5)
    def list_videos(self):
        self.client.get("/v1/videos?limit=20", headers=HEADERS)

    @task(5)
    def get_video(self):
        if self.video_ids:
            self.client.get(
                f"/v1/videos/{random.choice(self.video_ids)}", headers=HEADERS, name="/v1/videos/{id}"
            )

    @task(3)
    def list_jobs(self):
        self.client.get("/v1/jobs?limit=20", headers=HEADERS)

    @task(1)
    def health(self):
        self.client.get("/health/ready")


class TranscodeSubmitter(HttpUser):
    weight = 1 if READY_VIDEO_ID else 0
    wait_time = between(5, 15)

    @task
    def submit(self):
        body = {
            "presets": random.sample(["1080p", "720p", "480p", "360p"], k=2),
            "crf": random.randint(20, 28),
        }
        self.client.post(
            f"/v1/videos/{READY_VIDEO_ID}/jobs", json=body, headers=HEADERS, name="/v1/videos/{id}/jobs"
        )
