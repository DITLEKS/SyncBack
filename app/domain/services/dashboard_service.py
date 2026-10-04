"""
DashboardService — агрегаты рабочего пространства: сводка с трендами,
документы «требуют внимания», недавно открытые.

Зависит только от IDashboardQueryService (read-model порт); результат
возвращает доменными структурами, HTTP-схемы собирает роутер.

Снэпшот за сегодня пишется при каждом запросе сводки — фоновый
джоб не нужен. ON CONFLICT DO UPDATE гарантирует идемпотентность.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, timedelta
from datetime import datetime as dt

from app.domain.interfaces.dashboard_query_service import IDashboardQueryService


@dataclass(frozen=True)
class TrendPoint:
    """Точка sparkline: дата в ISO 8601 и значение на этот день."""

    date: str
    value: float


@dataclass(frozen=True)
class DashboardSummary:
    total_documents: int
    awaiting_approval_count: int
    ready_count: int
    relevance_percent: float
    total_trend: list[TrendPoint]
    awaiting_trend: list[TrendPoint]
    relevance_trend: list[TrendPoint]


def _interpolate(
    dates: list[date],
    snap_by_date: dict[date, float],
    fallback: float,
) -> list[TrendPoint]:
    """Строит 7 точек sparkline с линейной интерполяцией пропусков.

    - Известный день         → точное значение из снэпшота.
    - Пропуск между двумя    → линейная интерполяция.
    - Пропуск до первого     → значение первого известного.
    - Пропуск после последнего → значение последнего известного.
    - Нет снэпшотов вообще   → flat = fallback.
    """
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

    async def get_dashboard(self, user_id: uuid.UUID) -> DashboardSummary:
        """Сводка рабочего пространства с трендами за 7 дней.

        Читает текущую статистику и снэпшоты, записывает снэпшот за сегодня
        (идемпотентный upsert) и строит тренды с интерполяцией пропусков.
        Запросы идут последовательно: read-model работает на одной AsyncSession,
        которую нельзя использовать конкурентно.
        """
        stats = await self._qs.get_stats(user_id)
        snapshots = await self._qs.get_trends(user_id, days=7)

        total: int = stats["total"]
        ready: int = stats["ready"]
        awaiting: int = stats["awaiting"]
        relevance = round(ready / total * 100, 1) if total else 0.0
        today = dt.now(tz=UTC).date()

        # — записываем снэпшот сегодня: ON CONFLICT DO UPDATE, идемпотентно
        await self._qs.upsert_snapshot(
            owner_id=user_id,
            snapshot_date=today,
            total_count=total,
            awaiting_count=awaiting,
            relevance_percent=relevance,
        )

        # — подмерживаем локальный список свежезаписанным снэпшотом
        snap_by_date: dict[date, dict] = {s["snapshot_date"]: s for s in snapshots}
        snap_by_date[today] = {
            "snapshot_date": today,
            "total_count": total,
            "awaiting_count": awaiting,
            "relevance_percent": relevance,
        }

        dates = [today - timedelta(days=i) for i in range(6, -1, -1)]

        return DashboardSummary(
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

    async def get_attention_documents(self, user_id: uuid.UUID, limit: int = 4) -> list[dict]:
        return await self._qs.get_attention_documents(user_id, limit=limit)

    async def get_recent_documents(self, user_id: uuid.UUID, limit: int = 5) -> list[dict]:
        return await self._qs.get_recent_documents(user_id, limit=limit)

    async def track_open(
        self,
        user_id: uuid.UUID,
        document_id: uuid.UUID,
        project_id: uuid.UUID | None = None,
    ) -> None:
        await self._qs.upsert_open(user_id, document_id)
