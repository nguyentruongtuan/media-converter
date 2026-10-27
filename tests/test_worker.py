import json
import threading
from datetime import timedelta

import fakeredis
import pytest

from app.db import session_scope
from app.models import JobOutput, JobStatus, TranscodeJob
from app.services import jobs as job_service
from app.services.concurrency import ConcurrencyLimiter
from app.services.transcoder import OutputArtifact, PermanentError
from app.worker.tasks import WorkerDeps, process_job, recover_stale_jobs


class FakePipeline:
    def __init__(self, behaviour=None):
        self.behaviour = behaviour or (lambda req, progress: None)
        self.calls = 0

    def run(self, req, on_progress):
        self.calls += 1
        on_progress(0.5)
        self.behaviour(req, on_progress)
        return [
            OutputArtifact(
                "hls_master", f"{req.video_id}/{req.job_id}/hls/master.m3u8", "application/vnd.apple.mpegurl"
            )
        ]


def raise_(exc):
    def behaviour(req, progress):
        raise exc

    return behaviour


@pytest.fixture
def make_deps(settings):
    def factory(pipeline=None, limit=0, worker_id="worker-1"):
        redis_client = fakeredis.FakeRedis(decode_responses=True)
        calls = []
        return WorkerDeps(
            settings=settings,
            pipeline=pipeline or FakePipeline(),
            limiter=ConcurrencyLimiter(redis_client, limit, settings.job_lease_seconds),
            redis=redis_client,
            enqueue=lambda job_id, countdown=None: calls.append((job_id, countdown)) or True,
            worker_id=worker_id,
        ), calls

    return factory


@pytest.fixture
def job_id(client, ready_video):
    from tests.conftest import ALICE

    return client.post(f"/v1/videos/{ready_video}/jobs", headers=ALICE).json()["id"]


def load(job_id):
    with session_scope() as db:
        job = db.get(TranscodeJob, job_id)
        db.expunge(job)
        return job


def test_successful_job(make_deps, job_id):
    deps, _ = make_deps()
    assert process_job(job_id, deps) == "completed"
    job = load(job_id)
    assert (job.status, job.progress, job.attempts) == (JobStatus.COMPLETED, 1.0, 1)
    assert job.finished_at is not None and job.lease_expires_at is None
    with session_scope() as db:
        assert db.query(JobOutput).filter_by(job_id=job_id).count() == 1


def test_duplicate_delivery_is_skipped(make_deps, job_id):
    pipeline = FakePipeline()
    deps, _ = make_deps(pipeline)
    process_job(job_id, deps)
    assert process_job(job_id, deps) == "skipped"
    assert pipeline.calls == 1


def test_concurrent_claim_only_one_wins(make_deps, job_id, settings):
    with session_scope() as db:
        assert job_service.claim_job(db, job_id, "a", settings.job_lease_seconds)
        assert not job_service.claim_job(db, job_id, "b", settings.job_lease_seconds)

    deps, _ = make_deps(worker_id="b")
    assert process_job(job_id, deps) == "already_claimed"


def test_expired_lease_can_be_reclaimed(job_id, settings):
    with session_scope() as db:
        assert job_service.claim_job(db, job_id, "a", settings.job_lease_seconds)
        job = db.get(TranscodeJob, job_id)
        job.lease_expires_at = job_service.utcnow() - timedelta(seconds=1)
    with session_scope() as db:
        assert job_service.claim_job(db, job_id, "b", settings.job_lease_seconds)
    assert load(job_id).attempts == 2


def test_transient_failure_schedules_retry_with_backoff(make_deps, job_id):
    deps, calls = make_deps(FakePipeline(raise_(OSError("connection reset"))))
    assert process_job(job_id, deps) == "retry_scheduled"
    job = load(job_id)
    assert job.status == JobStatus.PENDING
    assert "connection reset" in job.error
    assert calls == [(job_id, 30)]

    assert process_job(job_id, deps) == "retry_scheduled"
    assert calls[-1] == (job_id, 60)


