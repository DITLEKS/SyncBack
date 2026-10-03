"""
Защита логина от брутфорса: счётчик неудачных попыток по email в Redis.
"""

from redis.asyncio import Redis

from app.core.config import Settings


class LoginRateLimiter:
    def __init__(self, redis: Redis, settings: Settings):
        self._redis = redis
        self._max_attempts = settings.login_max_attempts
        self._lockout_seconds = settings.login_lockout_seconds

    def _key(self, email: str) -> str:
        return f"login_attempts:{email.lower()}"

    async def is_locked(self, email: str) -> tuple[bool, int]:
        key = self._key(email)
        attempts = await self._redis.get(key)
        if attempts is None or int(attempts) < self._max_attempts:
            return False, 0
        ttl = await self._redis.ttl(key)
        if ttl < 0:
            # ключ без TTL (сбой между INCR и EXPIRE) — не даём блокировке стать вечной
            await self._redis.expire(key, self._lockout_seconds)
            ttl = self._lockout_seconds
        return True, ttl

    async def register_failure(self, email: str) -> None:
        key = self._key(email)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.ttl(key)
            attempts, ttl = await pipe.execute()
        if attempts == 1 or ttl < 0:
            await self._redis.expire(key, self._lockout_seconds)

    async def reset(self, email: str) -> None:
        await self._redis.delete(self._key(email))
