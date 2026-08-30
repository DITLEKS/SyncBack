"""
Репозиторий документов.

ИСПРАВЛЕНО: list_by_project теперь принимает limit/offset, добавлен count_by_project.
"""

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.source import Source


class DocumentRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(self, document: Document) -> Document:
        self._session.add(document)
        await self._session.commit()
        await self._session.refresh(document)
        return document

    async def get_by_id(self, document_id: uuid.UUID) -> Document | None:
        return await self._session.get(Document, document_id)

    async def list_by_project(self, project_id: uuid.UUID, limit: int, offset: int) -> list[Document]:
        result = await self._session.execute(
            select(Document)
            .where(Document.project_id == project_id)
            .order_by(Document.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def count_by_project(self, project_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.count()).select_from(Document).where(Document.project_id == project_id)
        )
        return result.scalar_one()

    async def attach_sources(self, document: Document, sources: list[Source]) -> Document:
        # Загружаем текущие источники документа в асинхронном контексте, чтобы
        # избежать lazy-load вне greenlet_spawn.
        await self._session.refresh(document, ['sources'])
        document.sources = list({s.id: s for s in (document.sources + sources)}.values())
        await self._session.commit()
        await self._session.refresh(document, ['sources'])
        return document
