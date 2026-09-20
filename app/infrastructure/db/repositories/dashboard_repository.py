"""
DashboardRepository — реальные SQL-агрегаты для GET /dashboard.

Запросы:
  - total_documents           COUNT всех документов пользователя
  - awaiting_approval_count   COUNT документов в статусе AWAITING_APPROVAL
  - ready_count               COUNT документов в статусе READY
  - activity_last_7_days      GROUP BY date за последние 7 дней (из document_opens)
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.document_open import DocumentOpen
from app.infrastructure.db.models.enums import DocumentStatus
from app.infrastructure.db.models.project import Project


class DashboardRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Вспомогательный подзапрос: документы, принадлежащие пользователю
    # ------------------------------------------------------------------

    def _owned_docs_q(self, owner_id: uuid.UUID):
        """SELECT d FROM documents JOIN projects WHERE projects.owner_id = owner_id"""
        return (
            select(Document)
            .join(Project, Document.project_id == Project.id)
            .where(Project.owner_id == owner_id)
        )

    # ------------------------------------------------------------------
    # Агрегаты
    # ------------------------------------------------------------------

    async def get_total_documents(self, owner_id: uuid.UUID) -> int:
        q = select(func.count()).select_from(self._owned_docs_q(owner_id).subquery())
        return (await self._session.execute(q)).scalar_one()

    async def get_awaiting_approval_count(self, owner_id: uuid.UUID) -> int:
        base = self._owned_docs_q(owner_id).where(
            Document.status == DocumentStatus.AWAITING_APPROVAL
        )
        q = select(func.count()).select_from(base.subquery())
        return (await self._session.execute(q)).scalar_one()

    async def get_ready_count(self, owner_id: uuid.UUID) -> int:
        base = self._owned_docs_q(owner_id).where(
            Document.status == DocumentStatus.READY
        )
        q = select(func.count()).select_from(base.subquery())
        return (await self._session.execute(q)).scalar_one()

    async def get_activity_last_7_days(
        self, owner_id: uuid.UUID
    ) -> list[dict]:
        """Возвращает список {date: str, opens: int} за последние 7 дней."""
        since = datetime.now(tz=timezone.utc) - timedelta(days=6)
        # Приводим timestamp к дате в UTC
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
        # Заполняем нулями пропущенные дни
        result_map: dict[date, int] = {r.day.date(): r.opens for r in rows}
        today = datetime.now(tz=timezone.utc).date()
        return [
            {
                "date": (today - timedelta(days=i)).isoformat(),
                "opens": result_map.get(today - timedelta(days=i), 0),
            }
            for i in range(6, -1, -1)
        ]

    async def get_attention_documents(
        self, owner_id: uuid.UUID, limit: int = 4
    ) -> list[dict]:
        """
        Топ-N документов в статусе AWAITING_APPROVAL,
        отсортированных по pending_suggestions DESC.
        """
        from app.infrastructure.db.models.suggestion import Suggestion
        from app.infrastructure.db.models.enums import SuggestionStatus

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
            {
                "id": r.id,
                "title": r.name,
                "project_id": r.project_id,
                "project_name": r.project_name,
                "pending_suggestions": r.pending_suggestions,
                "updated_at": r.uploaded_at,
            }
            for r in rows
        ]

    async def get_recent_documents(
        self, user_id: uuid.UUID, limit: int = 5
    ) -> list[dict]:
        """5 последних открытых документов пользователя."""
        q = (
            select(
                Document.id,
                Document.name,
                Document.project_id,
                Project.name.label("project_name"),
                Document.status,
                DocumentOpen.last_opened_at,
            )
            .join(DocumentOpen, DocumentOpen.document_id == Document.id)
            .join(Project, Document.project_id == Project.id)
            .where(DocumentOpen.user_id == user_id)
            .order_by(DocumentOpen.last_opened_at.desc())
            .limit(limit)
        )
        rows = (await self._session.execute(q)).all()
        return [
            {
                "id": r.id,
                "title": r.name,
                "project_id": r.project_id,
                "project_name": r.project_name,
                "status": r.status.value if hasattr(r.status, "value") else r.status,
                "last_opened_at": r.last_opened_at,
            }
            for r in rows
        ]
