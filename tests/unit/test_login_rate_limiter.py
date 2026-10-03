"""Защита логина от перебора: блокировка по паре (email, IP), TTL ставится один раз."""

from types import SimpleNamespace

from fakeredis.aioredis import FakeRedis

from app.infrastructure.security.login_rate_limiter import LoginRateLimiter


def _settings(max_attempts: int = 3, lockout_seconds: int = 60):
    return SimpleNamespace(login_max_attempts=max_attempts, login_lockout_seconds=lockout_seconds)


async def test_locks_after_max_failed_attempts():
    redis = FakeRedis()
    limiter = LoginRateLimiter(redis, _settings(max_attempts=3))
    email, ip = "user@example.com", "203.0.113.1"

    for _ in range(3):
        is_locked, _ = await limiter.is_locked(email, ip)
        assert is_locked is False
        await limiter.register_failure(email, ip)

    is_locked, retry_after = await limiter.is_locked(email, ip)
    assert is_locked is True
    assert 0 < retry_after <= 60


async def test_reset_clears_lock():
    redis = FakeRedis()
    limiter = LoginRateLimiter(redis, _settings(max_attempts=1))
    email, ip = "user2@example.com", "203.0.113.1"

    await limiter.register_failure(email, ip)
    assert (await limiter.is_locked(email, ip))[0] is True

    await limiter.reset(email, ip)
    assert (await limiter.is_locked(email, ip))[0] is False


async def test_different_emails_do_not_affect_each_other():
    redis = FakeRedis()
    limiter = LoginRateLimiter(redis, _settings(max_attempts=1))

    await limiter.register_failure("attacker@example.com", "203.0.113.1")
    assert (await limiter.is_locked("victim@example.com", "203.0.113.1"))[0] is False


async def test_attacker_from_other_ip_cannot_lock_victim():
    redis = FakeRedis()
    limiter = LoginRateLimiter(redis, _settings(max_attempts=1))

    await limiter.register_failure("victim@example.com", "198.51.100.9")
    assert (await limiter.is_locked("victim@example.com", "198.51.100.9"))[0] is True
    assert (await limiter.is_locked("victim@example.com", "203.0.113.1"))[0] is False


async def test_counter_always_has_ttl_and_window_is_not_extended():
    redis = FakeRedis()
    limiter = LoginRateLimiter(redis, _settings(max_attempts=5, lockout_seconds=60))
    email, ip = "user@example.com", None
    key = "login_attempts:user@example.com:unknown"

    await limiter.register_failure(email, ip)
    assert await redis.ttl(key) == 60
    await redis.expire(key, 10)

    await limiter.register_failure(email, ip)
    assert int(await redis.get(key)) == 2
    assert await redis.ttl(key) <= 10


async def test_email_is_case_insensitive():
    redis = FakeRedis()
    limiter = LoginRateLimiter(redis, _settings(max_attempts=1))

    await limiter.register_failure("User@Example.com", "203.0.113.1")
    assert (await limiter.is_locked("user@example.com", "203.0.113.1"))[0] is True
