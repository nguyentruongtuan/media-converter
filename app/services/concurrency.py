"""Cluster-wide cap on concurrent transcodes, as a Redis sorted set of expiring leases."""

import time

import redis

_ACQUIRE = """
local key, member = KEYS[1], ARGV[1]
local limit, now, expires = tonumber(ARGV[2]), tonumber(ARGV[3]), tonumber(ARGV[4])
redis.call('ZREMRANGEBYSCORE', key, '-inf', now)
if redis.call('ZSCORE', key, member) or redis.call('ZCARD', key) < limit then
  redis.call('ZADD', key, expires, member)
  return 1
end
return 0
"""


class ConcurrencyLimiter:
    def __init__(self, client: redis.Redis, limit: int, lease_seconds: int, key: str = "transcode:slots"):
        self.client = client
        self.limit = limit
        self.lease_seconds = lease_seconds
        self.key = key
        self._acquire = client.register_script(_ACQUIRE) if limit > 0 else None

    def acquire(self, member: str) -> bool:
        if self._acquire is None:
            return True
        now = time.time()
        return bool(self._acquire(keys=[self.key], args=[member, self.limit, now, now + self.lease_seconds]))

    def refresh(self, member: str) -> None:
        if self._acquire is not None:
            self.client.zadd(self.key, {member: time.time() + self.lease_seconds}, xx=True)

    def release(self, member: str) -> None:
        if self._acquire is not None:
            self.client.zrem(self.key, member)
