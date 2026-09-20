"""
DocumentOpenRepository — CRUD для таблицы document_opens.

Таблица создана миграцией 0010_document_opens.py.
Хранит последнее открытие документа пользователем (upsert по unique (user_id, document_id)).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.services.dashboard_service import RecentItem
from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.document_open import DocumentOpen
from app.infrastructure.db.models.project import Project


class DocumentOpenRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_open(
        self, user_id: uuid.UUID, document_id: uuid.UUID
    ) -> None:
        """INSERT … ON CONFLICT DO UPDATE last_opened_at = now().

        Использует PostgreSQL UPSERT (pg_insert) для атомарного обновления.
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
        await self._session.commit()

    async def get_recent_for_user(
        self, user_id: uuid.UUID, limit: int = 5
    ) -> list[RecentItem]:
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
            RecentItem(
                id=r.id,
                name=r.name,
                project_id=r.project_id,
                project_name=r.project_name,
                status=r.status.value if hasattr(r.status, "value") else r.status,
                last_opened_at=r.last_opened_at,
            )
            for r in rows
        ]
