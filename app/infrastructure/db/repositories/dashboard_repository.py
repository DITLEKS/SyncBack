"""
DashboardRepository — реальные SQL-агрегаты для GET /dashboard.

refactor(#18): методы возвращают типизированные dataclass-объекты
               (DashboardData, AttentionItem) вместо list[dict].
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.services.dashboard_service import (
    AttentionItem,
    DashboardData,
    DayActivityData,
)
from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.document_open import DocumentOpen
from app.infrastructure.db.models.enums import DocumentStatus
from app.infrastructure.db.models.project import Project


class DashboardRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Агрегированный запрос — один вызов вместо трёх (#8 opt)
    # ------------------------------------------------------------------

    async def get_dashboard_aggregates(self, owner_id: uuid.UUID) -> DashboardData:
        """Возвращает все агрегаты дашборда одним запросом.

        Использует conditional COUNT (FILTER WHERE) для total / awaiting / ready
        — один SELECT вместо трёх.
        """
        q = (
            select(
                func.count(Document.id).label("total"),
                func.count(Document.id)
                .filter(Document.status == DocumentStatus.AWAITING_APPROVAL)
                .label("awaiting"),
                func.count(Document.id)
                .filter(Document.status == DocumentStatus.READY)
                .label("ready"),
            )
            .select_from(Document)
            .join(Project, Document.project_id == Project.id)
            .where(Project.owner_id == owner_id)
        )
        row = (await self._session.execute(q)).one()
        total = row.total or 0
        ready = row.ready or 0
        awaiting = row.awaiting or 0
        relevance = round((ready / total * 100), 1) if total > 0 else 0.0

        activity = await self._get_activity_last_7_days(owner_id)

        return DashboardData(
            total_documents=total,
            awaiting_approval_count=awaiting,
            ready_count=ready,
            relevance_percent=relevance,
            activity_last_7_days=activity,
        )

    async def _get_activity_last_7_days(
        self, owner_id: uuid.UUID
    ) -> list[DayActivityData]:
        """Активность за последние 7 дней из document_opens."""
        since = datetime.now(tz=timezone.utc) - timedelta(days=6)
        day_col = func.date_trunc("day", DocumentOpen.last_opened_at).label("day")
        q = (
            select(day_col, func.count().label("opens"))
            .where(
                DocumentOpen.user_id == owner_id,
                DocumentOpen.last_opened_at >= since,
            )
            .group_by(day_col)
            .order_by(day_col)
        )
        rows = (await self._session.execute(q)).all()
        result_map: dict[date, int] = {r.day.date(): r.opens for r in rows}
        today = datetime.now(tz=timezone.utc).date()
        return [
            DayActivityData(
                date=(today - timedelta(days=i)).isoformat(),
                analyzed=result_map.get(today - timedelta(days=i), 0),
            )
            for i in range(6, -1, -1)
        ]

    async def get_attention_documents(
        self, owner_id: uuid.UUID, limit: int = 4
    ) -> list[AttentionItem]:
        """Топ-N документов в awaiting_approval, по pending_suggestions DESC."""
        from app.infrastructure.db.models.enums import SuggestionStatus
        from app.infrastructure.db.models.suggestion import Suggestion

        pending_count = (
            select(func.count(Suggestion.id))
            .where(
                Suggestion.document_id == Document.id,
                Suggestion.status == SuggestionStatus.PENDING,
            )
            .correlate(Document)
            .scalar_subquery()
        )
        q = (
            select(
                Document.id,
                Document.name,
                Document.project_id,
                Project.name.label("project_name"),
                pending_count.label("pending_suggestions"),
                Document.uploaded_at,
            )
            .join(Project, Document.project_id == Project.id)
            .where(
                Project.owner_id == owner_id,
                Document.status == DocumentStatus.AWAITING_APPROVAL,
            )
            .order_by(pending_count.desc())
            .limit(limit)
        )
        rows = (await self._session.execute(q)).all()
        return [
            AttentionItem(
                id=r.id,
                name=r.name,
                project_id=r.project_id,
                project_name=r.project_name,
                pending_suggestions=r.pending_suggestions,
                updated_at=r.uploaded_at,
            )
            for r in rows
        ]
