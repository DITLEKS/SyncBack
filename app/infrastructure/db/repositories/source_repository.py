"""
SQLAlchemy-адаптер для Source.

Правило: НИКАКИХ session.commit() / session.rollback() здесь.
Все изменения фиксирует SqlAlchemyUnitOfWork через uow.commit().

P2: метод create() (принимал text_content) удалён.
    Добавлен create_url() — для источников типа URL (только url, без storage_key).
    create_with_id() теперь единственный путь для FILE-источников.
R-4: uploaded_at удалён из модели Source; list_by_project теперь
    сортирует по created_at DESC (семантически эквивалентно).
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select
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

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get_by_id(self, source_id: uuid.UUID) -> "Source | None":
        from app.infrastructure.db.models.source import Source as M
        return await self._session.get(M, source_id)

    async def get_many_by_ids(self, source_ids: list[uuid.UUID]) -> "list[Source]":
        from app.infrastructure.db.models.source import Source as M
        result = await self._session.execute(
            select(M).where(M.id.in_(source_ids))
        )
        return list(result.scalars().all())

    async def list_by_project(
        self, project_id: uuid.UUID, limit: int, offset: int
    ) -> "list[Source]":
        from app.infrastructure.db.models.source import Source as M
        # R-4: сортировка по created_at вместо удалённого uploaded_at.
        result = await self._session.execute(
            select(M)
            .where(M.project_id == project_id)
            .order_by(M.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(result.scalars().all())

    async def count_by_project(self, project_id: uuid.UUID) -> int:
        from app.infrastructure.db.models.source import Source as M
        result = await self._session.execute(
            select(func.count()).select_from(M).where(M.project_id == project_id)
        )
        return result.scalar_one()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    async def create_with_id(
        self,
        source_id: uuid.UUID,
        project_id: uuid.UUID,
        name: str,
        source_type: SourceTypeVO,
        storage_key: str,
        scope: SourceScopeVO = SourceScopeVO.PROJECT,
    ) -> "Source":
        """Файловый источник (включая бывшие note, сохранённые как .txt)."""
        from app.infrastructure.db.models.source import Source as M
        source = M(
            id=source_id,
            project_id=project_id,
            name=name,
            source_type=_type_to_orm(source_type),
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
    ) -> "Source":
        """URL-источник — только url, без storage_key."""
        from app.infrastructure.db.models.enums import SourceType
        from app.infrastructure.db.models.source import Source as M
        source = M(
            id=uuid.uuid4(),
            project_id=project_id,
            name=name,
            source_type=SourceType.URL,
            url=url,
            scope=_scope_to_orm(scope),
        )
        self._session.add(source)
        await self._session.flush()
        return source

    async def replace_document_sources(
        self,
        document_id: uuid.UUID,
        sources: "list[Source]",
    ) -> "list[Source]":
        from app.infrastructure.db.models.enums import SourceScope
        from app.infrastructure.db.models.source import Source as M

        await self._session.execute(
            delete(M).where(
                M.document_id == document_id,
                M.scope == SourceScope.DOCUMENT,
            )
        )

        for source in sources:
            source.document_id = document_id
            source.scope = SourceScope.DOCUMENT

        await self._session.flush()
        return sources
