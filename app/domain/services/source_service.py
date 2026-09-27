"""
Бизнес-логика источников истины.

После P2:
  - create_text_source (хранил text_content в БД) удалён.
  - create_note_source: кодирует текст в UTF-8, загружает в MinIO.
  - create_url_source: сохраняет url в БД (без файла в MinIO).
  - create_file_source: без изменений.

Архитектурные правила:
  - Зависит только от IUnitOfWork (порт) и FileStorage (порт).
  - Нет импортов из app.infrastructure.* при выполнении.
  - app.core.config — допустимый non-infra импорт.

I-1: list_sources_for_documents — батч-загрузка document-scope источников.
OPT-S2: delete_source_with_guard — атомарное удаление без предварительного get_source();
        бросает SourceNotFoundError если источник не найден или принадлежит другому проекту.
"""
from __future__ import annotations

import uuid
from collections import defaultdict
from typing import TYPE_CHECKING

from app.core.config import Settings, get_settings
from app.domain.exceptions import FileTooLargeError, SourceLockError, SourceNotFoundError
from app.domain.interfaces.file_storage import FileStorage
from app.domain.interfaces.unit_of_work import IUnitOfWork
from app.domain.value_objects import DocumentStatusVO, SourceScopeVO, SourceTypeVO

if TYPE_CHECKING:
    from app.infrastructure.db.models.document import Document
    from app.infrastructure.db.models.project import Project
    from app.infrastructure.db.models.source import Source

_LOCKED_STATUSES: frozenset[DocumentStatusVO] = frozenset({
    DocumentStatusVO.IN_PROGRESS,
    DocumentStatusVO.AWAITING_APPROVAL,
})


