"""
Провайдер-агностичная логика повторных попыток при обрыве соединения. Не содержит
бизнес-логики формата запроса/ответа — только быстрый повтор POST при обрыве соединения.
"""
import asyncio
import httpx
from app.domain.exceptions import LLMInvalidResponseError, LLMTimeoutError

CONNECTION_RETRY_ATTEMPTS = 2
CONNECTION_RETRY_DELAY_SECONDS = 1.0


class HttpConnectionRetryMixin:
    async def _post_with_connection_retry(self, url: str, headers: dict, json_payload: dict, timeout_seconds: int) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(CONNECTION_RETRY_ATTEMPTS + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                    response = await client.post(url, headers=headers, json=json_payload)
                    response.raise_for_status()
                    return response
            except httpx.ConnectError as exc:
                last_error = exc
                if attempt < CONNECTION_RETRY_ATTEMPTS:
                    await asyncio.sleep(CONNECTION_RETRY_DELAY_SECONDS)
                    continue
                raise LLMTimeoutError(f"Не удалось подключиться к LLM-эндпоинту после {attempt + 1} попыток: {exc}") from exc
            except httpx.TimeoutException as exc:
                raise LLMTimeoutError(f"LLM не ответила за {timeout_seconds} сек.") from exc
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    raise LLMTimeoutError("LLM вернула 429 Too Many Requests") from exc
                raise LLMInvalidResponseError(f"LLM вернула HTTP {exc.response.status_code}: {exc.response.text[:200]}") from exc
            except httpx.HTTPError as exc:
                raise LLMInvalidResponseError(f"LLM вернула ошибку: {exc}") from exc
        raise LLMTimeoutError(f"Не удалось получить ответ от LLM: {last_error}")
