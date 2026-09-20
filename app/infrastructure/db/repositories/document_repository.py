import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select

from app.infrastructure.db.models.document import Document, document_sources
from app.infrastructure.db.models.source import Source
from app.infrastructure.db.models.enums import DocumentStatus
from app.infrastructure.db.models.source_scope import SourceScope


class DocumentRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_id(self, document_id: uuid.UUID) -> Document | None:
        return await self._session.get(Document, document_id)

    async def list_for_project(self, project_id: uuid.UUID, limit: int, offset: int) -> tuple[list[Document], int]:
        stmt = select(Document).where(Document.project_id == project_id).order_by(Document.uploaded_at.desc())
        total = await self._session.scalar(select(func.count()).select_from(stmt.subquery()))
        items = (await self._session.execute(stmt.limit(limit).offset(offset))).scalars().all()
        return items, total

    async def attach_sources(self, document: Document, source_ids: list[uuid.UUID]) -> Document:
        # Привязка специфичных источников только для одного документа
        existing = await self._session.execute(
            select(Source.id).join(document_sources).where(document_sources.c.document_id == document.id)
        )
        existing_ids = {row[0] for row in existing}
        new_ids = set(source_ids)
        to_add = new_ids - existing_ids
        to_delete = existing_ids - new_ids
        if to_delete:
            await self._session.execute(
                delete(document_sources).where(
                    document_sources.c.document_id == document.id,
                    document_sources.c.source_id.in_(list(to_delete)),
                )
            )
        if to_add:
            for sid in to_add:
                await self._session.execute(
                    document_sources.insert().values(document_id=document.id, source_id=sid)
                )
        await self._session.commit()
        await self._session.refresh(document, ["sources"])
        return document

    async def update_status(self, document: Document, status: DocumentStatus) -> Document:
        document.status = status
        await self._session.commit()
        await self._session.refresh(document)
        return document

    async def list_analyzable_for_project(self, project_id: uuid.UUID) -> list[Document]:
        # Документы, доступные для группового анализа: draft и ready, без активной задачи анализа
        stmt = select(Document).where(
            Document.project_id == project_id,
            Document.status.in_((DocumentStatus.DRAFT, DocumentStatus.READY)),
        )
        return (await self._session.execute(stmt)).scalars().all()
