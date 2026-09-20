"""
Репозиторий документов.

ИСПРАВЛЕНО (code-review):
- C-2: list_all_for_user параметры переименованы status_filter→status, name_query→search
- P-2: COUNT без subquery — прямые WHERE-условия вместо оборачивания в subquery()
- Q-3: метод save() переименован в create() для согласованности с IDocumentRepository
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Literal

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.interfaces.repository_interfaces import IDocumentRepository
from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.enums import DocumentStatus

if TYPE_CHECKING:
    pass


class DocumentRepository(IDocumentRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, document_id: uuid.UUID) -> Document | None:
        return await self._session.get(Document, document_id)

    async def list_by_project(
        self, project_id: uuid.UUID, limit: int, offset: int
    ) -> list[Document]:
        stmt = (
            select(Document)
            .where(Document.project_id == project_id)
            .order_by(Document.uploaded_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_by_project(self, project_id: uuid.UUID) -> int:
        stmt = select(func.count()).select_from(Document).where(Document.project_id == project_id)
        result = await self._session.execute(stmt)
        return result.scalar_one()

    # Q-3: create() вместо save() — основной метод создания документа
    async def create(self, document: Document) -> Document:
        self._session.add(document)
        await self._session.flush()
        await self._session.refresh(document)
        return document

    # Алиас для обратной совместимости (убрать после рефакторинга всех вызывателей)
    async def save(self, document: Document) -> Document:
        return await self.create(document)

    async def update_status(
        self, document: Document, new_status: DocumentStatus
    ) -> Document:
        document.status = new_status
        self._session.add(document)
        await self._session.flush()
        await self._session.refresh(document)
        return document

    async def update_current_job(
        self, document: Document, job_id: uuid.UUID | None
    ) -> Document:
        document.current_analysis_job_id = job_id
        self._session.add(document)
        await self._session.flush()
        await self._session.refresh(document)
        return document

    async def delete(self, document: Document) -> None:
        await self._session.delete(document)
        await self._session.flush()

    async def list_all_for_user(
        self,
        user_id: uuid.UUID,
        limit: int,
        offset: int,
        status: DocumentStatus | None = None,
        search: str | None = None,
    ) -> tuple[list[Document], int]:
        """Глобальный список документов пользователя через проекты.

        P-2: COUNT строится с теми же WHERE-условиями напрямую,
        без оборачивания основного запроса в subquery().
        C-2: параметры status= и search= (было status_filter=, name_query=).
        """
        from app.infrastructure.db.models.project import Project

        base_where = [Project.owner_id == user_id]
        if status is not None:
            base_where.append(Document.status == status)
        if search:
            base_where.append(Document.name.ilike(f"%{search}%"))

        # P-2: прямой COUNT с теми же условиями — без subquery
        count_stmt = (
            select(func.count(Document.id))
            .select_from(Document)
            .join(Project, Document.project_id == Project.id)
            .where(*base_where)
        )
        total = (await self._session.execute(count_stmt)).scalar_one()

        stmt = (
            select(Document)
            .join(Project, Document.project_id == Project.id)
            .where(*base_where)
            .order_by(Document.uploaded_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all()), total

    async def list_analyzable_for_project(self, project_id: uuid.UUID) -> list[Document]:
        """Документы в статусах DRAFT / AWAITING_APPROVAL для bulk-запуска анализа."""
        stmt = (
            select(Document)
            .where(
                Document.project_id == project_id,
                Document.status.in_((DocumentStatus.DRAFT, DocumentStatus.AWAITING_APPROVAL)),
            )
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def increment_review_version(self, document: Document) -> Document:
        """Атомарный UPDATE review_version += 1 через UPDATE ... RETURNING."""
        stmt = (
            update(Document)
            .where(Document.id == document.id)
            .values(review_version=Document.review_version + 1)
            .returning(Document)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one()

    # Алиас для совместимости с вызовами bump_review_version в suggestion_service
    async def bump_review_version(self, document: Document) -> Document:
        return await self.increment_review_version(document)
