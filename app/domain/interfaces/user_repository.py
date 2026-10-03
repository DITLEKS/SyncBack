"""
Порт репозитория пользователей для domain-сервисов.

REVIEW-7: AuthService теперь зависит от этого Protocol, а не от
конкретного UserRepository из infrastructure.
"""

from __future__ import annotations

import uuid
from typing import Protocol, runtime_checkable

from app.domain.interfaces.entities import UserProtocol


@runtime_checkable
class IUserRepository(Protocol):
    async def get_by_email(self, email: str) -> UserProtocol | None: ...
    async def get_by_id(self, user_id: uuid.UUID) -> UserProtocol | None: ...
    async def create_from_credentials(self, email: str, password_hash: str) -> UserProtocol: ...
