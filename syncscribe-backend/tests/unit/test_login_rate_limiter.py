"""
Проверяем защиту логина от брутфорса: после login_max_attempts неудачных попыток аккаунт
блокируется на login_lockout_seconds, а reset() (успешный вход) снимает блокировку.
Используем fakeredis вместо реального Redis, чтобы тест был быстрым и изолированным.
"""

from types import SimpleNamespace

from fakeredis.aioredis import FakeRedis

from app.infrastructure.security.login_rate_limiter import LoginRateLimiter


def _settings(max_attempts: int = 3, lockout_seconds: int = 60):
    return SimpleNamespace(login_max_attempts=max_attempts, login_lockout_seconds=lockout_seconds)


async def test_locks_after_max_failed_attempts():
    redis = FakeRedis()
    limiter = LoginRateLimiter(redis, _settings(max_attempts=3))
    email = "user@example.com"

    for _ in range(3):
        is_locked, _ = await limiter.is_locked(email)
        assert is_locked is False
        await limiter.register_failure(email)

    is_locked, retry_after = await limiter.is_locked(email)
    assert is_locked is True
    assert retry_after > 0


async def test_reset_clears_lock():
    redis = FakeRedis()
    limiter = LoginRateLimiter(redis, _settings(max_attempts=1))
    email = "user2@example.com"

    await limiter.register_failure(email)
    is_locked, _ = await limiter.is_locked(email)
    assert is_locked is True

    await limiter.reset(email)
    is_locked, _ = await limiter.is_locked(email)
    assert is_locked is False


async def test_different_emails_do_not_affect_each_other():
    redis = FakeRedis()
    limiter = LoginRateLimiter(redis, _settings(max_attempts=1))

    await limiter.register_failure("attacker@example.com")
    is_locked, _ = await limiter.is_locked("victim@example.com")
    assert is_locked is False
