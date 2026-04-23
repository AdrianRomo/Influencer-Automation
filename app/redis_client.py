"""Process-wide Redis client pool.

One connection pool per process, shared by rate limiting, task ownership,
refresh tokens, backpressure, article locks, and Celery signal handlers.
"""
import os
from typing import Optional

from redis import Redis


_client: Optional[Redis] = None


def get_redis() -> Redis:
    """Return the shared Redis client. Initialized lazily on first call."""
    global _client
    if _client is None:
        _client = Redis.from_url(
            os.getenv("CELERY_BROKER_URL", "redis://redis:6379/0"),
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
            health_check_interval=30,
        )
    return _client
