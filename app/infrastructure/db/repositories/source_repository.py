"""
SQLAlchemy-адаптер для Source.

Правило: НИКАКИХ session.commit() / session.rollback() здесь.
Все изменения фиксирует SqlAlchemyUnitOfWork через uow.commit().

P2: метод create() (принимал text_content) удалён.
    Добавлен create_url() — для источников типа URL (только url, без storage_key).
    create_with_id() теперь единственный путь для FILE-источников.
R-4: uploaded_at удалён из модели Source; list_by_project теперь
    сортирует по created_at DESC (семантически эквивалентно).
I-1: добавлен list_by_document_ids — батч-запрос document-scope источников
    для нескольких документов через JOIN на document_sources (M2M).
    Возвращает list[tuple[Source, document_id]] чтобы сервис мог
    строить dict без обращения к несуществующей Source.document_id.

FIX-1: list_by_document_ids и replace_document_sources переписаны
    через JOIN на document_sources — Source.document_id не существует.
FIX-2: create_with_id и create_url: source_type= → type= (имя колонки).
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

    async def list_by_document_ids(
        self,
        project_id: uuid.UUID,
        document_ids: list[uuid.UUID],
    ) -> "list[tuple[Source, uuid.UUID]]":
        """I-1 / FIX-1: батч-запрос document-scope источников через M2M.

        Source не имеет колонки document_id — связь идёт через таблицу
        document_sources. Возвращаем list[(Source, document_id)] чтобы
        вызывающий код (SourceService) мог группировать без AttrError.

        SQL:
            SELECT s.*, ds.document_id
              FROM sources s
              JOIN document_sources ds ON ds.source_id = s.id
             WHERE ds.document_id IN (:ids)
               AND s.project_id = :pid
             ORDER BY s.created_at DESC
        """
        if not document_ids:
            return []
        from app.infrastructure.db.models.document_source import document_sources as DS
        from app.infrastructure.db.models.source import Source as M
        stmt = (
            select(M, DS.c.document_id)
            .join(DS, DS.c.source_id == M.id)
            .where(
                DS.c.document_id.in_(document_ids),
                M.project_id == project_id,
            )
            .order_by(M.created_at.desc())
        )
        result = await self._session.execute(stmt)
        return [(row.Source, row.document_id) for row in result]

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
        """Файловый источник (включая бывшие note, сохранённые как .txt).

        FIX-2: исправлено source_type= → type= (имя колонки ORM-модели).
        """
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
    ) -> "Source":
        """URL-источник — только url, без storage_key.

        FIX-2: исправлено source_type= → type= (имя колонки ORM-модели).
        """
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

    async def replace_document_sources(
        self,
        document_id: uuid.UUID,
        sources: "list[Source]",
    ) -> "list[Source]":
        """FIX-1: удаление и вставка через M2M таблицу document_sources.

        Source не имеет колонки document_id — операции идут через
        document_sources (JOIN-таблица). Скоуп источников не меняется
        (scope уже выставлен на DOCUMENT при создании либо обновляется здесь).
        """
        from app.infrastructure.db.models.document_source import document_sources as DS
        from app.infrastructure.db.models.enums import SourceScope

        # Удаляем все текущие связи документа с источниками
        await self._session.execute(
            delete(DS).where(DS.c.document_id == document_id)
        )

        # Вставляем новые связи и выставляем scope=DOCUMENT на источниках
        for source in sources:
            source.scope = SourceScope.DOCUMENT
            await self._session.execute(
                DS.insert().values(document_id=document_id, source_id=source.id)
            )

        await self._session.flush()
        return sources

    async def delete_if_owned(
        self,
        project_id: uuid.UUID,
        source_id: uuid.UUID,
    ) -> "Source | None":
        """OPT-S2: атомарное удаление источника принадлежащего проекту.

        FIX-1: убрано обращение к Source.document_id (колонки нет).
        Возвращает удалённый объект или None если не найден / чужой.
        storage_key доступен на объекте для последующего удаления из MinIO.
        """
        from app.infrastructure.db.models.source import Source as M
        source = await self._session.get(M, source_id)
        if source is None or source.project_id != project_id:
            return None
        await self._session.delete(source)
        await self._session.flush()
        return source
