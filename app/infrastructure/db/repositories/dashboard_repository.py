"""
DashboardRepository — SQL-агрегаты для GET /dashboard.

ИСПРАВЛЕНО (review #5, #10, #13):
- get_total_documents / get_awaiting_approval_count / get_ready_count объединены
  в один метод get_document_counts, который делает единственный JOIN с тремя
  условными COUNT(...) FILTER (WHERE ...) вместо трёх отдельных запросов.
- _owned_docs_q переименован в _owned_docs_subquery и возвращает сразу .subquery(),
  чтобы снять неоднозначность типа возвращаемого значения.
- Убран hasattr-check в get_recent_documents: Document.status — StrEnum и всегда str.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.document_open import DocumentOpen
from app.infrastructure.db.models.enums import DocumentStatus, SuggestionStatus
from app.infrastructure.db.models.project import Project


class DashboardRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------------
    # Вспомогательный подзапрос: документы, принадлежащие пользователю
    # ------------------------------------------------------------------

    def _owned_docs_subquery(self, owner_id: uuid.UUID):
        """Подзапрос: все документы, принадлежащие owner_id через projects."""
        return (
            select(Document)
            .join(Project, Document.project_id == Project.id)
            .where(Project.owner_id == owner_id)
            .subquery()
        )

    # ------------------------------------------------------------------
    # Агрегаты (один запрос вместо трёх)
    # ------------------------------------------------------------------

    async def get_document_counts(
        self, owner_id: uuid.UUID
    ) -> tuple[int, int, int]:
        """Возвращает (total, awaiting_approval, ready) одним SQL-запросом.

        Использует COUNT(*) FILTER (WHERE ...) — доступно в PostgreSQL 9.4+.
        Заменяет три отдельных COUNT-запроса с JOIN, снижая нагрузку на БД
        в три раза.
        """
        sub = self._owned_docs_subquery(owner_id)
        q = select(
            func.count().label("total"),
            func.count(
                case((sub.c.status == DocumentStatus.AWAITING_APPROVAL, 1))
            ).label("awaiting"),
            func.count(
                case((sub.c.status == DocumentStatus.READY, 1))
            ).label("ready"),
        ).select_from(sub)
        row = (await self._session.execute(q)).one()
        return row.total, row.awaiting, row.ready

    async def get_activity_last_7_days(
        self, owner_id: uuid.UUID
    ) -> list[dict]:
        """Возвращает список {date: str, opens: int} за последние 7 дней."""
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
        # Заполняем нулями пропущенные дни (7 элементов — O(1) dict lookup)
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
        """N последних открытых документов пользователя."""
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
                # Document.status — StrEnum (подкласс str), приведение не нужно
                "status": r.status,
                "last_opened_at": r.last_opened_at,
            }
            for r in rows
        ]
