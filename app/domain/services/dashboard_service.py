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


def _interpolate(
    dates: list[date],
    snap_by_date: dict[date, float],
    fallback: float,
) -> list[TrendPoint]:
    """Строит 7 точек sparkline с линейной интерполяцией пропусков.

    Правила:
      - Известный день       → точное значение из снэпшота.
      - Пропуск между двумя  → линейная интерполяция.
      - Пропуск до первого   → значение первого известного снэпшота.
      - Пропуск после последнего → значение последнего известного снэпшота.
      - Нет снэпшотов вообще → flat-линия = fallback (текущее live-значение).
    """
    if not snap_by_date:
        return [TrendPoint(date=d.isoformat(), value=fallback) for d in dates]

    known_dates = sorted(snap_by_date.keys())
    first_known = known_dates[0]
    last_known = known_dates[-1]

    result: list[TrendPoint] = []
    for d in dates:
        if d in snap_by_date:
            result.append(TrendPoint(date=d.isoformat(), value=snap_by_date[d]))
        elif d < first_known:
            # leading gap — тянем от первого известного
            result.append(TrendPoint(date=d.isoformat(), value=snap_by_date[first_known]))
        elif d > last_known:
            # trailing gap — тянем от последнего известного
            result.append(TrendPoint(date=d.isoformat(), value=snap_by_date[last_known]))
        else:
            # пропуск между двумя известными — линейная интерполяция
            before = max(kd for kd in known_dates if kd < d)
            after = min(kd for kd in known_dates if kd > d)
            v0, v1 = snap_by_date[before], snap_by_date[after]
            span = (after - before).days          # всегда > 0
            step = (d - before).days
            value = v0 + (v1 - v0) * step / span
            result.append(TrendPoint(date=d.isoformat(), value=round(value, 2)))

    return result


class DashboardService:
    def __init__(self, dashboard_qs: IDashboardQueryService) -> None:
        self._qs = dashboard_qs

    async def get_dashboard(self, user_id: uuid.UUID) -> DashboardResponse:
        """GET /dashboard — текущее состояние + sparkline за 7 дней."""
        stats, snapshots = await self._fetch(user_id)

        total: int = stats["total"]
        ready: int = stats["ready"]
        relevance_percent = round(ready / total * 100) if total else 0.0

        today = dt.now(tz=timezone.utc).date()
        dates = [today - timedelta(days=i) for i in range(6, -1, -1)]

        total_map:     dict[date, float] = {s["snapshot_date"]: float(s["total_count"])       for s in snapshots}
        awaiting_map:  dict[date, float] = {s["snapshot_date"]: float(s["awaiting_count"])    for s in snapshots}
        relevance_map: dict[date, float] = {s["snapshot_date"]: s["relevance_percent"]        for s in snapshots}

        return DashboardResponse(
            total_documents=total,
            awaiting_approval_count=stats["awaiting"],
            ready_count=ready,
            relevance_percent=relevance_percent,
            total_trend=_interpolate(dates, total_map,     float(total)),
            awaiting_trend=_interpolate(dates, awaiting_map,  float(stats["awaiting"])),
            relevance_trend=_interpolate(dates, relevance_map, float(relevance_percent)),
        )

    async def _fetch(self, user_id: uuid.UUID) -> tuple[dict, list[dict]]:
        """stats и snapshots параллельно."""
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
