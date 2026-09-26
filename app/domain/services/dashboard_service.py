"""
DashboardService — оркестрирует агрегаты для GET /dashboard,
GET /documents/attention, GET /documents/recent и GET /dashboard/stats.

Архитектурные правила:
  - Зависит только от IDashboardQueryService (read-model порт) для агрегатов.
  - track_open делегируется через тот же IDashboardQueryService (он содержит upsert_open).
  - Нет импортов из app.infrastructure.* при выполнении.

CRIT-D1 (этот раунд):
  DashboardService переключён с IUnitOfWork на IDashboardQueryService.

STATS: get_extended_stats добавлен для GET /dashboard/stats.
  Оценка saved_hours: каждая принятая правка экономит AVG_MINUTES_PER_SUGGESTION минут;
  константа намеренно вынесена в сервис — продакт может скорректировать без
  изменения репозитория.
"""
from __future__ import annotations

import uuid

from app.api.schemas.dashboard import (
    DashboardResponse,
    DashboardStatsResponse,
    DayActivity,
    DocumentsByStatus,
)
from app.domain.interfaces.dashboard_query_service import IDashboardQueryService

# Среднее время (в минутах) на ручное применение одной правки.
# Используется для оценки «сэкономленных часов» на дашборде.
_AVG_MINUTES_PER_SUGGESTION: float = 3.0


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

    # ── Расширенная статистика (виджеты) ─────────────────────────────

    async def get_extended_stats(self, user_id: uuid.UUID) -> DashboardStatsResponse:
        """GET /dashboard/stats — данные для виджетов главной страницы.

        saved_hours рассчитывается по формуле:
          accepted_count * _AVG_MINUTES_PER_SUGGESTION / 60
        """
        raw = await self._qs.get_extended_stats(user_id)

        accepted: int = raw.get("accepted_count", 0)
        rejected: int = raw.get("rejected_count", 0)
        decided = accepted + rejected
        approved_percent = round(accepted / decided * 100, 1) if decided else 0.0
        saved_hours = round(accepted * _AVG_MINUTES_PER_SUGGESTION / 60, 1)

        return DashboardStatsResponse(
            saved_hours=saved_hours,
            approved_percent=approved_percent,
            documents_by_status=DocumentsByStatus(
                draft=raw.get("draft", 0),
                in_progress=raw.get("in_progress", 0),
                awaiting_approval=raw.get("awaiting_approval", 0),
                ready=raw.get("ready", 0),
                failed=raw.get("failed", 0),
            ),
            total_suggestions=raw.get("total_suggestions", 0),
            accepted_count=accepted,
            rejected_count=rejected,
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
