"""
Репозиторий документов.

ИСПРАВЛЕНО: list_by_project теперь принимает limit/offset, добавлен count_by_project.
ДОБАВЛЕНО (P0-4): list_all_for_user — глобальный список документов пользователя
  с счётчиками правок (total/pending/accepted/rejected) через LEFT JOIN + GROUP BY.
  Поддерживает фильтрацию по статусу, поиск по названию, сортировку и пагинацию.
"""

import uuid
from typing import Literal

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infrastructure.db.models.analysis_job import AnalysisJob
from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.enums import DocumentStatus, SuggestionStatus
from app.infrastructure.db.models.project import Project
from app.infrastructure.db.models.source import Source
from app.infrastructure.db.models.suggestion import Suggestion


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

    async def update_status(self, document: Document, status: DocumentStatus) -> Document:
        document.status = status
        await self._session.commit()
        await self._session.refresh(document)
        return document

    async def attach_sources(self, document: Document, sources: list[Source]) -> Document:
        # Загружаем текущие источники документа в асинхронном контексте, чтобы
        # избежать lazy-load вне greenlet_spawn.
        await self._session.refresh(document, ["sources"])
        document.sources = list({s.id: s for s in (document.sources + sources)}.values())
        await self._session.commit()
        await self._session.refresh(document, ["sources"])
        return document

    # -------------------------------------------------------------------------
    # P0-4: глобальный список документов пользователя
    # -------------------------------------------------------------------------

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
        """
        Возвращает список документов пользователя с агрегированными счётчиками
        правок. Один SQL-запрос: Document → Project (JOIN) + Suggestion через
        current_analysis_job (LEFT JOIN + CASE + GROUP BY).

        Результат — список словарей:
          document       — ORM-объект Document
          project_name   — str
          suggestions_total    — int
          suggestions_pending  — int
          suggestions_accepted — int
          suggestions_rejected — int
        """
        # Агрегаты правок для текущей задачи анализа
        total_col = func.count(Suggestion.id).label("suggestions_total")
        pending_col = func.sum(
            case((Suggestion.status == SuggestionStatus.PENDING, 1), else_=0)
        ).label("suggestions_pending")
        accepted_col = func.sum(
            case((Suggestion.status == SuggestionStatus.ACCEPTED, 1), else_=0)
        ).label("suggestions_accepted")
        rejected_col = func.sum(
            case((Suggestion.status == SuggestionStatus.REJECTED, 1), else_=0)
        ).label("suggestions_rejected")

        stmt = (
            select(
                Document,
                Project.name.label("project_name"),
                total_col,
                pending_col,
                accepted_col,
                rejected_col,
            )
            .join(Project, Document.project_id == Project.id)
            .outerjoin(
                AnalysisJob,
                AnalysisJob.id == Document.current_analysis_job_id,
            )
            .outerjoin(
                Suggestion,
                Suggestion.analysis_job_id == AnalysisJob.id,
            )
            .where(Project.owner_id == owner_id)
            .group_by(Document.id, Project.name)
        )

        if status is not None:
            stmt = stmt.where(Document.status == status)

        if search:
            # Регистронезависимый поиск по подстроке в названии
            stmt = stmt.where(Document.title.ilike(f"%{search}%"))

        # Сортировка
        sort_col = {
            "created_at": Document.created_at,
            "updated_at": Document.updated_at,
            "title": Document.title,
        }[sort_by]
        order_expr = sort_col.asc() if sort_dir == "asc" else sort_col.desc()
        stmt = stmt.order_by(order_expr)

        # Считаем total до пагинации
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total_result = await self._session.execute(count_stmt)
        total = total_result.scalar_one()

        # Применяем пагинацию
        stmt = stmt.limit(limit).offset(offset)
        rows = await self._session.execute(stmt)

        items = [
            {
                "document": row.Document,
                "project_name": row.project_name,
                "suggestions_total": row.suggestions_total or 0,
                "suggestions_pending": row.suggestions_pending or 0,
                "suggestions_accepted": row.suggestions_accepted or 0,
                "suggestions_rejected": row.suggestions_rejected or 0,
            }
            for row in rows
        ]
        return items, total
