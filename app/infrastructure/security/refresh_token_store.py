"""
REVIEW-1: хранилище активных refresh-токенов в Redis.

Принцип работы (rotation with revocation):
  - При логине / обновлении токена: save(jti, user_id, ttl)
  - При refresh: revoke_if_valid(old_jti) → если токен не найден → 401
  - При logout: revoke(jti)

Каждый refresh-токен несёт claim `jti` (JWT ID, uuid4).
JWTHandler.create_refresh_token добавляет `jti` автоматически (см. изменения).
"""
from __future__ import annotations

import uuid

from redis.asyncio import Redis

_PREFIX = "syncscribe:rt:"


class RefreshTokenStore:
    """Тонкая обёртка над Redis для управления активными refresh-токенами."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    def _key(self, jti: str) -> str:
        return f"{_PREFIX}{jti}"

    async def save(self, jti: str, user_id: uuid.UUID, ttl_seconds: int) -> None:
        """Зарегистрировать новый активный refresh-токен."""
        await self._redis.set(self._key(jti), str(user_id), ex=ttl_seconds)

    async def revoke_if_valid(self, jti: str) -> bool:
        """Проверить и атомарно удалить токен.

        Возвращает True, если токен был найден и успешно удалён.
        Возвращает False, если токен не существует (уже использован или отозван).
        """
        deleted = await self._redis.delete(self._key(jti))
        return deleted > 0

    async def revoke(self, jti: str) -> None:
        """Отозвать токен (logout)."""
        await self._redis.delete(self._key(jti))
