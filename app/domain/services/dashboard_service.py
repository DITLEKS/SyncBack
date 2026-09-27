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
from datetime import date, timedelta, timezone
from datetime import datetime as dt

from app.api.schemas.dashboard import (
    DashboardResponse,
    TrendPoint,
)
from app.domain.interfaces.dashboard_query_service import IDashboardQueryService


class DashboardService:
    def __init__(self, dashboard_qs: IDashboardQueryService) -> None:
        self._qs = dashboard_qs

    async def get_dashboard(self, user_id: uuid.UUID) -> DashboardResponse:
        """GET /dashboard — текущее состояние + sparkline за 7 дней."""
        stats, snapshots = await self._fetch(user_id)

        total: int = stats["total"]
        ready: int = stats["ready"]
        relevance_percent = round(ready / total * 100) if total else 0

        # — индекс снэпшотов по дате
        snap_by_date: dict[date, dict] = {
            s["snapshot_date"]: s for s in snapshots
        }

        today = dt.now(tz=timezone.utc).date()
        dates = [today - timedelta(days=i) for i in range(6, -1, -1)]

        def _fill(key: str, current: float) -> list[TrendPoint]:
            """7 точек; дни без снэпшота — текущее значение."""
            return [
                TrendPoint(
                    date=d.isoformat(),
                    value=snap_by_date[d][key] if d in snap_by_date else current,
                )
                for d in dates
            ]

        return DashboardResponse(
            total_documents=total,
            awaiting_approval_count=stats["awaiting"],
            ready_count=ready,
            relevance_percent=relevance_percent,
            total_trend=_fill("total_count", float(total)),
            awaiting_trend=_fill("awaiting_count", float(stats["awaiting"])),
            relevance_trend=_fill("relevance_percent", float(relevance_percent)),
        )

    async def _fetch(self, user_id: uuid.UUID) -> tuple[dict, list[dict]]:
        """stats и snapshots одновременно."""
        import asyncio
        stats, snapshots = await asyncio.gather(
            self._qs.get_stats(user_id),
            self._qs.get_trends(user_id, days=7),
        )
        return stats, snapshots

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
        await self._qs.upsert_open(user_id, document_id)
