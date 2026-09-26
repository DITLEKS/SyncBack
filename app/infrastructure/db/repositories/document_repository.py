"""
Репозиторий документов.

ДОБАВЛЕНО (P0-2): increment_review_version — атомарный UPDATE review_version += 1,
возвращает обновлённый Document. Используется в SuggestionService.atomic_review_save.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.enums import DocumentStatus

if TYPE_CHECKING:
    pass


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, document_id: uuid.UUID) -> Document | None:
        result = await self._session.get(Document, document_id)
        return result

    async def list_by_project(self, project_id: uuid.UUID, limit: int, offset: int) -> list[Document]:
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

    async def save(self, document: Document) -> Document:
        self._session.add(document)
        await self._session.flush()
        await self._session.refresh(document)
        return document

    async def update_status(self, document: Document, new_status: DocumentStatus) -> Document:
        document.status = new_status
        self._session.add(document)
        await self._session.flush()
        await self._session.refresh(document)
        return document

    async def update_current_job(self, document: Document, job_id: uuid.UUID | None) -> Document:
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
        status_filter: DocumentStatus | None = None,
        name_query: str | None = None,
    ) -> tuple[list[Document], int]:
        """Глобальный список документов пользователя через проекты (P0-4)."""
        from app.infrastructure.db.models.project import Project

        stmt = (
            select(Document)
            .join(Project, Document.project_id == Project.id)
            .where(Project.owner_id == user_id)
        )
        if status_filter is not None:
            stmt = stmt.where(Document.status == status_filter)
        if name_query:
            stmt = stmt.where(Document.name.ilike(f"%{name_query}%"))

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total_result = await self._session.execute(count_stmt)
        total = total_result.scalar_one()

        stmt = stmt.order_by(Document.uploaded_at.desc()).limit(limit).offset(offset)
        result = await self._session.execute(stmt)
        return list(result.scalars().all()), total

    async def compare_and_increment_review_version(
        self, document_id: uuid.UUID, expected_version: int
    ) -> Document | None:
        """Atomically claim a review version using compare-and-swap."""
        stmt = (
            update(Document)
            .where(Document.id == document_id, Document.review_version == expected_version)
            .values(review_version=Document.review_version + 1)
            .returning(Document)
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def increment_review_version(self, document: Document) -> Document:
        """P0-2: Атомарный UPDATE review_version += 1.

        Выполняется через UPDATE ... RETURNING, чтобы получить актуальное
        значение без дополнительного SELECT.
        """
        stmt = (
            update(Document)
            .where(Document.id == document.id)
            .values(review_version=Document.review_version + 1)
            .returning(Document)
        )
        result = await self._session.execute(stmt)
        updated = result.scalar_one()
        return updated
