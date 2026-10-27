import logging
import time

from prometheus_client import Counter, Gauge, Histogram
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.registry import Collector
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger(__name__)

# --- API ---
HTTP_REQUESTS = Counter("http_requests_total", "HTTP requests", ["method", "route", "status"])
HTTP_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency",
    ["method", "route"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30),
)
UPLOADS_REJECTED = Counter("transcoder_uploads_rejected_total", "Uploads rejected at validation", ["reason"])

# --- Worker ---
JOBS_FINISHED = Counter(
    "transcoder_jobs_finished_total", "Jobs reaching a terminal or retry state", ["outcome"]
)
JOBS_ACTIVE = Gauge("transcoder_jobs_active", "Jobs currently being transcoded by this worker")
JOB_DURATION = Histogram(
    "transcoder_job_duration_seconds",
    "Wall-clock duration of successful jobs",
    buckets=(10, 30, 60, 120, 300, 600, 1200, 1800, 3600, 7200, 14400),
)
JOB_SPEED = Histogram(
    "transcoder_job_realtime_factor",
    "Seconds of source video processed per wall-clock second",
    buckets=(0.1, 0.25, 0.5, 1, 2, 4, 8, 16),
)
FFMPEG_DURATION = Histogram(
    "transcoder_ffmpeg_duration_seconds",
    "Wall-clock time of individual FFmpeg invocations",
    ["step", "rendition"],
    buckets=(1, 5, 15, 30, 60, 120, 300, 600, 1200, 3600),
)
FFMPEG_CPU = Counter("transcoder_ffmpeg_cpu_seconds_total", "CPU seconds consumed by FFmpeg", ["step"])
FFMPEG_MAX_RSS = Gauge("transcoder_ffmpeg_max_rss_bytes", "Peak resident memory of FFmpeg child processes")
FFMPEG_FAILURES = Counter("transcoder_ffmpeg_failures_total", "FFmpeg invocations that failed", ["step"])
DEAD_LETTERED = Counter("transcoder_dead_lettered_total", "Jobs moved to the dead-letter list")


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    return getattr(route, "path", "unmatched")


class MetricsMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        start = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            route = _route_template(request)
            HTTP_REQUESTS.labels(request.method, route, str(status)).inc()
            HTTP_LATENCY.labels(request.method, route).observe(time.perf_counter() - start)


class PlatformCollector(Collector):
    """Scrape-time view of queue depth and job states, exported by the API."""

    def __init__(self, redis_factory, session_factory, settings):
        self.redis_factory = redis_factory
        self.session_factory = session_factory
        self.settings = settings

    def collect(self):
        from sqlalchemy import func, select

        from app.models import TranscodeJob

        queue = GaugeMetricFamily("transcoder_queue_depth", "Messages waiting in the transcode queue")
        dead = GaugeMetricFamily("transcoder_dead_letter_size", "Entries in the dead-letter list")
        try:
            r = self.redis_factory()
            queue.add_metric([], r.llen(self.settings.transcode_queue))
            dead.add_metric([], r.llen(self.settings.dead_letter_key))
            yield queue
            yield dead
        except Exception:
            logger.warning("Could not read queue metrics from Redis", exc_info=True)

        jobs = GaugeMetricFamily("transcoder_jobs", "Jobs by status", labels=["status"])
        try:
            with self.session_factory() as db:
                rows = db.execute(
                    select(TranscodeJob.status, func.count()).group_by(TranscodeJob.status)
                ).all()
            for status, count in rows:
                jobs.add_metric([status], count)
            yield jobs
        except Exception:
            logger.warning("Could not read job metrics from database", exc_info=True)
