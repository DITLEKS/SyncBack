"""
DashboardService — оркестрирует агрегаты для GET /dashboard,
GET /documents/attention и GET /documents/recent.

Сервис не выполняет прямых SQL-запросов: делегирует DashboardRepository.

PERF-1: вместо 3 отдельных COUNT-запросов используется один агрегатный запрос
с FILTER (в DashboardRepository.get_stats), сокращая 3 RTT до 1.
"""
from __future__ import annotations

import uuid

from app.api.schemas.dashboard import DashboardResponse, DayActivity
from app.infrastructure.db.repositories.dashboard_repository import DashboardRepository


class DashboardService:
    """Dashboard service. Инжектируется через get_dashboard_service()."""

    def __init__(self, dashboard_repository: DashboardRepository) -> None:
        self._repo = dashboard_repository

    # ── #1 Dashboard агрегаты ——————————————————————————————————————————

    async def get_dashboard(self, user_id: uuid.UUID) -> DashboardResponse:
        """PERF-1: все COUNT-агрегаты + activity в двух запросах вместо четырёх."""
        stats = await self._repo.get_stats(user_id)
        total = stats["total"]
        awaiting = stats["awaiting"]
        ready = stats["ready"]
        relevance_percent = round(ready / total * 100) if total else 0
        activity_rows = await self._repo.get_activity_last_7_days(user_id)
        return DashboardResponse(
            total_documents=total,
            awaiting_approval_count=awaiting,
            ready_count=ready,
            relevance_percent=relevance_percent,
            activity_last_7_days=[
                DayActivity(date=r["date"], opens=r["opens"]) for r in activity_rows
            ],
        )

    # ── #2 Требуют внимания ———————————————————————————————————————————

    async def get_attention_documents(
        self, user_id: uuid.UUID, limit: int = 4
    ) -> list[dict]:
        return await self._repo.get_attention_documents(user_id, limit=limit)

    # ── #3 Недавние документы ————————————————————————————————————————

    async def get_recent_documents(
        self, user_id: uuid.UUID, limit: int = 5
    ) -> list[dict]:
        return await self._repo.get_recent_documents(user_id, limit=limit)

    # ── Трекинг открытия ——————————————————————————————————————————————

    async def track_open(self, user_id: uuid.UUID, document_id: uuid.UUID) -> None:
        """UPSERT last_opened_at через DashboardRepository."""
        await self._repo.upsert_open(user_id, document_id)
