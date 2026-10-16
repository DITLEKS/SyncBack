"""Повторы HTTP-запросов к LLM с экспоненциальной задержкой."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.domain.exceptions import LLMInvalidResponseError, LLMTimeoutError

BACKOFF_BASE_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 30.0
_sleep = asyncio.sleep


def backoff_delay(attempt: int, retry_after: str | None = None) -> float:
    if retry_after is not None:
        try:
            return min(max(float(retry_after), 0.0), BACKOFF_MAX_SECONDS)
        except ValueError:
            pass
    return min(BACKOFF_BASE_SECONDS * (1 << attempt), BACKOFF_MAX_SECONDS)


def _is_retryable_status(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


class HttpConnectionRetryMixin:
    def _make_client(self, timeout: float, verify: bool | str = True) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, verify=verify)

    async def _post_with_retries(
        self,
        url: str,
        headers: dict[str, str],
        json_payload: dict[str, Any],
        timeout_seconds: int,
        max_retries: int,
        verify: bool | str = True,
    ) -> httpx.Response:
        """POST без включения request/response body и секретов в исключения."""
        async with self._make_client(timeout=timeout_seconds, verify=verify) as client:
            for attempt in range(max_retries + 1):
                retry_after: str | None = None
                try:
                    response = await client.post(url, headers=headers, json=json_payload)
                except httpx.TimeoutException as exc:
                    failure = f"LLM не ответила за {timeout_seconds} сек."
                    cause: Exception = exc
                except httpx.TransportError as exc:
                    failure = "Не удалось подключиться к LLM-эндпоинту"
                    cause = exc
                else:
                    if response.is_success:
                        return response
                    if not _is_retryable_status(response.status_code):
                        raise LLMInvalidResponseError(
                            f"LLM вернула HTTP {response.status_code}",
                            status_code=response.status_code,
                        )
                    failure = f"LLM вернула HTTP {response.status_code}"
                    cause = httpx.HTTPStatusError(
                        failure, request=response.request, response=response
                    )
                    retry_after = response.headers.get("retry-after")

                if attempt == max_retries:
                    raise LLMTimeoutError(f"{failure} (попыток: {attempt + 1})") from cause
                await _sleep(backoff_delay(attempt, retry_after))
        raise AssertionError("unreachable")
