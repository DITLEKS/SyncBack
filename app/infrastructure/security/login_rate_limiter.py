"""Защита логина от перебора: счётчик неудачных попыток в Redis.

Ключ — пара (email, адрес клиента). INCR и EXPIRE выполняются одной
транзакцией, а EXPIRE с NX ставит срок жизни только при первом сбое:
ключ не может остаться без TTL, и окно блокировки не продлевается
каждой новой неудачной попыткой.
"""

from redis.asyncio import Redis

from app.core.config import Settings


class LoginRateLimiter:
    def __init__(self, redis: Redis, settings: Settings):
        self._redis = redis
        self._max_attempts = settings.login_max_attempts
        self._lockout_seconds = settings.login_lockout_seconds

    @staticmethod
    def _key(email: str, client_ip: str | None) -> str:
        return f"login_attempts:{email.lower()}:{client_ip or 'unknown'}"

    async def is_locked(self, email: str, client_ip: str | None) -> tuple[bool, int]:
        key = self._key(email, client_ip)
        attempts = await self._redis.get(key)
        if attempts is None or int(attempts) < self._max_attempts:
            return False, 0
        ttl = await self._redis.ttl(key)
        return True, max(ttl, 0)

    async def register_failure(self, email: str, client_ip: str | None) -> None:
        key = self._key(email, client_ip)
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, self._lockout_seconds, nx=True)
            await pipe.execute()

    async def reset(self, email: str, client_ip: str | None) -> None:
        await self._redis.delete(self._key(email, client_ip))