class SourceService:
    def __init__(
        self,
        uow: IUnitOfWork,
        file_storage: FileStorage,
        settings: Settings | None = None,
    ) -> None:
        self._uow = uow
        self._storage = file_storage
        self._settings = settings or get_settings()

    # ------------------------------------------------------------------
    # Guard
    # ------------------------------------------------------------------

    @staticmethod
    def _assert_sources_mutable(document: "Document") -> None:
        if document.status in _LOCKED_STATUSES:
            raise SourceLockError(
                f"Нельзя изменить источники документа в статусе '{document.status.value}'. "
                "Дождитесь завершения анализа или переведите документ обратно в черновик."
            )

    # ------------------------------------------------------------------
    # Create — note (P2: текст → MinIO как .txt)
    # ------------------------------------------------------------------

    async def create_note_source(
        self,
        project: "Project",
        name: str,
        text_content: str,
        scope: SourceScopeVO = SourceScopeVO.PROJECT,
    ) -> "Source":
        """Сохраняет текстовую заметку как .txt в MinIO."""
        if len(text_content.encode()) > self._settings.max_upload_size_bytes:
            raise FileTooLargeError(
                f"Текст превышает лимит {self._settings.max_upload_size_mb} МБ"
            )

        source_id = uuid.uuid4()
        storage_key = f"projects/{project.id}/sources/{source_id}/note.txt"
        await self._storage.upload(
            storage_key,
            text_content.encode("utf-8"),
            "text/plain; charset=utf-8",
        )

        try:
            async with self._uow:
                source = await self._uow.sources.create_with_id(
                    source_id=source_id,
                    project_id=project.id,
                    name=name,
                    source_type=SourceTypeVO.FILE,
                    storage_key=storage_key,
                    scope=scope,
                )
                await self._uow.commit()
        except Exception:
            await self._storage.delete(storage_key)
            raise
        return source

    # ------------------------------------------------------------------
    # Create — url
    # ------------------------------------------------------------------

    async def create_url_source(
        self,
        project: "Project",
        name: str,
        url: str,
        scope: SourceScopeVO = SourceScopeVO.PROJECT,
    ) -> "Source":
        async with self._uow:
            source = await self._uow.sources.create_url(
                project_id=project.id,
                name=name,
                url=url,
                scope=scope,
            )
            await self._uow.commit()
        return source

    # ------------------------------------------------------------------
    # Create — file
    # ------------------------------------------------------------------

    async def create_file_source(
        self,
        project: "Project",
        name: str,
        filename: str,
        content: bytes,
        content_type: str,
        scope: SourceScopeVO = SourceScopeVO.PROJECT,
    ) -> "Source":
        if len(content) > self._settings.max_upload_size_bytes:
            raise FileTooLargeError(
                f"Файл превышает лимит {self._settings.max_upload_size_mb} МБ"
            )
        source_id = uuid.uuid4()
        storage_key = f"projects/{project.id}/sources/{source_id}/{filename}"
        await self._storage.upload(storage_key, content, content_type)
        try:
            async with self._uow:
                source = await self._uow.sources.create_with_id(
                    source_id=source_id,
                    project_id=project.id,
                    name=name,
                    source_type=SourceTypeVO.FILE,
                    storage_key=storage_key,
                    scope=scope,
                )
                await self._uow.commit()
        except Exception:
            await self._storage.delete(storage_key)
            raise
        return source

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def list_sources(
        self, project_id: uuid.UUID, limit: int, offset: int
    ) -> "tuple[list[Source], int]":
        async with self._uow:
            items = await self._uow.sources.list_by_project(
                project_id, limit=limit, offset=offset
            )
            total = await self._uow.sources.count_by_project(project_id)
        return items, total

    async def get_source(
        self, project_id: uuid.UUID, source_id: uuid.UUID
    ) -> "Source":
        """Получить источник. Бросает SourceNotFoundError если не найден."""
        async with self._uow:
            source = await self._uow.sources.get_by_id(source_id)
        if source is None or source.project_id != project_id:
            raise SourceNotFoundError(
                f"Источник {source_id} не найден в проекте {project_id}"
            )
        return source

    async def get_sources_for_project(
        self, project_id: uuid.UUID, source_ids: list[uuid.UUID]
    ) -> "list[Source]":
        async with self._uow:
            sources = await self._uow.sources.get_many_by_ids(source_ids)

        found_ids = {s.id for s in sources}
        missing = set(source_ids) - found_ids
        if missing:
            raise SourceNotFoundError(f"Источники не найдены: {missing}")

        foreign = [s.id for s in sources if s.project_id != project_id]
        if foreign:
            raise SourceNotFoundError(
                f"Источники не принадлежат проекту {project_id}: {foreign}"
            )
        return sources

    async def list_sources_for_documents(
        self,
        project_id: uuid.UUID,
        document_ids: list[uuid.UUID],
    ) -> "dict[uuid.UUID, list[Source]]":
        """I-1: батч-загрузка document-scope источников для списка документов.

        Возвращает {document_id: [Source, ...]} для маппинга в DocumentListItem.sources.
        """
        if not document_ids:
            return {}
        async with self._uow:
            sources = await self._uow.sources.list_by_document_ids(
                project_id, document_ids
            )
        result: dict[uuid.UUID, list["Source"]] = defaultdict(list)
        for src in sources:
            result[src.document_id].append(src)
        return dict(result)

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    async def delete_source(
        self, project_id: uuid.UUID, source_id: uuid.UUID
    ) -> None:
        """Удалить источник. Устаревший метод — используй delete_source_with_guard."""
        source = await self.get_source(project_id, source_id)
        storage_key = getattr(source, "storage_key", None)
        async with self._uow:
            await self._uow.sources.delete(source_id)
            await self._uow.commit()
        if storage_key:
            await self._storage.delete(storage_key)

    async def delete_source_with_guard(
        self,
        project_id: uuid.UUID,
        source_id: uuid.UUID,
    ) -> "uuid.UUID | None":
        """OPT-S2: атомарное удаление без предварительного get_source().

        Репозиторий возвращает удалённый объект (или None если не найден/чужой).
        Бросает SourceNotFoundError при отсутствии.
        Возвращает document_id удалённого источника (для _guard_no_active_job в роутере).
        """
        async with self._uow:
            deleted = await self._uow.sources.delete_if_owned(
                project_id=project_id,
                source_id=source_id,
            )
            if deleted is None:
                raise SourceNotFoundError(
                    f"Источник {source_id} не найден в проекте {project_id}"
                )
            await self._uow.commit()
        storage_key = getattr(deleted, "storage_key", None)
        if storage_key:
            await self._storage.delete(storage_key)
        return getattr(deleted, "document_id", None)

    # ------------------------------------------------------------------
    # Attach / detach
    # ------------------------------------------------------------------

    async def replace_document_sources(
        self,
        document: "Document",
        source_ids: list[uuid.UUID],
    ) -> "list[Source]":
        self._assert_sources_mutable(document)
        async with self._uow:
            sources = await self._uow.sources.get_many_by_ids(source_ids)

            found_ids = {s.id for s in sources}
            missing = set(source_ids) - found_ids
            if missing:
                raise SourceNotFoundError(f"Источники не найдены: {missing}")
            foreign = [s.id for s in sources if s.project_id != document.project_id]
            if foreign:
                raise SourceNotFoundError(
                    f"Источники не принадлежат проекту {document.project_id}: {foreign}"
                )

            result = await self._uow.sources.replace_document_sources(
                document.id, sources
            )
            await self._uow.commit()
        return result
