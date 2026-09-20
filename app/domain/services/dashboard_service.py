"""
Dashboard service — агрегирует данные для GET /dashboard.

ИСПРАВЛЕНО (code-review):
- A-3/A-4: DashboardData, AttentionItem, RecentItem, DayActivityData
           перенесены в domain/interfaces/dashboard_types.py.
           Здесь оставлены re-export алиасы для обратной совместимости
           с существующими импортами в роутерах.
"""
from __future__ import annotations

import uuid

# Re-export из domain/interfaces/dashboard_types для обратной совместимости.
# Роутеры и тесты, импортирующие эти типы из dashboard_service,
# продолжат работать без изменений.
from app.domain.interfaces.dashboard_types import (  # noqa: F401
    AttentionItem,
    DashboardData,
    DayActivityData,
    RecentItem,
)
from app.infrastructure.db.repositories.dashboard_repository import DashboardRepository
from app.infrastructure.db.repositories.document_open_repository import DocumentOpenRepository


class DashboardService:
    def __init__(
        self,
        dashboard_repository: DashboardRepository,
        document_open_repository: DocumentOpenRepository,
    ) -> None:
        self._dashboard = dashboard_repository
        self._opens = document_open_repository

    async def get_dashboard(self, owner_id: uuid.UUID) -> DashboardData:
        return await self._dashboard.get_dashboard_aggregates(owner_id)

    async def get_attention_documents(
        self, owner_id: uuid.UUID, limit: int = 4
    ) -> list[AttentionItem]:
        return await self._dashboard.get_attention_documents(owner_id, limit=limit)

    async def get_recent_documents(
        self, user_id: uuid.UUID, limit: int = 5
    ) -> list[RecentItem]:
        return await self._opens.get_recent_for_user(user_id, limit=limit)

    async def record_open(
        self, user_id: uuid.UUID, document_id: uuid.UUID
    ) -> None:
        await self._opens.upsert_open(user_id, document_id)
