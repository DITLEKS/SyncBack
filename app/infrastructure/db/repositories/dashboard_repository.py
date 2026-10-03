"""
DashboardRepository — SQL-агрегаты для GET /dashboard.

Запросы:
  get_stats              — единый COUNT(*) FILTER (базовая статистика)
  get_trends             — SELECT из dashboard_snapshots за N дней
  upsert_snapshot        — ON CONFLICT DO UPDATE для фонового джоба
  get_attention_documents— Топ-N AWAITING_APPROVAL по pending_suggestions DESC
  get_recent_documents   — 5 последних открытых + счётчики правок
  upsert_open            — ON CONFLICT DO UPDATE last_opened_at
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.interfaces.dashboard_query_service import IDashboardQueryService
from app.infrastructure.db.models.dashboard_snapshot import DashboardSnapshot
from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.document_open import DocumentOpen
from app.infrastructure.db.models.enums import DocumentStatus, SuggestionStatus
from app.infrastructure.db.models.project import Project
from app.infrastructure.db.models.suggestion import Suggestion


class DashboardRepository(IDashboardQueryService):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── базовая статистика ──────────────────────────────────────────────────────

    async def get_stats(self, owner_id: uuid.UUID) -> dict:
        q = select(
            func.count().label("total"),
            func.count()
            .filter(Document.status == DocumentStatus.AWAITING_APPROVAL)
            .label("awaiting"),
            func.count().filter(Document.status == DocumentStatus.READY).label("ready"),
        ).select_from(
            select(Document.status)
            .join(Project, Document.project_id == Project.id)
            .where(Project.owner_id == owner_id)
            .subquery()
        )
        row = (await self._session.execute(q)).one()
        return {"total": row.total, "awaiting": row.awaiting, "ready": row.ready}

    # ── снэпшоты / тренды ─────────────────────────────────────────────────────

    async def get_trends(self, owner_id: uuid.UUID, days: int = 7) -> list[dict]:
        """SELECT снэпшоты за последние `days` дней (today-days+1 … today)."""
        today = datetime.now(tz=UTC).date()
        since = today - timedelta(days=days - 1)
        q = (
            select(
                DashboardSnapshot.snapshot_date,
                DashboardSnapshot.total_count,
                DashboardSnapshot.awaiting_count,
                DashboardSnapshot.relevance_percent,
            )
            .where(
                DashboardSnapshot.owner_id == owner_id,
                DashboardSnapshot.snapshot_date >= since,
            )
            .order_by(DashboardSnapshot.snapshot_date)
        )
        rows = (await self._session.execute(q)).all()
        return [
            {
                "snapshot_date": r.snapshot_date,
                "total_count": r.total_count,
                "awaiting_count": r.awaiting_count,
                "relevance_percent": r.relevance_percent,
            }
            for r in rows
        ]

    async def upsert_snapshot(
        self,
        owner_id: uuid.UUID,
        snapshot_date: date,
        total_count: int,
        awaiting_count: int,
        relevance_percent: float,
    ) -> None:
        """ON CONFLICT (owner_id, snapshot_date) DO UPDATE."""
        stmt = (
            pg_insert(DashboardSnapshot)
            .values(
                owner_id=owner_id,
                snapshot_date=snapshot_date,
                total_count=total_count,
                awaiting_count=awaiting_count,
                relevance_percent=relevance_percent,
            )
            .on_conflict_do_update(
                constraint="uq_dashboard_snapshot_owner_date",
                set_={
                    "total_count": total_count,
                    "awaiting_count": awaiting_count,
                    "relevance_percent": relevance_percent,
                },
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()

    # ── внимание / последние документы ──────────────────────────────────────

    async def get_attention_documents(self, owner_id: uuid.UUID, limit: int = 4) -> list[dict]:
        q = (
            select(
                Document.id,
                Document.name,
                Document.project_id,
                Document.status,
                Document.uploaded_at,
                Project.name.label("project_name"),
                func.count(Suggestion.id)
                .filter(Suggestion.status == SuggestionStatus.PENDING)
                .label("pending_suggestions"),
            )
            .join(Project, Document.project_id == Project.id)
            .outerjoin(Suggestion, Suggestion.document_id == Document.id)
            .where(
                Project.owner_id == owner_id,
                Document.status == DocumentStatus.AWAITING_APPROVAL,
            )
            .group_by(
                Document.id,
                Document.name,
                Document.project_id,
                Document.status,
                Document.uploaded_at,
                Project.name,
            )
            .order_by(
                func.count(Suggestion.id)
                .filter(Suggestion.status == SuggestionStatus.PENDING)
                .desc()
            )
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

    async def get_recent_documents(self, user_id: uuid.UUID, limit: int = 5) -> list[dict]:
        q = (
            select(
                Document.id,
                Document.name,
                Document.project_id,
                Project.name.label("project_name"),
                Document.status,
                DocumentOpen.last_opened_at,
                func.count(Suggestion.id).label("suggestions_total"),
                func.count(Suggestion.id)
                .filter(
                    Suggestion.status.in_(
                        [
                            SuggestionStatus.ACCEPTED,
                            SuggestionStatus.REJECTED,
                        ]
                    )
                )
                .label("suggestions_resolved"),
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

    async def upsert_open(self, user_id: uuid.UUID, document_id: uuid.UUID) -> None:
        now = datetime.now(tz=UTC)
        stmt = (
            pg_insert(DocumentOpen)
            .values(user_id=user_id, document_id=document_id, last_opened_at=now)
            .on_conflict_do_update(
                constraint="uq_document_opens_user_document",
                set_={"last_opened_at": now},
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()
