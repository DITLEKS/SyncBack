"""Порты безопасности: хэширование паролей, выпуск токенов, защита логина.

Домен описывает, что ему нужно от этих механизмов; реализации живут
в app.infrastructure.security и подключаются через DI.
"""

from __future__ import annotations

import uuid
from typing import Any, Protocol


class IPasswordHasher(Protocol):
    def hash(self, plain_password: str) -> str: ...

    def verify(self, plain_password: str, password_hash: str) -> bool: ...


class ITokenIssuer(Protocol):
    """Выпуск и проверка пары access/refresh-токенов.

    decode_* бросают InvalidTokenError для просроченного, подделанного
    или токена другого типа.
    """

    def create_access_token(self, user_id: uuid.UUID, role: str) -> tuple[str, int]:
        """Вернуть (token, expires_in_seconds)."""
        ...

    def create_refresh_token(self, user_id: uuid.UUID, role: str) -> tuple[str, int, str]:
        """Вернуть (token, expires_in_seconds, jti)."""
        ...

    def decode_access_token(self, token: str) -> dict[str, Any]: ...

    def decode_refresh_token(self, token: str) -> dict[str, Any]: ...


class ILoginThrottle(Protocol):
    """Защита входа от перебора пароля по идентификатору учётной записи."""

    async def is_locked(self, email: str) -> tuple[bool, int]:
        """Вернуть (заблокирован ли вход, секунд до разблокировки)."""
        ...

    async def register_failure(self, email: str) -> None: ...

    async def reset(self, email: str) -> None: ...


class IRefreshTokenStore(Protocol):
    """Реестр активных refresh-токенов по jti для ротации и отзыва."""

    async def save(self, jti: str, user_id: uuid.UUID, ttl_seconds: int) -> None: ...

    async def revoke_if_valid(self, jti: str) -> bool:
        """Атомарно отозвать токен; False, если он уже отозван или не существовал."""
        ...

    async def revoke(self, jti: str) -> None: ...
