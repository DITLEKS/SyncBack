"""Повторы HTTP-запросов к LLM с экспоненциальной задержкой.

Повторяются только временные сбои: обрыв соединения, таймаут, 429 и 5xx.
Остальные ответы 4xx — ошибка запроса, повтор её не исправит.
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.domain.exceptions import LLMInvalidResponseError, LLMTimeoutError

BACKOFF_BASE_SECONDS = 1.0
BACKOFF_MAX_SECONDS = 30.0

_sleep = asyncio.sleep


def backoff_delay(attempt: int, retry_after: str | None = None) -> float:
    """Задержка перед повтором номер attempt (с нуля); Retry-After в секундах важнее."""
    if retry_after is not None:
        try:
            return min(max(float(retry_after), 0.0), BACKOFF_MAX_SECONDS)
        except ValueError:
            pass
    return min(BACKOFF_BASE_SECONDS * (1 << attempt), BACKOFF_MAX_SECONDS)


def _is_retryable_status(status_code: int) -> bool:
    return status_code == 429 or status_code >= 500


class HttpConnectionRetryMixin:
    async def _post_with_retries(
        self,
        url: str,
        headers: dict[str, str],
        json_payload: dict[str, Any],
        timeout_seconds: int,
        max_retries: int,
    ) -> httpx.Response:
        """POST с max_retries повторами временных сбоев.

        LLMTimeoutError — LLM недоступна или перегружена после всех попыток;
        LLMInvalidResponseError — LLM отклонила запрос (4xx, кроме 429).
        """
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            for attempt in range(max_retries + 1):
                retry_after: str | None = None
                try:
                    response = await client.post(url, headers=headers, json=json_payload)
                except httpx.TimeoutException as exc:
                    failure = f"LLM не ответила за {timeout_seconds} сек."
                    cause: Exception = exc
                except httpx.TransportError as exc:
                    failure = f"Не удалось подключиться к LLM-эндпоинту: {exc}"
                    cause = exc
                else:
                    if response.is_success:
                        return response
                    if not _is_retryable_status(response.status_code):
                        raise LLMInvalidResponseError(
                            f"LLM вернула HTTP {response.status_code}: {response.text[:200]}"
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
