"""
SqlAlchemyDashboardQueryService — инфраструктурная реализация IDashboardQueryService.

L-C: вынесено из IUnitOfWork. Инжектируется через FastAPI Depends:

    from app.infrastructure.db.services.dashboard_query_service import (
        SqlAlchemyDashboardQueryService,
    )

    async def get_dashboard_query_service(
        session: AsyncSession = Depends(get_db),
    ) -> IDashboardQueryService:
        return SqlAlchemyDashboardQueryService(session)

Данный модуль не импортирует IUnitOfWork и не вызывает commit()/rollback().
"""
from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.interfaces.dashboard_query_service import IDashboardQueryService
from app.infrastructure.db.repositories.dashboard_repository import DashboardRepository


class SqlAlchemyDashboardQueryService(IDashboardQueryService):
    """Делегирует все вызовы DashboardRepository, скрывая его от внешнего мира."""

    def __init__(self, session: AsyncSession) -> None:
        self._repo = DashboardRepository(session)

    async def get_stats(self, owner_id: uuid.UUID) -> dict:
        return await self._repo.get_stats(owner_id)

    async def get_activity_last_7_days(self, owner_id: uuid.UUID) -> list[dict]:
        return await self._repo.get_activity_last_7_days(owner_id)

    async def get_attention_documents(
        self, owner_id: uuid.UUID, limit: int = 4
    ) -> list[dict]:
        return await self._repo.get_attention_documents(owner_id, limit)

    async def get_recent_documents(
        self, user_id: uuid.UUID, limit: int = 5
    ) -> list[dict]:
        return await self._repo.get_recent_documents(user_id, limit)

    async def upsert_open(
        self, user_id: uuid.UUID, document_id: uuid.UUID
    ) -> None:
        await self._repo.upsert_open(user_id, document_id)
