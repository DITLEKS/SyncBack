"""
Репозиторий документов.

ДОБАВЛЕНО:
- delete() — удаляет запись документа; каскад в БД удаляет suggestions, analysis_jobs,
  document_sources.
- list_by_project / count_by_project — переименованы из list_for_project (было оба имени).
"""

import uuid
from typing import Literal

from sqlalchemy import case, delete, func, or_, select
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

    async def get_by_id(self, document_id: uuid.UUID) -> Document | None:
        return await self._session.get(Document, document_id)

    async def create(self, document: Document) -> Document:
        self._session.add(document)
        await self._session.commit()
        await self._session.refresh(document)
        return document

    async def delete(self, document: Document) -> None:
        await self._session.delete(document)
        await self._session.commit()

    async def list_by_project(self, project_id: uuid.UUID, limit: int, offset: int) -> list[Document]:
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
            select(func.count()).select_from(Document).where(Document.project_id == project_id)
        )
        return result.scalar_one()

    async def update_status(self, document: Document, status: DocumentStatus) -> Document:
        document.status = status
        await self._session.commit()
        await self._session.refresh(document)
        return document

    async def attach_sources(self, document: Document, sources: list[Source]) -> Document:
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
            .outerjoin(AnalysisJob, AnalysisJob.id == Document.current_analysis_job_id)
            .outerjoin(Suggestion, Suggestion.analysis_job_id == AnalysisJob.id)
            .where(Project.owner_id == owner_id)
            .group_by(Document.id, Project.name)
        )

        if status is not None:
            stmt = stmt.where(Document.status == status)
        if search:
            stmt = stmt.where(Document.title.ilike(f"%{search}%"))

        sort_col = {
            "created_at": Document.created_at,
            "updated_at": Document.updated_at,
            "title": Document.title,
        }[sort_by]
        stmt = stmt.order_by(sort_col.asc() if sort_dir == "asc" else sort_col.desc())

        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await self._session.execute(count_stmt)).scalar_one()

        rows = await self._session.execute(stmt.limit(limit).offset(offset))
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
