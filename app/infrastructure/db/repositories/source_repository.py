"""SQLAlchemy-адаптер для Source.

Изменения не фиксируются здесь: commit делает SqlAlchemyUnitOfWork.
Связь источника с документами идёт через таблицу document_sources,
у Source нет колонки document_id.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.interfaces.repositories import ISourceRepository
from app.domain.value_objects import SourceScopeVO, SourceTypeVO

if TYPE_CHECKING:
    from app.infrastructure.db.models.source import Source


def _scope_to_orm(vo: SourceScopeVO):
    from app.infrastructure.db.models.enums import SourceScope

    return SourceScope(vo.value)


def _type_to_orm(vo: SourceTypeVO):
    from app.infrastructure.db.models.enums import SourceType

    return SourceType(vo.value)


class SourceRepository(ISourceRepository):
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, source_id: uuid.UUID) -> Source | None:
        from app.infrastructure.db.models.source import Source as M

        return await self._session.get(M, source_id)

    async def get_many_by_ids(self, source_ids: list[uuid.UUID]) -> list[Source]:
        from app.infrastructure.db.models.source import Source as M

        if not source_ids:
            return []
        result = await self._session.execute(select(M).where(M.id.in_(source_ids)))
        return list(result.scalars().all())

    async def list_by_project(
        self,
        project_id: uuid.UUID,
        limit: int,
        offset: int,
        scope: SourceScopeVO | None = None,
    ) -> list[Source]:
        from app.infrastructure.db.models.source import Source as M

        stmt = (
            select(M)
            .where(M.project_id == project_id)
            .order_by(M.created_at.desc(), M.id.desc())
            .limit(limit)
            .offset(offset)
        )
        if scope is not None:
            stmt = stmt.where(M.scope == _scope_to_orm(scope))
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def list_for_analysis(
        self, project_id: uuid.UUID, document_id: uuid.UUID
    ) -> list[Source]:
        from app.infrastructure.db.models.document_source import document_sources as DS
        from app.infrastructure.db.models.enums import SourceScope
        from app.infrastructure.db.models.source import Source as M

        attached = select(DS.c.source_id).where(DS.c.document_id == document_id)
        stmt = (
            select(M)
            .where(
                M.project_id == project_id,
                (M.scope == SourceScope.PROJECT) | M.id.in_(attached),
            )
            .order_by(M.created_at.asc(), M.id.asc())
        )
        result = await self._session.execute(stmt)
        return list(result.scalars().all())

    async def count_by_project(
        self, project_id: uuid.UUID, scope: SourceScopeVO | None = None
    ) -> int:
        from app.infrastructure.db.models.source import Source as M

        stmt = select(func.count()).select_from(M).where(M.project_id == project_id)
        if scope is not None:
            stmt = stmt.where(M.scope == _scope_to_orm(scope))
        result = await self._session.execute(stmt)
        return result.scalar_one()

    async def list_by_document_ids(
        self,
        project_id: uuid.UUID,
        document_ids: list[uuid.UUID],
    ) -> list[tuple[Source, uuid.UUID]]:
        if not document_ids:
            return []
        from app.infrastructure.db.models.document_source import document_sources as DS
        from app.infrastructure.db.models.enums import SourceScope
        from app.infrastructure.db.models.source import Source as M

        stmt = (
            select(M, DS.c.document_id)
            .join(DS, DS.c.source_id == M.id)
            .where(
                DS.c.document_id.in_(document_ids),
                M.project_id == project_id,
                M.scope == SourceScope.DOCUMENT,
            )
            .order_by(M.created_at.desc(), M.id.desc())
        )
        result = await self._session.execute(stmt)
        return [(row.Source, row.document_id) for row in result]

    async def list_attached_document_ids(self, source_id: uuid.UUID) -> list[uuid.UUID]:
        from app.infrastructure.db.models.document_source import document_sources as DS

        result = await self._session.execute(
            select(DS.c.document_id).where(DS.c.source_id == source_id)
        )
        return list(result.scalars().all())

    async def attach_to_document(self, source_id: uuid.UUID, document_id: uuid.UUID) -> None:
        from app.infrastructure.db.models.document_source import document_sources as DS

        stmt = (
            pg_insert(DS)
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
        from app.infrastructure.db.models.source import Source as M

        source = M(
            id=source_id,
            project_id=project_id,
            name=name,
            type=_type_to_orm(source_type),
            storage_key=storage_key,
            scope=_scope_to_orm(scope),
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
        from app.infrastructure.db.models.enums import SourceType
        from app.infrastructure.db.models.source import Source as M

        source = M(
            id=uuid.uuid4(),
            project_id=project_id,
            name=name,
            type=SourceType.URL,
            url=url,
            scope=_scope_to_orm(scope),
        )
        self._session.add(source)
        await self._session.flush()
        return source

    async def delete(self, source: Source) -> None:
        await self._session.delete(source)
        await self._session.flush()
