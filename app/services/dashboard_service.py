"""
DashboardService — оркестрирует агрегаты для GET /dashboard,
GET /documents/attention и GET /documents/recent.

Все stub-данные заменены реальными запросами через DashboardRepository.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.api.schemas.dashboard import DashboardResponse, DayActivity
from app.infrastructure.db.models.document_open import DocumentOpen
from app.infrastructure.db.repositories.dashboard_repository import DashboardRepository


class DashboardService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = DashboardRepository(session)

    # ------------------------------------------------------------------
    # GET /dashboard
    # ------------------------------------------------------------------

    async def get_dashboard(self, owner_id: uuid.UUID) -> DashboardResponse:
        total = await self._repo.get_total_documents(owner_id)
        awaiting = await self._repo.get_awaiting_approval_count(owner_id)
        ready = await self._repo.get_ready_count(owner_id)
        relevance_percent = round(ready / total * 100) if total else 0
        activity_rows = await self._repo.get_activity_last_7_days(owner_id)

        return DashboardResponse(
            total_documents=total,
            awaiting_approval_count=awaiting,
            ready_count=ready,
            relevance_percent=relevance_percent,
            activity_last_7_days=[
                DayActivity(date=r["date"], opens=r["opens"]) for r in activity_rows
            ],
        )

    # ------------------------------------------------------------------
    # GET /documents/attention
    # ------------------------------------------------------------------

    async def get_attention_documents(self, owner_id: uuid.UUID) -> list[dict]:
        return await self._repo.get_attention_documents(owner_id, limit=4)

    # ------------------------------------------------------------------
    # GET /documents/recent
    # ------------------------------------------------------------------

    async def get_recent_documents(self, user_id: uuid.UUID) -> list[dict]:
        return await self._repo.get_recent_documents(user_id, limit=5)

    # ------------------------------------------------------------------
    # POST /documents/{document_id}/open  — трекинг открытия
    # ------------------------------------------------------------------

    async def track_open(self, user_id: uuid.UUID, document_id: uuid.UUID) -> None:
        """
        UPSERT в document_opens:
        - если записи нет — INSERT
        - если запись есть — UPDATE last_opened_at = NOW()

        Используем PostgreSQL ON CONFLICT DO UPDATE для атомарности.
        """
        stmt = (
            pg_insert(DocumentOpen)
            .values(
                id=uuid.uuid4(),
                user_id=user_id,
                document_id=document_id,
                last_opened_at=datetime.now(tz=timezone.utc),
            )
            .on_conflict_do_update(
                constraint="uq_document_opens_user_document",
                set_={"last_opened_at": datetime.now(tz=timezone.utc)},
            )
        )
        await self._session.execute(stmt)
        await self._session.flush()
