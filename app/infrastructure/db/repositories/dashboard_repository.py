"""
DashboardRepository — реальные SQL-агрегаты для GET /dashboard.

CRIT-D4: класс теперь наследует IDashboardQueryService, чтобы
get_dashboard_service мог передать его как dashboard_qs= в DashboardService.

Запросы:
  - get_stats                 единый COUNT(*) FILTER вместо трёх отдельных (PERF-1)
  - activity_last_7_days      GROUP BY date за последние 7 дней (из document_opens)
  - get_attention_documents   Топ-N AWAITING_APPROVAL по pending_suggestions DESC + status
  - get_recent_documents      5 последних открытых + счётчики правок (total/resolved)
  - upsert_open               ON CONFLICT DO UPDATE last_opened_at

OPT-2: get_recent_documents переписан с коррелированных scalar_subquery()
  на один LEFT JOIN + COUNT FILTER. Итог: N+1 запросов → 1 запрос.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.interfaces.dashboard_query_service import IDashboardQueryService
from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.document_open import DocumentOpen
from app.infrastructure.db.models.enums import DocumentStatus, SuggestionStatus
from app.infrastructure.db.models.project import Project
from app.infrastructure.db.models.suggestion import Suggestion


class DashboardRepository(IDashboardQueryService):
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
    # PERF-1: единый агрегатный запрос вместо трёх отдельных COUNT
    # ------------------------------------------------------------------

    async def get_stats(self, owner_id: uuid.UUID) -> dict:
        """Возвращает {total, awaiting, ready} за один SQL-запрос."""
        q = (
            select(
                func.count().label("total"),
                func.count().filter(
                    Document.status == DocumentStatus.AWAITING_APPROVAL
                ).label("awaiting"),
                func.count().filter(
                    Document.status == DocumentStatus.READY
                ).label("ready"),
            )
            .select_from(
                select(Document.status)
                .join(Project, Document.project_id == Project.id)
                .where(Project.owner_id == owner_id)
                .subquery()
            )
        )
        row = (await self._session.execute(q)).one()
        return {"total": row.total, "awaiting": row.awaiting, "ready": row.ready}

    # ------------------------------------------------------------------
    # Activity
    # ------------------------------------------------------------------

    async def get_activity_last_7_days(
        self, owner_id: uuid.UUID
    ) -> list[dict]:
        """Возвращает [{date: str, opens: int}] за последние 7 дней."""
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
            {
                "date": (today - timedelta(days=i)).isoformat(),
                "opens": result_map.get(today - timedelta(days=i), 0),
            }
            for i in range(6, -1, -1)
        ]

    # ------------------------------------------------------------------
    # Attention documents
    # ------------------------------------------------------------------

    async def get_attention_documents(
        self, owner_id: uuid.UUID, limit: int = 4
    ) -> list[dict]:
        """Топ-N документов в AWAITING_APPROVAL, сортировка по pending_suggestions DESC.

        UI-fix: теперь возвращает поле status для цветного бейджа на плашке.
        """
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
                Document.status,
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
                "status": r.status.value if hasattr(r.status, "value") else r.status,
                "pending_suggestions": r.pending_suggestions,
                "updated_at": r.uploaded_at,
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Recent documents
    # ------------------------------------------------------------------

    async def get_recent_documents(
        self, user_id: uuid.UUID, limit: int = 5
    ) -> list[dict]:
        """N последних открытых документов пользователя.

        OPT-2: коррелированные scalar_subquery() (N×2 отдельных COUNT-запроса)
        заменены на LEFT JOIN + COUNT FILTER, итог: 1 запрос вместо N+1.
        """
        q = (
            select(
                Document.id,
                Document.name,
                Document.project_id,
                Project.name.label("project_name"),
                Document.status,
                DocumentOpen.last_opened_at,
                func.count(Suggestion.id).label("suggestions_total"),
                func.count(Suggestion.id).filter(
                    Suggestion.status.in_([
                        SuggestionStatus.ACCEPTED,
                        SuggestionStatus.REJECTED,
                    ])
                ).label("suggestions_resolved"),
            )
            .join(DocumentOpen, DocumentOpen.document_id == Document.id)
            .join(Project, Document.project_id == Project.id)
            .outerjoin(Suggestion, Suggestion.document_id == Document.id)
            .where(DocumentOpen.user_id == user_id)
            .group_by(
                Document.id,
                Document.name,
                Document.project_id,
                Project.name,
                Document.status,
                DocumentOpen.last_opened_at,
            )
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
                "suggestions_total": r.suggestions_total or 0,
                "suggestions_resolved": r.suggestions_resolved or 0,
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Track open
    # ------------------------------------------------------------------

    async def upsert_open(self, user_id: uuid.UUID, document_id: uuid.UUID) -> None:
        """Упсерт last_opened_at атомарно (PostgreSQL ON CONFLICT DO UPDATE)."""
        now = datetime.now(tz=timezone.utc)
        stmt = (
            pg_insert(DocumentOpen)
            .values(
                id=uuid.uuid4(),
                user_id=user_id,
                document_id=document_id,
                last_opened_at=now,
            )
            .on_conflict_do_update(
                constraint="uq_document_opens_user_document",
                set_={"last_opened_at": now},
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()