def test_final_attempt_failure_dead_letters(make_deps, job_id, settings):
    deps, calls = make_deps(FakePipeline(raise_(RuntimeError("ffmpeg crashed"))))
    outcomes = [process_job(job_id, deps) for _ in range(settings.job_max_attempts)]
    assert outcomes == ["retry_scheduled", "retry_scheduled", "failed"]
    job = load(job_id)
    assert (job.status, job.attempts) == (JobStatus.FAILED, 3)
    entry = json.loads(deps.redis.lindex(settings.dead_letter_key, 0))
    assert entry["job_id"] == job_id and "ffmpeg crashed" in entry["error"]
    assert process_job(job_id, deps) == "skipped"


def test_permanent_failure_is_not_retried(make_deps, job_id, settings):
    deps, calls = make_deps(FakePipeline(raise_(PermanentError("source missing"))))
    assert process_job(job_id, deps) == "failed"
    assert load(job_id).status == JobStatus.FAILED
    assert calls == []
    assert deps.redis.llen(settings.dead_letter_key) == 1


def test_concurrency_limit_throttles(make_deps, job_id, settings):
    deps, calls = make_deps(limit=1)
    deps.limiter.acquire("some-other-job")
    assert process_job(job_id, deps) == "throttled"
    assert load(job_id).status == JobStatus.PENDING
    assert load(job_id).attempts == 0
    assert calls == [(job_id, settings.concurrency_retry_seconds)]

    deps.limiter.release("some-other-job")
    assert process_job(job_id, deps) == "completed"
    assert deps.redis.zcard("transcode:slots") == 0


def test_lost_lease_abandons_job(make_deps, job_id, settings, monkeypatch):
    monkeypatch.setattr(settings, "job_heartbeat_seconds", 0.05)
    started = threading.Event()

    def steal_then_continue(req, on_progress):
        with session_scope() as db:
            db.get(TranscodeJob, job_id).worker_id = "thief"
        started.wait(0.5)
        on_progress(0.9)

    deps, _ = make_deps(FakePipeline(steal_then_continue))
    assert process_job(job_id, deps) == "lost"
    assert load(job_id).worker_id == "thief"


def test_missing_job(make_deps):
    deps, _ = make_deps()
    assert process_job("does-not-exist", deps) == "missing"


def test_recover_requeues_interrupted_job(make_deps, job_id, settings):
    deps, calls = make_deps()
    with session_scope() as db:
        job_service.claim_job(db, job_id, "dead-worker", settings.job_lease_seconds)
        db.get(TranscodeJob, job_id).lease_expires_at = job_service.utcnow() - timedelta(minutes=1)

    assert recover_stale_jobs(deps) == {"requeued": 1, "failed": 0}
    assert load(job_id).status == JobStatus.PENDING
    assert calls == [(job_id, None)]
    assert process_job(job_id, deps) == "completed"
    assert load(job_id).attempts == 2


def test_recover_fails_job_out_of_attempts(make_deps, job_id, settings):
    deps, calls = make_deps()
    with session_scope() as db:
        job = db.get(TranscodeJob, job_id)
        job.status, job.attempts = JobStatus.PROCESSING, job.max_attempts
        job.lease_expires_at = job_service.utcnow() - timedelta(minutes=1)
    assert recover_stale_jobs(deps) == {"requeued": 0, "failed": 1}
    assert load(job_id).status == JobStatus.FAILED
    assert deps.redis.llen(settings.dead_letter_key) == 1


def test_recover_requeues_lost_pending_messages(make_deps, job_id, settings):
    deps, calls = make_deps()
    with session_scope() as db:
        db.get(TranscodeJob, job_id).updated_at = job_service.utcnow() - timedelta(
            seconds=settings.stale_pending_seconds + 5
        )
    assert recover_stale_jobs(deps)["requeued"] == 1
    assert recover_stale_jobs(deps)["requeued"] == 0  # touched, so not re-sent every cycle
