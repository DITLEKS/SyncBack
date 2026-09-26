"""
DashboardService — оркестрирует агрегаты для GET /dashboard,
GET /documents/attention и GET /documents/recent.

Архитектурные правила:
  - Зависит только от IUnitOfWork (порт).
  - Нет импортов из app.infrastructure.* при выполнении.
  - PERF-1: все COUNT + activity — два запроса вместо четырёх (uow.dashboard).
"""
from __future__ import annotations

import uuid

from app.api.schemas.dashboard import DashboardResponse, DayActivity
from app.domain.interfaces.unit_of_work import IUnitOfWork


class DashboardService:
    def __init__(self, uow: IUnitOfWork) -> None:
        self._uow = uow

    # ── Dashboard агрегаты ————————————————————————————————————————————

    async def get_dashboard(self, user_id: uuid.UUID) -> DashboardResponse:
        """PERF-1: все COUNT-агрегаты + activity в двух запросах."""
        async with self._uow:
            stats = await self._uow.dashboard.get_stats(user_id)
            activity_rows = await self._uow.dashboard.get_activity_last_7_days(user_id)

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

    # ── Требуют внимания —————————————————————————————————————————————

    async def get_attention_documents(
        self, user_id: uuid.UUID, limit: int = 4
    ) -> list[dict]:
        async with self._uow:
            return await self._uow.dashboard.get_attention_documents(
                user_id, limit=limit
            )

    # ── Недавние документы ———————————————————————————————————————————

    async def get_recent_documents(
        self, user_id: uuid.UUID, limit: int = 5
    ) -> list[dict]:
        async with self._uow:
            return await self._uow.dashboard.get_recent_documents(
                user_id, limit=limit
            )

    # ── Трекинг открытия ————————————————————————————————————————————

    async def track_open(
        self, user_id: uuid.UUID, document_id: uuid.UUID
    ) -> None:
        async with self._uow:
            await self._uow.dashboard.upsert_open(user_id, document_id)
            await self._uow.commit()
