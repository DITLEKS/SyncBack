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
        attempts = await self._redis.get(self._key(email))
        if attempts is None or int(attempts) < self._max_attempts:
            return False, 0
        ttl = await self._redis.ttl(self._key(email))
        return True, max(ttl, 0)

    async def register_failure(self, email: str) -> None:
        key = self._key(email)
        attempts = await self._redis.incr(key)
        if attempts == 1:
            await self._redis.expire(key, self._lockout_seconds)

    async def reset(self, email: str) -> None:
        await self._redis.delete(self._key(email))
