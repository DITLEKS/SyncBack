"""SQLAlchemy-адаптер для Source.

Изменения не фиксируются здесь: commit делает SqlAlchemyUnitOfWork.
Связь источника с документами идёт через таблицу document_sources,
у Source нет колонки document_id.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.interfaces.repositories import ISourceRepository
from app.domain.value_objects import SourceScopeVO, SourceTypeVO
from app.infrastructure.db.models.document_source import document_sources
from app.infrastructure.db.models.enums import SourceType
from app.infrastructure.db.models.source import Source


def _type_to_orm(vo: SourceTypeVO):
    return SourceType(vo.value)


class SourceRepository(ISourceRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, source_id: uuid.UUID) -> Source | None:
        return await self._session.get(Source, source_id)

    async def get_many_by_ids(self, source_ids: list[uuid.UUID]) -> list[Source]:
        if not source_ids:
            return []
        result = await self._session.execute(select(Source).where(Source.id.in_(source_ids)))
        return list(result.scalars().all())

    async def list_by_project(
        self,
        project_id: uuid.UUID,
        limit: int,
        offset: int,
        scope: SourceScopeVO | None = None,
    ) -> list[Source]:
        stmt = (
            select(Source)
            .where(Source.project_id == project_id)
            .order_by(Source.created_at.desc(), Source.id.desc())
            .limit(limit)
            .offset(offset)
        )
        if scope is not None:
            stmt = stmt.where(Source.scope == scope)
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_for_analysis(
        self, project_id: uuid.UUID, document_id: uuid.UUID
    ) -> list[Source]:
        attached = select(document_sources.c.source_id).where(
            document_sources.c.document_id == document_id
        )
        stmt = (
            select(Source)
            .where(
                Source.project_id == project_id,
                (Source.scope == SourceScopeVO.PROJECT) | Source.id.in_(attached),
            )
            .order_by(Source.created_at.asc(), Source.id.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_by_project(
        self, project_id: uuid.UUID, scope: SourceScopeVO | None = None
    ) -> int:
        stmt = select(func.count()).select_from(Source).where(Source.project_id == project_id)
        if scope is not None:
            stmt = stmt.where(Source.scope == scope)
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def list_by_document_ids(
        self,
        project_id: uuid.UUID,
        document_ids: list[uuid.UUID],
    ) -> list[tuple[Source, uuid.UUID]]:
        if not document_ids:
            return []
        stmt = (
            select(Source, document_sources.c.document_id)
            .join(document_sources, document_sources.c.source_id == Source.id)
            .where(
                document_sources.c.document_id.in_(document_ids),
                Source.project_id == project_id,
                Source.scope == SourceScopeVO.DOCUMENT,
            )
            .order_by(Source.created_at.desc(), Source.id.desc())
        )
        result = await self._session.execute(stmt)
        return [(row.Source, row.document_id) for row in result]

    async def list_attached_document_ids(self, source_id: uuid.UUID) -> list[uuid.UUID]:
        result = await self._session.execute(
            select(document_sources.c.document_id).where(document_sources.c.source_id == source_id)
        )
        return list(result.scalars().all())

    async def attach_to_document(self, source_id: uuid.UUID, document_id: uuid.UUID) -> None:
        stmt = (
            pg_insert(document_sources)
            .values(document_id=document_id, source_id=source_id)
            .on_conflict_do_nothing()
        )
        await self._session.execute(stmt)
        await self._session.flush()

    async def create_with_id(
        self,
        source_id: uuid.UUID,
        project_id: uuid.UUID,
        name: str,
        source_type: SourceTypeVO,
        storage_key: str,
        scope: SourceScopeVO = SourceScopeVO.PROJECT,
    ) -> Source:
        """Файловый источник; id задаётся заранее, потому что входит в ключ в хранилище."""
        source = Source(
            id=source_id,
            project_id=project_id,
            name=name,
            type=_type_to_orm(source_type),
            storage_key=storage_key,
            scope=scope,
        )
        self._session.add(source)
        await self._session.flush()
        return source

    async def create_url(
        self,
        project_id: uuid.UUID,
        name: str,
        url: str,
        scope: SourceScopeVO = SourceScopeVO.PROJECT,
    ) -> Source:
        source = Source(
            id=uuid.uuid4(),
            project_id=project_id,
            name=name,
            type=SourceType.URL,
            url=url,
            scope=scope,
        )
        self._session.add(source)
        await self._session.flush()
        return source

    async def delete(self, source: Source) -> None:
        await self._session.delete(source)
        await self._session.flush()
