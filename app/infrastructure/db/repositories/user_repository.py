"""
Репозиторий пользователей.

ИЗМЕНЕНИЯ:
- HIGH-A: удалён session.commit() из create() — нарушение UoW-правила.
  Теперь только flush() + refresh() через параметрD obj для совместимости;
  commit — ответственность вызывающего UoW.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.user import User


class UserRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_email(self, email: str) -> User | None:
        result = await self._session.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        return await self._session.get(User, user_id)

    async def create(self, user: User) -> User:
        """HIGH-A: commit удалён — фиксирует вызывающий UoW."""
        self._session.add(user)
        await self._session.flush()
        await self._session.refresh(user)
        return user
