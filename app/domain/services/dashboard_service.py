"""
DashboardService — оркестрирует агрегаты для GET /dashboard,
GET /documents/attention и GET /documents/recent.

Сервис не выполняет прямых SQL-запросов: делегирует DashboardPort.

H2.2: убрана зависимость от app.api.schemas и конкретного DashboardRepository.
      Сервис принимает DashboardPort и возвращает plain-dict результаты;
      маппинг в Pydantic-схемы выполняется в роутере.

PERF-1: вместо 3 отдельных COUNT-запросов используется один агрегатный
        запрос с FILTER (в DashboardRepository.get_stats).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

from app.domain.ports.dashboard_port import DashboardPort


@dataclass(frozen=True)
class DashboardStats:
    """Агрегаты для экрана «Рабочее пространство»."""

    total_documents: int
    awaiting_approval_count: int
    ready_count: int
    relevance_percent: float
    activity_last_7_days: list[dict]


class DashboardService:
    """Dashboard service. Инжектируется через get_dashboard_service()."""

    def __init__(self, dashboard_repository: DashboardPort) -> None:
        self._repo = dashboard_repository

    # ── #1 Dashboard агрегаты ──────────────────────────────────────────

    async def get_dashboard(self, user_id: uuid.UUID) -> DashboardStats:
        """PERF-1: все COUNT-агрегаты + activity в двух запросах вместо четырёх."""
        stats = await self._repo.get_stats(user_id)
        total: int = stats["total"]
        awaiting: int = stats["awaiting"]
        ready: int = stats["ready"]
        relevance_percent = round(ready / total * 100, 1) if total else 0.0
        activity_rows = await self._repo.get_activity_last_7_days(user_id)
        return DashboardStats(
            total_documents=total,
            awaiting_approval_count=awaiting,
            ready_count=ready,
            relevance_percent=relevance_percent,
            activity_last_7_days=activity_rows,
        )

    # ── #2 Требуют внимания ────────────────────────────────────────────

    async def get_attention_documents(
        self, user_id: uuid.UUID, limit: int = 4
    ) -> list[dict]:
        return await self._repo.get_attention_documents(user_id, limit=limit)

    # ── #3 Недавние документы ──────────────────────────────────────────

    async def get_recent_documents(
        self, user_id: uuid.UUID, limit: int = 5
    ) -> list[dict]:
        return await self._repo.get_recent_documents(user_id, limit=limit)

    # ── Трекинг открытия ──────────────────────────────────────────────

    async def track_open(
        self, user_id: uuid.UUID, document_id: uuid.UUID
    ) -> None:
        """UPSERT last_opened_at через DashboardPort."""
        await self._repo.upsert_open(user_id, document_id)
