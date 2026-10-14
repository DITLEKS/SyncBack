"""OAuth GigaChat: повторы при 429/5xx, общий кэш токена, устойчивость к сбоям кэша."""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx
import pytest

from app.domain.exceptions import LLMInvalidResponseError, LLMTimeoutError
from app.infrastructure.llm import auth as auth_module
from app.infrastructure.llm import http_retry_mixin
from app.infrastructure.llm.auth import GigaChatOAuth, RedisTokenCache

pytestmark = pytest.mark.asyncio

OAUTH_URL = "http://oauth.test/api/v2/oauth"


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    recorded: list[float] = []

    async def fake_sleep(delay: float) -> None:
        recorded.append(delay)

    monkeypatch.setattr(http_retry_mixin, "_sleep", fake_sleep)
    return recorded


def _factory(handler: Callable[[httpx.Request], httpx.Response]):
    calls: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    return calls, lambda: httpx.AsyncClient(transport=httpx.MockTransport(recording))


def _token_response(token: str) -> httpx.Response:
    return httpx.Response(
        200, json={"access_token": token, "expires_at": int((time.time() + 1800) * 1000)}
    )


def _auth(
    handler, *, max_retries: int = 3, cache=None
) -> tuple[list[httpx.Request], GigaChatOAuth]:
    calls, factory = _factory(handler)
    strategy = GigaChatOAuth(
        auth_key="k",
        scope="S",
        oauth_url=OAUTH_URL,
        client_factory=factory,
        max_retries=max_retries,
        token_cache=cache,
    )
    return calls, strategy


class MemoryCache:
    def __init__(self) -> None:
        self.value: tuple[str, float] | None = None

    async def get(self):
        return self.value

    async def set(self, token: str, expires_at: float) -> None:
        self.value = (token, expires_at)


class _FakeRedis:
    store: dict[str, tuple[str, int | None]] = {}
    fail = False

    @classmethod
    def from_url(cls, url: str, decode_responses: bool = True) -> "_FakeRedis":
        if cls.fail:
            raise ConnectionError("redis down")
        return cls()

    async def get(self, key: str):
        item = self.store.get(key)
        return item[0] if item else None

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = (value, ex)

    async def aclose(self) -> None:
        return None


async def test_oauth_429_is_retried_with_backoff(sleeps) -> None:
    responses = iter([httpx.Response(429), httpx.Response(503), _token_response("t1")])
    calls, strategy = _auth(lambda r: next(responses))

    assert await strategy.headers() == {"Authorization": "Bearer t1"}
    assert len(calls) == 3
    assert sleeps == [1.0, 2.0]


async def test_oauth_429_honours_retry_after(sleeps) -> None:
    responses = iter([httpx.Response(429, headers={"Retry-After": "5"}), _token_response("t1")])
    _, strategy = _auth(lambda r: next(responses))
    await strategy.headers()
    assert sleeps == [5.0]


async def test_oauth_gives_up_after_max_retries(sleeps) -> None:
    calls, strategy = _auth(lambda r: httpx.Response(429), max_retries=1)
    with pytest.raises(LLMTimeoutError, match="HTTP 429 \\(попыток: 2\\)"):
        await strategy.headers()
    assert len(calls) == 2
    assert sleeps == [1.0]


async def test_oauth_rejected_key_is_not_retried(sleeps) -> None:
    calls, strategy = _auth(lambda r: httpx.Response(401, text="bad key"))
    with pytest.raises(LLMInvalidResponseError, match="HTTP 401"):
        await strategy.headers()
    assert len(calls) == 1
    assert sleeps == []


async def test_shared_cache_means_one_oauth_request_for_two_processes() -> None:
    cache = MemoryCache()
    calls_a, first = _auth(lambda r: _token_response("shared"), cache=cache)
    calls_b, second = _auth(lambda r: _token_response("unexpected"), cache=cache)

    assert await first.headers() == {"Authorization": "Bearer shared"}
    assert await second.headers() == {"Authorization": "Bearer shared"}
    assert len(calls_a) == 1
    assert calls_b == []


async def test_expired_cached_token_is_refetched_and_cache_updated() -> None:
    cache = MemoryCache()
    cache.value = ("stale", time.time() + 10)
    calls, strategy = _auth(lambda r: _token_response("fresh"), cache=cache)

    assert await strategy.headers() == {"Authorization": "Bearer fresh"}
    assert len(calls) == 1
    assert cache.value is not None and cache.value[0] == "fresh"


async def test_force_refresh_bypasses_cache() -> None:
    cache = MemoryCache()
    cache.value = ("cached", time.time() + 1800)
    calls, strategy = _auth(lambda r: _token_response("fresh"), cache=cache)

    assert await strategy.headers(force_refresh=True) == {"Authorization": "Bearer fresh"}
    assert len(calls) == 1


async def test_redis_cache_roundtrip(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeRedis.store = {}
    _FakeRedis.fail = False
    monkeypatch.setattr(auth_module, "Redis", _FakeRedis)
    expires_at = time.time() + 1800

    cache = RedisTokenCache("redis://test/0", "key", "SCOPE")
    await cache.set("t1", expires_at)
    assert await cache.get() == ("t1", expires_at)

    [(key, (_, ttl))] = _FakeRedis.store.items()
    assert key.startswith("llm:oauth_token:")
    assert ttl is not None and 1700 < ttl <= 1800

    other = RedisTokenCache("redis://test/0", "other-key", "SCOPE")
    assert await other.get() is None


async def test_redis_cache_failure_falls_back_to_oauth(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeRedis.fail = True
    monkeypatch.setattr(auth_module, "Redis", _FakeRedis)
    cache = RedisTokenCache("redis://test/0", "key", "SCOPE")
    calls, strategy = _auth(lambda r: _token_response("t1"), cache=cache)

    assert await strategy.headers() == {"Authorization": "Bearer t1"}
    assert len(calls) == 1
    _FakeRedis.fail = False
