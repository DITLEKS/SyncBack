"""
DashboardService — оркестрирует агрегаты для GET /dashboard,
GET /documents/attention и GET /documents/recent.

Архитектурные правила:
  - Зависит только от IDashboardQueryService (read-model порт) для агрегатов.
  - track_open делегируется через тот же IDashboardQueryService (он содержит upsert_open).
  - Нет импортов из app.infrastructure.* при выполнении.

CRIT-D1 (этот раунд):
  DashboardService переключён с IUnitOfWork на IDashboardQueryService.
  Ранее сервис обращался к self._uow.dashboard — которого НЕТ в IUnitOfWork
  (H-NEW-2 убрал dashboard из UoW), что давало AttributeError на каждый запрос
  к /dashboard. Теперь сервис принимает IDashboardQueryService напрямую.

  Пример инициализации (core/dependencies.py):
    def get_dashboard_service(
        dashboard_qs: IDashboardQueryService = Depends(get_dashboard_query_service),
    ) -> DashboardService:
        return DashboardService(dashboard_qs=dashboard_qs)
"""
from __future__ import annotations

import uuid

from app.api.schemas.dashboard import DashboardResponse, DayActivity
from app.domain.interfaces.dashboard_query_service import IDashboardQueryService


class DashboardService:
    def __init__(self, dashboard_qs: IDashboardQueryService) -> None:
        self._qs = dashboard_qs

    # ── Dashboard агрегаты ────────────────────────────────────────────

    async def get_dashboard(self, user_id: uuid.UUID) -> DashboardResponse:
        """CRIT-D1: используем IDashboardQueryService, не uow.dashboard."""
        stats = await self._qs.get_stats(user_id)
        activity_rows = await self._qs.get_activity_last_7_days(user_id)

        total = stats["total"]
        awaiting = stats["awaiting"]
        ready = stats["ready"]
        relevance_percent = round(ready / total * 100) if total else 0

        return DashboardResponse(
            total_documents=total,
            awaiting_approval_count=awaiting,
            ready_count=ready,
            relevance_percent=relevance_percent,
            activity_last_7_days=[
                DayActivity(date=r["date"], opens=r["opens"]) for r in activity_rows
            ],
        )

    # ── Требуют внимания ─────────────────────────────────────────────

    async def get_attention_documents(
        self, user_id: uuid.UUID, limit: int = 4
    ) -> list[dict]:
        return await self._qs.get_attention_documents(user_id, limit=limit)

    # ── Недавние документы ───────────────────────────────────────────

    async def get_recent_documents(
        self, user_id: uuid.UUID, limit: int = 5
    ) -> list[dict]:
        return await self._qs.get_recent_documents(user_id, limit=limit)

    # ── Трекинг открытия ─────────────────────────────────────────────

    async def track_open(
        self, user_id: uuid.UUID, document_id: uuid.UUID
    ) -> None:
        """CRIT-D1: upsert_open живёт в IDashboardQueryService, не в UoW."""
        await self._qs.upsert_open(user_id, document_id)
