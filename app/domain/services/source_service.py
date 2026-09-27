"""
Бизнес-логика источников истины.

После P2:
  - create_text_source (хранил text_content в БД) удалён.
  - create_note_source: кодирует текст в UTF-8, загружает в MinIO
    как «<source_id>.txt», сохраняет storage_key. Единый путь
    с файловым источником — воркер всегда идёт в MinIO.
  - create_url_source: сохраняет url в БД (без файла в MinIO).
  - create_file_source: без изменений.

Архитектурные правила:
  - Зависит только от IUnitOfWork (порт) и FileStorage (порт).
  - Нет импортов из app.infrastructure.* при выполнении.
  - app.core.config — допустимый non-infra импорт (документировано).
"""
from __future__ import annotations

import uuid
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
        """Сохраняет текстовую заметку как .txt в MinIO.

        Единый кодовый путь в пайплайне воркера:
        воркер всегда идёт в MinIO по storage_key — никаких спецкейсов NOTE.
        """
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
    # Create — file (без изменений)
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
