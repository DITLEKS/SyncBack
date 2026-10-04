"""Повторы запросов к LLM: какие сбои повторяются, сколько раз и с какой задержкой."""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from app.domain.exceptions import LLMInvalidResponseError, LLMTimeoutError
from app.infrastructure.llm import http_retry_mixin
from app.infrastructure.llm.http_retry_mixin import HttpConnectionRetryMixin, backoff_delay

pytestmark = pytest.mark.asyncio


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    recorded: list[float] = []

    async def fake_sleep(delay: float) -> None:
        recorded.append(delay)

    monkeypatch.setattr(http_retry_mixin, "_sleep", fake_sleep)
    return recorded


def _install(monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]):
    real_client = httpx.AsyncClient
    calls: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    monkeypatch.setattr(
        http_retry_mixin.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(recording), **kwargs),
    )
    return calls


async def _post(max_retries: int = 3) -> httpx.Response:
    return await HttpConnectionRetryMixin()._post_with_retries(
        url="http://llm.test/generate",
        headers={},
        json_payload={"prompt": "p"},
        timeout_seconds=5,
        max_retries=max_retries,
    )


async def test_retries_server_errors_with_exponential_backoff(monkeypatch, sleeps) -> None:
    statuses = iter([503, 500, 200])
    calls = _install(monkeypatch, lambda r: httpx.Response(next(statuses), json={"ok": True}))
    response = await _post()
    assert response.status_code == 200
    assert len(calls) == 3
    assert sleeps == [1.0, 2.0]


async def test_rate_limit_honours_retry_after(monkeypatch, sleeps) -> None:
    responses = iter([httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200)])
    _install(monkeypatch, lambda r: next(responses))
    await _post()
    assert sleeps == [7.0]


async def test_gives_up_after_max_retries(monkeypatch, sleeps) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    calls = _install(monkeypatch, refuse)
    with pytest.raises(LLMTimeoutError, match="попыток: 3"):
        await _post(max_retries=2)
    assert len(calls) == 3
    assert len(sleeps) == 2


async def test_client_errors_are_not_retried(monkeypatch, sleeps) -> None:
    calls = _install(monkeypatch, lambda r: httpx.Response(400, text="bad prompt"))
    with pytest.raises(LLMInvalidResponseError, match="HTTP 400"):
        await _post()
    assert len(calls) == 1
    assert sleeps == []


async def test_timeout_is_retried(monkeypatch, sleeps) -> None:
    attempts = iter([True, False])

    def handler(request: httpx.Request) -> httpx.Response:
        if next(attempts):
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200)

    _install(monkeypatch, handler)
    assert (await _post()).status_code == 200
    assert sleeps == [1.0]


def test_backoff_delay_is_capped() -> None:
    assert [backoff_delay(i) for i in range(3)] == [1.0, 2.0, 4.0]
    assert backoff_delay(10) == http_retry_mixin.BACKOFF_MAX_SECONDS
    assert backoff_delay(0, "999") == http_retry_mixin.BACKOFF_MAX_SECONDS
    assert backoff_delay(1, "soon") == 2.0
