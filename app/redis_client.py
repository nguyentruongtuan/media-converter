from functools import lru_cache

import redis

from app.config import get_settings


@lru_cache
def get_redis() -> redis.Redis:
    return redis.Redis.from_url(
        get_settings().redis_url, decode_responses=True, socket_timeout=5, socket_connect_timeout=5
    )
