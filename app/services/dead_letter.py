import json
import logging
from datetime import UTC, datetime

import redis

from app.config import Settings
from app.metrics import DEAD_LETTERED

logger = logging.getLogger(__name__)


def push(
    client: redis.Redis, settings: Settings, *, job_id: str, video_id: str, attempts: int, error: str
) -> None:
    entry = {
        "job_id": job_id,
        "video_id": video_id,
        "attempts": attempts,
        "error": error[:2000],
        "failed_at": datetime.now(UTC).isoformat(),
    }
    DEAD_LETTERED.inc()
    try:
        pipe = client.pipeline()
        pipe.lpush(settings.dead_letter_key, json.dumps(entry))
        pipe.ltrim(settings.dead_letter_key, 0, settings.dead_letter_max_length - 1)
        pipe.execute()
    except redis.RedisError:
        # The job is already FAILED in Postgres, which remains the source of truth.
        logger.exception("Could not write dead-letter entry", extra={"dead_letter_job_id": job_id})


def list_entries(client: redis.Redis, settings: Settings, limit: int = 100) -> list[dict]:
    return [json.loads(e) for e in client.lrange(settings.dead_letter_key, 0, limit - 1)]
