"""
DashboardService — оркестрирует агрегаты для GET /dashboard.

MYPY-FIX: все методы получили явные аннотации возврата.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import date, timedelta, timezone
from datetime import datetime as dt

from app.api.schemas.dashboard import DashboardResponse, TrendPoint
from app.domain.interfaces.dashboard_query_service import IDashboardQueryService


def _interpolate(
    dates: list[date],
    snap_by_date: dict[date, float],
    fallback: float,
) -> list[TrendPoint]:
    if not snap_by_date:
        return [TrendPoint(date=d.isoformat(), value=fallback) for d in dates]
    known = sorted(snap_by_date)
    first, last = known[0], known[-1]
    result: list[TrendPoint] = []
    for d in dates:
        if d in snap_by_date:
            v = snap_by_date[d]
        elif d < first:
            v = snap_by_date[first]
        elif d > last:
            v = snap_by_date[last]
        else:
            before = max(k for k in known if k < d)
            after = min(k for k in known if k > d)
            v0, v1 = snap_by_date[before], snap_by_date[after]
            t = (d - before).days / (after - before).days
            v = round(v0 + (v1 - v0) * t, 2)
        result.append(TrendPoint(date=d.isoformat(), value=v))
    return result


class DashboardService:
    def __init__(self, dashboard_qs: IDashboardQueryService) -> None:
        self._qs = dashboard_qs

    async def get_dashboard(self, user_id: uuid.UUID) -> DashboardResponse:
        stats, snapshots = await asyncio.gather(
            self._qs.get_stats(user_id),
            self._qs.get_trends(user_id, days=7),
        )
        total: int = stats["total"]
        ready: int = stats["ready"]
        awaiting: int = stats["awaiting"]
        relevance = round(ready / total * 100, 1) if total else 0.0
        today = dt.now(tz=timezone.utc).date()
        await self._qs.upsert_snapshot(
            owner_id=user_id,
            snapshot_date=today,
            total_count=total,
            awaiting_count=awaiting,
            relevance_percent=relevance,
        )
        snap_by_date: dict[date, dict] = {s["snapshot_date"]: s for s in snapshots}
        snap_by_date[today] = {
            "snapshot_date": today,
            "total_count": total,
            "awaiting_count": awaiting,
            "relevance_percent": relevance,
        }
        dates = [today - timedelta(days=i) for i in range(6, -1, -1)]
        return DashboardResponse(
            total_documents=total,
            awaiting_approval_count=awaiting,
            ready_count=ready,
            relevance_percent=relevance,
            total_trend=_interpolate(
                dates,
                {d: float(s["total_count"]) for d, s in snap_by_date.items()},
                float(total),
            ),
            awaiting_trend=_interpolate(
                dates,
                {d: float(s["awaiting_count"]) for d, s in snap_by_date.items()},
                float(awaiting),
            ),
            relevance_trend=_interpolate(
                dates,
                {d: s["relevance_percent"] for d, s in snap_by_date.items()},
                relevance,
            ),
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
        await self._qs.upsert_open(user_id, document_id)
