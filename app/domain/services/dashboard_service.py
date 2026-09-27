"""
DashboardService — оркестрирует агрегаты для GET /dashboard,
GET /documents/attention, GET /documents/recent.

Архитектурные правила:
  - Зависит только от IDashboardQueryService (read-model порт).
  - Нет импортов из app.infrastructure.* при выполнении.

OPT-D1: get_dashboard объединяет базовые агрегаты + расширенную статистику
  в один вызов к query-service. get_extended_stats удалён.
OPT-D8: track_open принимает project_id для проверки ownership.
"""
from __future__ import annotations

import uuid

from app.api.schemas.dashboard import (
    DashboardResponse,
    DayActivity,
    DocumentsByStatus,
)
from app.domain.interfaces.dashboard_query_service import IDashboardQueryService

_AVG_MINUTES_PER_SUGGESTION: float = 3.0


class DashboardService:
    def __init__(self, dashboard_qs: IDashboardQueryService) -> None:
        self._qs = dashboard_qs

    async def get_dashboard(self, user_id: uuid.UUID) -> DashboardResponse:
        """Единый метод GET /dashboard.

        Запрашивает get_all_dashboard_data — один вызов к query-service,
        который параллельно выполняет все нужные SELECT-ы.
        """
        stats, activity_rows, extended = await self._qs.get_all_dashboard_data(user_id)

        total: int = stats["total"]
        awaiting: int = stats["awaiting"]
        ready: int = stats["ready"]
        relevance_percent = round(ready / total * 100) if total else 0

        accepted: int = extended.get("accepted_count", 0)
        rejected: int = extended.get("rejected_count", 0)
        decided = accepted + rejected
        approved_percent = round(accepted / decided * 100, 1) if decided else 0.0
        saved_hours = round(accepted * _AVG_MINUTES_PER_SUGGESTION / 60, 1)

        return DashboardResponse(
            total_documents=total,
            awaiting_approval_count=awaiting,
            ready_count=ready,
            relevance_percent=relevance_percent,
            activity_last_7_days=[
                DayActivity(date=r["date"], opens=r["opens"]) for r in activity_rows
            ],
            saved_hours=saved_hours,
            approved_percent=approved_percent,
            documents_by_status=DocumentsByStatus(
                draft=extended.get("draft", 0),
                in_progress=extended.get("in_progress", 0),
                awaiting_approval=extended.get("awaiting_approval", 0),
                ready=extended.get("ready", 0),
                failed=extended.get("failed", 0),
            ),
            total_suggestions=extended.get("total_suggestions", 0),
            accepted_count=accepted,
            rejected_count=rejected,
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
