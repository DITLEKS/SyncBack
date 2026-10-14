"""Авторизация к LLM-API, отделённая от транспорта.

Единственное, чем провайдеры отличаются друг от друга помимо адреса и модели, — способ
получить заголовок Authorization. Поэтому новый провайдер — это новая стратегия здесь,
а не новый клиент.

  none           — локальные движки без ключа (Ollama, vLLM в контуре);
  bearer         — готовый API-ключ (OpenAI-совместимые сервисы, агрегаторы);
  oauth_gigachat — «ключ авторизации» GigaChat (Basic) обменивается на короткоживущий
                   access_token через POST /api/v2/oauth с RqUID и scope.

Токен GigaChat кэшируется в памяти процесса и, если задан redis_url, в Redis: воркер
работает несколькими процессами, и без общего кэша каждый из них ходит за своим токеном.
Источники одной задачи стартуют одновременно, и OAuth-сервер отвечал на такую пачку 429.
Запрос токена повторяется по тем же правилам, что и запрос к модели (429 и 5xx,
экспоненциальная задержка, Retry-After важнее) — раньше это было единственное звено
цепочки без повторов, и один 429 ронял весь источник.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from collections.abc import Callable
from typing import Protocol

import httpx
from redis.asyncio import Redis

from app.core.config import Settings
from app.domain.exceptions import LLMInvalidResponseError, LLMTimeoutError
from app.infrastructure.llm import http_retry_mixin

logger = logging.getLogger("syncscribe.llm.auth")

ClientFactory = Callable[[], httpx.AsyncClient]

# Обновляем токен заранее, чтобы он не истёк посередине долгого запроса к модели.
TOKEN_REFRESH_MARGIN_SECONDS = 60.0
# Если провайдер не сообщил expires_at: у GigaChat токен живёт 30 минут.
DEFAULT_TOKEN_TTL_SECONDS = 25 * 60.0
_TOKEN_CACHE_KEY_PREFIX = "llm:oauth_token:"


class AuthStrategy(Protocol):
    """Источник заголовков авторизации для каждого запроса к LLM."""

    refreshable: bool

    async def headers(self, *, force_refresh: bool = False) -> dict[str, str]: ...


class TokenCache(Protocol):
    """Общий кэш access_token между процессами."""

    async def get(self) -> tuple[str, float] | None: ...

    async def set(self, token: str, expires_at: float) -> None: ...


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


class RedisTokenCache:
    """Токен в Redis под ключом, привязанным к ключу авторизации и scope.

    Клиент создаётся на каждый вызов: в воркере каждая задача живёт в своём event loop,
    и общий клиент redis.asyncio между ними переиспользовать нельзя. Недоступный Redis —
    не ошибка: стратегия просто запросит токен сама.
    """

    def __init__(self, redis_url: str, auth_key: str, scope: str) -> None:
        self._redis_url = redis_url
        digest = hashlib.sha256(f"{auth_key}:{scope}".encode()).hexdigest()[:16]
        self._key = f"{_TOKEN_CACHE_KEY_PREFIX}{digest}"

    async def get(self) -> tuple[str, float] | None:
        try:
            client: Redis = Redis.from_url(self._redis_url, decode_responses=True)
            try:
                raw = await client.get(self._key)
            finally:
                await client.aclose()
            if raw is None:
                return None
            payload = json.loads(raw)
            return str(payload["access_token"]), float(payload["expires_at"])
        except Exception:
            logger.warning(
                "Кэш OAuth-токена LLM недоступен, токен будет запрошен заново", exc_info=True
            )
            return None

    async def set(self, token: str, expires_at: float) -> None:
        ttl = int(expires_at - time.time())
        if ttl <= 0:
            return
        value = json.dumps({"access_token": token, "expires_at": expires_at})
        try:
            client: Redis = Redis.from_url(self._redis_url, decode_responses=True)
            try:
                await client.set(self._key, value, ex=ttl)
            finally:
                await client.aclose()
        except Exception:
            logger.warning("Не удалось сохранить OAuth-токен LLM в кэш", exc_info=True)


class GigaChatOAuth:
    refreshable = True

    def __init__(
        self,
        auth_key: str,
        scope: str,
        oauth_url: str,
        client_factory: ClientFactory,
        *,
        max_retries: int = 0,
        token_cache: TokenCache | None = None,
    ) -> None:
        self._auth_key = auth_key
        self._scope = scope
        self._oauth_url = oauth_url
        self._client_factory = client_factory
        self._max_retries = max_retries
        self._cache = token_cache
        self._token: str | None = None
        self._expires_at = 0.0
        # Lock привязывается к event loop при первом ожидании, а воркер запускает каждую
        # задачу в новом loop — поэтому он создаётся лениво и пересоздаётся при смене loop.
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None

    async def headers(self, *, force_refresh: bool = False) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self._access_token(force_refresh)}"}

    def _get_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock = asyncio.Lock()
            self._lock_loop = loop
        return self._lock

    def _fresh_token(self, now: float) -> str | None:
        if self._token and now < self._expires_at - TOKEN_REFRESH_MARGIN_SECONDS:
            return self._token
        return None

    async def _access_token(self, force_refresh: bool) -> str:
        async with self._get_lock():
            now = time.time()
            if not force_refresh:
                token = self._fresh_token(now)
                if token is not None:
                    return token
                if self._cache is not None:
                    cached = await self._cache.get()
                    if cached is not None:
                        self._token, self._expires_at = cached
                        token = self._fresh_token(now)
                        if token is not None:
                            return token
            self._token, self._expires_at = await self._fetch_token(now)
            if self._cache is not None:
                await self._cache.set(self._token, self._expires_at)
            return self._token

    async def _post_oauth(self) -> httpx.Response:
        headers = {
            "Authorization": f"Basic {self._auth_key}",
            "RqUID": str(uuid.uuid4()),
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }
        async with self._client_factory() as client:
            return await client.post(self._oauth_url, headers=headers, data={"scope": self._scope})

    async def _fetch_token(self, now: float) -> tuple[str, float]:
        """Запросить токен с max_retries повторами временных сбоев (как у запроса к модели)."""
        for attempt in range(self._max_retries + 1):
            retry_after: str | None = None
            cause: Exception
            try:
                response = await self._post_oauth()
            except httpx.TimeoutException as exc:
                failure = "OAuth-сервер LLM не ответил вовремя"
                cause = exc
            except httpx.TransportError as exc:
                failure = f"Не удалось подключиться к OAuth-серверу LLM: {exc}"
                cause = exc
            else:
                if response.is_success:
                    return self._parse_token(response, now)
                if response.status_code != 429 and response.status_code < 500:
                    raise LLMInvalidResponseError(
                        f"OAuth-сервер LLM отклонил ключ авторизации: HTTP {response.status_code}: "
                        f"{response.text[:200]}"
                    )
                failure = f"OAuth-сервер LLM вернул HTTP {response.status_code}"
                cause = httpx.HTTPStatusError(failure, request=response.request, response=response)
                retry_after = response.headers.get("retry-after")

            if attempt == self._max_retries:
                raise LLMTimeoutError(f"{failure} (попыток: {attempt + 1})") from cause
            await http_retry_mixin._sleep(http_retry_mixin.backoff_delay(attempt, retry_after))
        raise AssertionError("unreachable")

    @staticmethod
    def _parse_token(response: httpx.Response, now: float) -> tuple[str, float]:
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
        redis_url = getattr(settings, "redis_url", "")
        token_cache = (
            RedisTokenCache(redis_url, settings.llm_api_key, settings.llm_oauth_scope)
            if redis_url
            else None
        )
        return GigaChatOAuth(
            auth_key=settings.llm_api_key,
            scope=settings.llm_oauth_scope,
            oauth_url=settings.llm_oauth_url,
            client_factory=client_factory,
            max_retries=settings.llm_max_retries,
            token_cache=token_cache,
        )
    raise ValueError(f"Неизвестный LLM_AUTH: {settings.llm_auth}")
