"""
DashboardService — оркестрирует агрегаты для GET /dashboard,
GET /documents/attention, GET /documents/recent.

Архитектурные правила:
  - Зависит только от IDashboardQueryService (read-model порт).
  - Нет импортов из app.infrastructure.* при выполнении.

OPT-D8: track_open принимает project_id для проверки ownership.
"""
from __future__ import annotations

import uuid

from app.api.schemas.dashboard import (
    DashboardResponse,
    DayActivity,
)
from app.domain.interfaces.dashboard_query_service import IDashboardQueryService


class DashboardService:
    def __init__(self, dashboard_qs: IDashboardQueryService) -> None:
        self._qs = dashboard_qs

    async def get_dashboard(self, user_id: uuid.UUID) -> DashboardResponse:
        """GET /dashboard — данные для трёх виджетов и мини-графика."""
        stats, activity_rows = await self._qs.get_dashboard_data(user_id)

        total: int = stats["total"]
        ready: int = stats["ready"]
        relevance_percent = round(ready / total * 100) if total else 0

        return DashboardResponse(
            total_documents=total,
            awaiting_approval_count=stats["awaiting"],
            ready_count=ready,
            relevance_percent=relevance_percent,
            activity_last_7_days=[
                DayActivity(date=r["date"], opens=r["opens"]) for r in activity_rows
            ],
        )

    async def get_attention_documents(
        self, user_id: uuid.UUID, limit: int = 4
    ) -> list[dict]:
        return await self._qs.get_attention_documents(user_id, limit=limit)

    async def get_recent_documents(
        self, user_id: uuid.UUID, limit: int = 5
    ) -> list[dict]:
        return await self._qs.get_recent_documents(user_id, limit=limit)

    async def track_open(
        self,
        user_id: uuid.UUID,
        document_id: uuid.UUID,
        project_id: uuid.UUID | None = None,
    ) -> None:
        await self._qs.upsert_open(user_id, document_id, project_id=project_id)
