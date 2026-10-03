"""
SqlAlchemyDashboardQueryService — инфраструктурная реализация
IDashboardQueryService. Делегирует все вызовы DashboardRepository.

L-C: не импортирует IUnitOfWork и не вызывает commit()/rollback().
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.interfaces.dashboard_query_service import IDashboardQueryService
from app.infrastructure.db.repositories.dashboard_repository import DashboardRepository


class SqlAlchemyDashboardQueryService(IDashboardQueryService):
    def __init__(self, session: AsyncSession) -> None:
        self._repo = DashboardRepository(session)

    async def get_stats(self, owner_id: uuid.UUID) -> dict:
        return await self._repo.get_stats(owner_id)

    async def get_trends(self, owner_id: uuid.UUID, days: int = 7) -> list[dict]:
        return await self._repo.get_trends(owner_id, days)

    async def upsert_snapshot(
        self,
        owner_id: uuid.UUID,
        snapshot_date: date,
        total_count: int,
        awaiting_count: int,
        relevance_percent: float,
    ) -> None:
        await self._repo.upsert_snapshot(
            owner_id, snapshot_date, total_count, awaiting_count, relevance_percent
        )

    async def get_attention_documents(self, owner_id: uuid.UUID, limit: int = 4) -> list[dict]:
        return await self._repo.get_attention_documents(owner_id, limit)

    async def get_recent_documents(self, user_id: uuid.UUID, limit: int = 5) -> list[dict]:
        return await self._repo.get_recent_documents(user_id, limit)

    async def upsert_open(self, user_id: uuid.UUID, document_id: uuid.UUID) -> None:
        await self._repo.upsert_open(user_id, document_id)
