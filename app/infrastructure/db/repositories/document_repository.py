"""
Репозиторий документов.

P0-2: добавлен метод finalize_and_bump_version() — атомарный UPDATE
статуса документа в READY + инкремент review_version в одном запросе.
Если между SELECT и UPDATE версия изменилась (гонка) — UPDATE не найдёт строку
(WHERE review_version = :expected) и выбросит StaleReviewVersionError.

fix/review-critical-p0:
- Добавлен метод bump_review_version() — инкремент review_version без смены статуса.
  Используется в SuggestionService.apply_review() (PUT /editor/review).
"""
from __future__ import annotations

import uuid
from typing import Literal

import sqlalchemy as sa
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.exceptions import StaleReviewVersionError
from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.enums import DocumentStatus


class DocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, document_id: uuid.UUID) -> Document | None:
        result = await self._session.execute(
            select(Document).where(Document.id == document_id)
        )
        return result.scalar_one_or_none()

    async def create(self, document: Document) -> Document:
        self._session.add(document)
        await self._session.flush()
        await self._session.refresh(document)
        return document

    async def update_status(self, document: Document, new_status: DocumentStatus) -> Document:
        document.status = new_status
        await self._session.flush()
        await self._session.refresh(document)
        return document

    async def list_by_project(
        self, project_id: uuid.UUID, limit: int, offset: int
    ) -> list[Document]:
        result = await self._session.execute(
            select(Document)
            .where(Document.project_id == project_id)
            .order_by(Document.uploaded_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def count_by_project(self, project_id: uuid.UUID) -> int:
        result = await self._session.execute(
            select(func.count()).where(Document.project_id == project_id)
        )
        return result.scalar_one()

    async def delete(self, document: Document) -> None:
        await self._session.delete(document)
        await self._session.flush()

    async def attach_sources(self, document: Document, sources: list) -> Document:
        document.sources = sources
        await self._session.flush()
        await self._session.refresh(document)
        return document

    async def list_analyzable_for_project(
        self, project_id: uuid.UUID
    ) -> list[Document]:
        """Вернуть документы проекта в статусах DRAFT или AWAITING_APPROVAL."""
        result = await self._session.execute(
            select(Document)
            .where(
                Document.project_id == project_id,
                Document.status.in_((DocumentStatus.DRAFT, DocumentStatus.AWAITING_APPROVAL)),
            )
        )
        return list(result.scalars().all())

    async def list_all_for_user(
        self,
        owner_id: uuid.UUID,
        *,
        status: DocumentStatus | None = None,
        search: str | None = None,
        sort_by: Literal["created_at", "updated_at", "title"] = "updated_at",
        sort_dir: Literal["asc", "desc"] = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Список документов пользователя с агрегированными счётчиками правок."""
        from app.infrastructure.db.models.project import Project

        q = (
            select(Document)
            .join(Project, Document.project_id == Project.id)
            .where(Project.owner_id == owner_id)
        )
        if status:
            q = q.where(Document.status == status)
        if search:
            q = q.where(Document.name.ilike(f"%{search}%"))
        sort_col = getattr(Document, sort_by, Document.uploaded_at)
        q = q.order_by(sort_col.desc() if sort_dir == "desc" else sort_col.asc())
        total_q = select(func.count()).select_from(q.subquery())
        total = (await self._session.execute(total_q)).scalar_one()
        items = list((await self._session.execute(q.limit(limit).offset(offset))).scalars().all())
        return [dict(
            id=d.id, project_id=d.project_id, name=d.name,
            format=d.format.value if hasattr(d.format, "value") else d.format,
            status=d.status.value if hasattr(d.status, "value") else d.status,
            size_bytes=d.size_bytes, uploaded_at=d.uploaded_at,
            review_version=d.review_version,
        ) for d in items], total

    # -----------------------------------------------------------------------
    # P0-2: оптимистическая блокировка
    # -----------------------------------------------------------------------

    async def finalize_and_bump_version(self, document: Document) -> Document:
        """Атомарно перевести документ в READY и инкрементировать review_version.

        UPDATE documents
           SET status = 'ready', review_version = review_version + 1
         WHERE id = :id AND review_version = :expected_version

        Если другой процесс уже инкрементировал версию между нашим SELECT и
        этим UPDATE — rowcount == 0 → StaleReviewVersionError → 412.
        """
        expected_version = document.review_version
        stmt = (
            update(Document)
            .where(
                Document.id == document.id,
                Document.review_version == expected_version,
            )
            .values(
                status=DocumentStatus.READY,
                review_version=Document.review_version + 1,
            )
            .returning(Document)
        )
        result = await self._session.execute(stmt)
        updated = result.scalar_one_or_none()
        if updated is None:
            raise StaleReviewVersionError(
                "Гонка при финализации: версия документа изменилась параллельным запросом. "
                "Обновите страницу и повторите."
            )
        await self._session.flush()
        return updated

    async def bump_review_version(self, document: Document) -> Document:
        """Атомарно инкрементировать review_version без смены статуса документа.

        Используется в apply_review (PUT /editor/review) — пользователь сохраняет
        часть правок, не завершая review целиком.

        UPDATE documents
           SET review_version = review_version + 1
         WHERE id = :id AND review_version = :expected_version

        Если версия изменилась параллельным запросом — StaleReviewVersionError → 409.
        """
        expected_version = document.review_version
        stmt = (
            update(Document)
            .where(
                Document.id == document.id,
                Document.review_version == expected_version,
            )
            .values(review_version=Document.review_version + 1)
            .returning(Document)
        )
        result = await self._session.execute(stmt)
        updated = result.scalar_one_or_none()
        if updated is None:
            raise StaleReviewVersionError(
                "Гонка при сохранении правок: версия документа изменилась параллельным запросом. "
                "Обновите страницу и повторите."
            )
        await self._session.flush()
        return updated
