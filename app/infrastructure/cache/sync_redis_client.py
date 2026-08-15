from redis import Redis
from app.core.config import get_settings

_sync_redis_client: Redis | None = None


def get_sync_redis_client() -> Redis:
    global _sync_redis_client
    if _sync_redis_client is None:
        _sync_redis_client = Redis.from_url(get_settings().redis_url, decode_responses=True)
    return _sync_redis_client
