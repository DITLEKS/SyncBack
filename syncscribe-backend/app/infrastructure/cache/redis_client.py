"""
Единственная точка создания клиента Redis.
"""

from redis.asyncio import Redis

from app.core.config import get_settings

_redis_client: Redis | None = None


def get_redis_client() -> Redis:
    global _redis_client
    if _redis_client is None:
        settings = get_settings()
        _redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client
