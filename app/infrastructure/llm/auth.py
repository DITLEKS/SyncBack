"""Авторизация к LLM-API, отделённая от транспорта.

Единственное, чем провайдеры отличаются друг от друга помимо адреса и модели, — способ
получить заголовок Authorization. Поэтому новый провайдер — это новая стратегия здесь,
а не новый клиент.

  none           — локальные движки без ключа (Ollama, vLLM в контуре);
  bearer         — готовый API-ключ (OpenAI-совместимые сервисы, агрегаторы);
  oauth_gigachat — «ключ авторизации» GigaChat (Basic) обменивается на короткоживущий
                   access_token через POST /api/v2/oauth с RqUID и scope; токен кэшируется.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Callable
from typing import Protocol

import httpx

from app.core.config import Settings
from app.domain.exceptions import LLMInvalidResponseError, LLMTimeoutError

ClientFactory = Callable[[], httpx.AsyncClient]

# Обновляем токен заранее, чтобы он не истёк посередине долгого запроса к модели.
TOKEN_REFRESH_MARGIN_SECONDS = 60.0
# Если провайдер не сообщил expires_at: у GigaChat токен живёт 30 минут.
DEFAULT_TOKEN_TTL_SECONDS = 25 * 60.0


class AuthStrategy(Protocol):
    """Источник заголовков авторизации для каждого запроса к LLM."""

    refreshable: bool

    async def headers(self, *, force_refresh: bool = False) -> dict[str, str]: ...


class NoAuth:
    refreshable = False

    async def headers(self, *, force_refresh: bool = False) -> dict[str, str]:
        return {}


class BearerAuth:
    refreshable = False

    def __init__(self, api_key: str) -> None:
        self._api_key = api_key

    async def headers(self, *, force_refresh: bool = False) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}


class GigaChatOAuth:
    refreshable = True

    def __init__(
        self, auth_key: str, scope: str, oauth_url: str, client_factory: ClientFactory
    ) -> None:
        self._auth_key = auth_key
        self._scope = scope
        self._oauth_url = oauth_url
        self._client_factory = client_factory
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def headers(self, *, force_refresh: bool = False) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self._access_token(force_refresh)}"}

    async def _access_token(self, force_refresh: bool) -> str:
        async with self._lock:
            now = time.time()
            if (
                not force_refresh
                and self._token
                and now < self._expires_at - TOKEN_REFRESH_MARGIN_SECONDS
            ):
                return self._token
            self._token, self._expires_at = await self._fetch_token(now)
            return self._token

    async def _fetch_token(self, now: float) -> tuple[str, float]:
        headers = {
            "Authorization": f"Basic {self._auth_key}",
            "RqUID": str(uuid.uuid4()),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        try:
            async with self._client_factory() as client:
                response = await client.post(
                    self._oauth_url, headers=headers, data={"scope": self._scope}
                )
        except httpx.TimeoutException as exc:
            raise LLMTimeoutError("OAuth-сервер LLM не ответил вовремя") from exc
        except httpx.TransportError as exc:
            raise LLMTimeoutError(f"Не удалось подключиться к OAuth-серверу LLM: {exc}") from exc

        if response.status_code == 429 or response.status_code >= 500:
            raise LLMTimeoutError(f"OAuth-сервер LLM вернул HTTP {response.status_code}")
        if not response.is_success:
            raise LLMInvalidResponseError(
                f"OAuth-сервер LLM отклонил ключ авторизации: HTTP {response.status_code}: "
                f"{response.text[:200]}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise LLMInvalidResponseError("OAuth-сервер LLM вернул не JSON") from exc
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if not isinstance(token, str) or not token:
            raise LLMInvalidResponseError("OAuth-сервер LLM не вернул access_token")
        expires_at_ms = payload.get("expires_at")
        expires_at = (
            float(expires_at_ms) / 1000.0
            if isinstance(expires_at_ms, int | float)
            else now + DEFAULT_TOKEN_TTL_SECONDS
        )
        return token, expires_at


def build_auth(settings: Settings, client_factory: ClientFactory) -> AuthStrategy:
    if settings.llm_auth == "none":
        return NoAuth()
    if settings.llm_auth == "bearer":
        return BearerAuth(settings.llm_api_key)
    if settings.llm_auth == "oauth_gigachat":
        return GigaChatOAuth(
            auth_key=settings.llm_api_key,
            scope=settings.llm_oauth_scope,
            oauth_url=settings.llm_oauth_url,
            client_factory=client_factory,
        )
    raise ValueError(f"Неизвестный LLM_AUTH: {settings.llm_auth}")
