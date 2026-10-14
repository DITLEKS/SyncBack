"""Источники истины проекта.

Базовые источники (scope=project) участвуют в анализе всех документов проекта,
документные (scope=document) прикрепляются к конкретному документу через M2M.
Файлы и заметки хранятся в файловом хранилище, в БД лежит только ключ.

Источники документа заморожены только пока идёт анализ (DocumentLifecycle.SOURCES_LOCKED).
После его завершения источник можно удалить даже при нерассмотренных правках: они
уже сохранены и от источника не зависят, а исчезнут только при следующем анализе.

Просмотр и скачивание идут по той же схеме, что у документов: presigned-ссылка из хранилища
и текст через реестр парсеров. Для url-источника «скачивание» — это сам адрес.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

from app.domain.exceptions import (
    DocumentNotFoundError,
    FileTooLargeError,
    InvalidSourceScopeError,
    SourceLockError,
    SourceNotFoundError,
    UnsupportedFormatError,
)
from app.domain.interfaces.document_parser import IDocumentParserRegistry
from app.domain.interfaces.file_storage import FileStorage, UploadContent
from app.domain.interfaces.unit_of_work import IUnitOfWork
from app.domain.lifecycle import DocumentLifecycle
from app.domain.policies import UploadLimits
from app.domain.source_url import SourceUrl
from app.domain.storage_keys import source_storage_key
from app.domain.value_objects import SourceScopeVO, SourceTypeVO

if TYPE_CHECKING:
    from app.infrastructure.db.models.document import Document
    from app.infrastructure.db.models.project import Project
    from app.infrastructure.db.models.source import Source

logger = logging.getLogger("syncscribe.sources")


class SourceService:
    def __init__(
        self,
        uow: IUnitOfWork,
        file_storage: FileStorage,
        upload_limits: UploadLimits,
        parser_registry: IDocumentParserRegistry | None = None,
        download_url_ttl_seconds: int = 3600,
    ) -> None:
        self._uow = uow
        self._storage = file_storage
        self._upload_limits = upload_limits
        self._parser_registry = parser_registry
        self._download_url_ttl_seconds = download_url_ttl_seconds

    @staticmethod
    def _assert_sources_mutable(document: Document) -> None:
        if not DocumentLifecycle.can_edit_sources(document.status):
            raise SourceLockError(
                f"Нельзя изменить источники документа в статусе '{document.status.value}'. "
                "Дождитесь завершения анализа."
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
        сейчас анализируется.
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
        source_url = SourceUrl.parse(url)
        async with self._uow:
            document = await self._target_document(project.id, scope, document_id)
            source = await self._uow.sources.create_url(
                project_id=project.id, name=name, url=source_url.value, scope=scope
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
            content=UploadContent.from_bytes(
                text_content.encode("utf-8"), "text/plain; charset=utf-8"
            ),
            scope=scope,
            document_id=document_id,
            too_large_message=f"Текст превышает лимит {self._upload_limits.max_size_mb} МБ",
        )

    async def create_file_source(
        self,
        project: Project,
        name: str,
        filename: str,
        content: UploadContent,
        scope: SourceScopeVO = SourceScopeVO.PROJECT,
        document_id: uuid.UUID | None = None,
    ) -> Source:
        return await self._create_stored_source(
            project,
            name=name,
            filename=filename,
            content=content,
            scope=scope,
            document_id=document_id,
            too_large_message=f"Файл превышает лимит {self._upload_limits.max_size_mb} МБ",
        )

    async def _create_stored_source(
        self,
        project: Project,
        *,
        name: str,
        filename: str,
        content: UploadContent,
        scope: SourceScopeVO,
        document_id: uuid.UUID | None,
        too_large_message: str,
    ) -> Source:
        """Загрузить содержимое в хранилище и записать источник в одной транзакции.

        Если запись в БД не удалась, загруженный объект удаляется, чтобы в хранилище
        не оставалось файлов без владельца.
        """
        if self._upload_limits.exceeded_by(content.size):
            raise FileTooLargeError(too_large_message)

        source_id = uuid.uuid4()
        storage_key = source_storage_key(project.id, source_id, filename)
        uploaded = False
        try:
            async with self._uow:
                document = await self._target_document(project.id, scope, document_id)
                await self._storage.upload(storage_key, content)
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
        анализируется. Уже созданные по этому источнику правки не трогаем:
        они остаются до следующего анализа. Файл в хранилище удаляется
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

    # ------------------------------------------------------------------
    # Просмотр и скачивание
    # ------------------------------------------------------------------

    @staticmethod
    def _download_name(source: Source) -> str:
        """Имя файла для браузера: название источника с расширением исходного файла."""
        suffix = Path(source.storage_key or "").suffix
        name = source.name.strip() or "source"
        if suffix and not name.lower().endswith(suffix.lower()):
            return f"{name}{suffix}"
        return name

    async def get_download_url(self, source: Source) -> tuple[str, int | None]:
        """Ссылка на скачивание: presigned URL для файла или сам адрес для url-источника.

        Второй элемент — срок жизни ссылки в секундах; для url-источника он не нужен (None).
        """
        if not source.storage_key:
            if not source.url:
                raise SourceNotFoundError(f"У источника {source.id} нет ни файла, ни ссылки")
            return source.url, None
        expires_in = self._download_url_ttl_seconds
        url = await self._storage.get_presigned_url(
            source.storage_key, expires_in, download_name=self._download_name(source)
        )
        return url, expires_in

    async def get_source_text(self, source: Source) -> str:
        """Текст файлового источника для просмотра.

        UnsupportedFormatError — формат не разбирается (например, .doc); для url-источников
        текста нет — просмотр идёт по ссылке, см. роутер.
        """
        if not source.storage_key:
            raise UnsupportedFormatError("Источник-ссылка открывается по адресу, текста у него нет")
        if self._parser_registry is None:
            raise UnsupportedFormatError("Просмотр источников не настроен")
        raw_bytes = await self._storage.download(source.storage_key)
        return self._parser_registry.parse_by_filename(source.storage_key, raw_bytes).plain_text
