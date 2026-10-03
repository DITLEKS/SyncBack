"""Источники истины проекта.

Базовые источники (scope=project) участвуют в анализе всех документов проекта,
документные (scope=document) прикрепляются к конкретному документу через M2M.
Файлы и заметки хранятся в файловом хранилище, в БД лежит только ключ.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from app.core.config import Settings, get_settings
from app.domain.exceptions import (
    DocumentNotFoundError,
    FileTooLargeError,
    InvalidSourceScopeError,
    SourceLockError,
    SourceNotFoundError,
)
from app.domain.interfaces.file_storage import FileStorage
from app.domain.interfaces.unit_of_work import IUnitOfWork
from app.domain.lifecycle import DocumentLifecycle
from app.domain.value_objects import SourceScopeVO, SourceTypeVO

if TYPE_CHECKING:
    from app.infrastructure.db.models.document import Document
    from app.infrastructure.db.models.project import Project
    from app.infrastructure.db.models.source import Source

logger = logging.getLogger("syncscribe.sources")


def _storage_filename(filename: str) -> str:
    """Имя файла для ключа в хранилище: без каталогов и служебных имён."""
    name = PurePosixPath(filename.replace("\\", "/")).name
    return name if name not in {"", ".", ".."} else "upload"


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

    @staticmethod
    def _assert_sources_mutable(document: Document) -> None:
        if not DocumentLifecycle.can_edit_sources(document.status):
            raise SourceLockError(
                f"Нельзя изменить источники документа в статусе '{document.status.value}'. "
                "Дождитесь завершения анализа или переведите документ обратно в черновик."
            )

    async def _target_document(
        self, project_id: uuid.UUID, scope: SourceScopeVO, document_id: uuid.UUID | None
    ) -> Document | None:
        """Документ, к которому будет прикреплён новый источник (None для scope=project).

        Вызывается внутри открытого UoW. Документ должен принадлежать проекту
        и допускать изменение источников.
        """
        if scope is not SourceScopeVO.DOCUMENT:
            return None
        if document_id is None:
            raise InvalidSourceScopeError("Для источника со scope=document нужен document_id")
        document = await self._uow.documents.get_by_id(document_id)
        if document is None or document.project_id != project_id:
            raise DocumentNotFoundError(f"Документ {document_id} не найден в проекте {project_id}")
        self._assert_sources_mutable(document)
        return document

    async def attach_sources_to_document(
        self, document: Document, sources: list[Source]
    ) -> list[Source]:
        """Прикрепить источники проекта к документу (повтор безопасен).

        Источники должны быть уже проверены на принадлежность проекту документа
        (см. get_sources_for_project). Бросает SourceLockError, если документ
        сейчас анализируется или находится на ревью.
        """
        self._assert_sources_mutable(document)
        async with self._uow:
            for source in sources:
                await self._uow.sources.attach_to_document(source.id, document.id)
            await self._uow.commit()
        return sources

    async def create_url_source(
        self,
        project: Project,
        name: str,
        url: str,
        scope: SourceScopeVO = SourceScopeVO.PROJECT,
        document_id: uuid.UUID | None = None,
    ) -> Source:
        async with self._uow:
            document = await self._target_document(project.id, scope, document_id)
            source = await self._uow.sources.create_url(
                project_id=project.id, name=name, url=url, scope=scope
            )
            if document is not None:
                await self._uow.sources.attach_to_document(source.id, document.id)
            await self._uow.commit()
        return source

    async def create_note_source(
        self,
        project: Project,
        name: str,
        text_content: str,
        scope: SourceScopeVO = SourceScopeVO.PROJECT,
        document_id: uuid.UUID | None = None,
    ) -> Source:
        """Текстовая заметка сохраняется как .txt-файл, тип источника — file."""
        return await self._create_stored_source(
            project,
            name=name,
            filename="note.txt",
            content=text_content.encode("utf-8"),
            content_type="text/plain; charset=utf-8",
            scope=scope,
            document_id=document_id,
            too_large_message=f"Текст превышает лимит {self._settings.max_upload_size_mb} МБ",
        )

    async def create_file_source(
        self,
        project: Project,
        name: str,
        filename: str,
        content: bytes,
        content_type: str,
        scope: SourceScopeVO = SourceScopeVO.PROJECT,
        document_id: uuid.UUID | None = None,
    ) -> Source:
        return await self._create_stored_source(
            project,
            name=name,
            filename=filename,
            content=content,
            content_type=content_type,
            scope=scope,
            document_id=document_id,
            too_large_message=f"Файл превышает лимит {self._settings.max_upload_size_mb} МБ",
        )

    async def _create_stored_source(
        self,
        project: Project,
        *,
        name: str,
        filename: str,
        content: bytes,
        content_type: str,
        scope: SourceScopeVO,
        document_id: uuid.UUID | None,
        too_large_message: str,
    ) -> Source:
        """Загрузить содержимое в хранилище и записать источник в одной транзакции.

        Если запись в БД не удалась, загруженный объект удаляется, чтобы в хранилище
        не оставалось файлов без владельца.
        """
        if len(content) > self._settings.max_upload_size_bytes:
            raise FileTooLargeError(too_large_message)

        source_id = uuid.uuid4()
        storage_key = f"projects/{project.id}/sources/{source_id}/{_storage_filename(filename)}"
        uploaded = False
        try:
            async with self._uow:
                document = await self._target_document(project.id, scope, document_id)
                await self._storage.upload(storage_key, content, content_type)
                uploaded = True
                source = await self._uow.sources.create_with_id(
                    source_id=source_id,
                    project_id=project.id,
                    name=name,
                    source_type=SourceTypeVO.FILE,
                    storage_key=storage_key,
                    scope=scope,
                )
                if document is not None:
                    await self._uow.sources.attach_to_document(source.id, document.id)
                await self._uow.commit()
        except Exception:
            if uploaded:
                await self._storage.delete(storage_key)
            raise
        return source

    async def list_sources(
        self,
        project_id: uuid.UUID,
        limit: int,
        offset: int,
        scope: SourceScopeVO | None = None,
    ) -> tuple[list[Source], int]:
        async with self._uow:
            items = await self._uow.sources.list_by_project(
                project_id, limit=limit, offset=offset, scope=scope
            )
            total = await self._uow.sources.count_by_project(project_id, scope=scope)
        return items, total

    async def get_source(self, project_id: uuid.UUID, source_id: uuid.UUID) -> Source:
        async with self._uow:
            source = await self._uow.sources.get_by_id(source_id)
        if source is None or source.project_id != project_id:
            raise SourceNotFoundError(f"Источник {source_id} не найден в проекте {project_id}")
        return source

    async def get_sources_for_project(
        self, project_id: uuid.UUID, source_ids: list[uuid.UUID]
    ) -> list[Source]:
        async with self._uow:
            sources = await self._uow.sources.get_many_by_ids(source_ids)

        found_ids = {s.id for s in sources}
        missing = set(source_ids) - found_ids
        if missing:
            raise SourceNotFoundError(f"Источники не найдены: {missing}")

        foreign = [s.id for s in sources if s.project_id != project_id]
        if foreign:
            raise SourceNotFoundError(f"Источники не принадлежат проекту {project_id}: {foreign}")
        return sources

    async def list_sources_for_documents(
        self,
        project_id: uuid.UUID,
        document_ids: list[uuid.UUID],
    ) -> dict[uuid.UUID, list[Source]]:
        """Документные источники для набора документов: {document_id: [Source, ...]}."""
        if not document_ids:
            return {}
        async with self._uow:
            pairs = await self._uow.sources.list_by_document_ids(project_id, document_ids)
        result: dict[uuid.UUID, list[Source]] = defaultdict(list)
        for source, doc_id in pairs:
            result[doc_id].append(source)
        return dict(result)

    async def delete_source(self, project_id: uuid.UUID, source_id: uuid.UUID) -> None:
        """Удалить источник проекта.

        Бросает SourceNotFoundError, если источник не найден или чужой,
        и SourceLockError, если он прикреплён к документу, который сейчас
        анализируется или находится на ревью. Файл в хранилище удаляется
        после фиксации транзакции; сбой удаления только логируется.
        """
        async with self._uow:
            source = await self._uow.sources.get_by_id(source_id)
            if source is None or source.project_id != project_id:
                raise SourceNotFoundError(f"Источник {source_id} не найден в проекте {project_id}")

            document_ids = await self._uow.sources.list_attached_document_ids(source_id)
            for document in await self._uow.documents.get_many_by_ids(document_ids):
                self._assert_sources_mutable(document)

            storage_key = source.storage_key
            await self._uow.sources.delete(source)
            await self._uow.commit()

        if storage_key:
            try:
                await self._storage.delete(storage_key)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Файл источника не удалён из хранилища",
                    exc_info=True,
                    extra={"source_id": str(source_id), "storage_key": storage_key},
                )
