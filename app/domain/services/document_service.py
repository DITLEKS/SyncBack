"""Бизнес-логика документов: загрузка, список, содержимое, удаление.

Зависит только от портов домена (IUnitOfWork, FileStorage, IDocumentParserRegistry)
и ограничений UploadLimits; один uow.commit() на операцию.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from app.domain.exceptions import (
    DocumentNotFoundError,
    FileTooLargeError,
    InvalidDocumentStatusError,
    UnsupportedFileFormatError,
)
from app.domain.interfaces.document_parser import IDocumentParserRegistry, ParsedDocument
from app.domain.interfaces.file_storage import FileStorage
from app.domain.interfaces.unit_of_work import IUnitOfWork
from app.domain.lifecycle import DocumentLifecycle
from app.domain.policies import UploadLimits
from app.domain.value_objects import (
    DocumentFormatVO,
    DocumentStatusVO,
    KeysetPage,
    PaginationParams,
)

if TYPE_CHECKING:
    from app.infrastructure.db.models.document import Document

logger = logging.getLogger("syncscribe.services.document")


_FORMAT_BY_EXTENSION: dict[str, DocumentFormatVO] = {
    ".docx": DocumentFormatVO.DOCX,
    ".txt": DocumentFormatVO.TXT,
    ".md": DocumentFormatVO.MARKDOWN,
    ".markdown": DocumentFormatVO.MARKDOWN,
}


def _extension_to_format(suffix: str) -> DocumentFormatVO:
    fmt = _FORMAT_BY_EXTENSION.get(suffix)
    if fmt is None:
        raise UnsupportedFileFormatError(
            f"Формат '{suffix or 'без расширения'}' не поддерживается. "
            "Допустимые форматы: docx, txt, md"
        )
    return fmt


class DocumentService:
    def __init__(
        self,
        uow: IUnitOfWork,
        file_storage: FileStorage,
        parser_registry: IDocumentParserRegistry,
        upload_limits: UploadLimits,
        download_url_ttl_seconds: int,
    ):
        self._uow = uow
        self._storage = file_storage
        self._parser_registry = parser_registry
        self._upload_limits = upload_limits
        self._download_url_ttl_seconds = download_url_ttl_seconds

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def _resolve_format(self, filename: str) -> DocumentFormatVO:
        return _extension_to_format(Path(filename).suffix.lower())

    async def upload_document(
        self,
        project_id: uuid.UUID,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> Document:
        """Загрузить документ в хранилище и создать запись в БД."""
        if self._upload_limits.exceeded_by(len(content)):
            raise FileTooLargeError(f"Файл превышает лимит {self._upload_limits.max_size_mb} МБ")
        document_format = self._resolve_format(filename)
        document_id = uuid.uuid4()
        storage_key = f"projects/{project_id}/documents/{document_id}/{filename}"
        await self._storage.upload(storage_key, content, content_type)

        try:
            async with self._uow:
                saved = await self._uow.documents.create(
                    id=document_id,
                    project_id=project_id,
                    name=filename,
                    format=document_format,
                    storage_key=storage_key,
                    size_bytes=len(content),
                )
                await self._uow.commit()
        except Exception:
            await self._storage.delete(storage_key)
            raise
        return saved

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def list_documents(
        self,
        project_id: uuid.UUID,
        pagination: PaginationParams | KeysetPage,
        *,
        status_filter: DocumentStatusVO | None = None,
    ) -> tuple[list[Document], int]:
        """Постраничный список документов проекта.

        status_filter — опциональный фильтр по статусу (пробрасывается в репозиторий).
        CRIT-NEW-2: передаём pagination-объект целиком, не limit/offset позиционно.
        """
        async with self._uow:
            items = await self._uow.documents.list_for_project(
                project_id, pagination, status=status_filter
            )
            total = await self._uow.documents.count_for_project(project_id, status=status_filter)
        return items, total

    async def get_document(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> Document:
        async with self._uow:
            document = await self._uow.documents.get_by_id(document_id)
        if document is None or document.project_id != project_id:
            raise DocumentNotFoundError(f"Документ {document_id} не найден в проекте {project_id}")
        return document

    async def list_all_for_user(
        self,
        owner_id: uuid.UUID,
        *,
        status: DocumentStatusVO | None = None,
        outdated: bool = False,
        search: str | None = None,
        sort_by: Literal["created_at", "updated_at", "name"] = "updated_at",
        sort_dir: Literal["asc", "desc"] = "desc",
        pagination: PaginationParams | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Список всех документов пользователя с агрегированными счётчиками правок.

        BUG-FIX-2: принимает outdated: bool, пробрасывает в репозиторий.
        BUG-FIX-3: возвращает tuple[list[dict], int] — репозиторий формирует dict
        с ключами document / project_name / suggestions_*.
        """
        _pagination = pagination or PaginationParams(limit=50, offset=0)
        async with self._uow:
            return await self._uow.documents.list_all_for_user(
                owner_id,
                status=status,
                outdated=outdated,
                search=search,
                sort_by=sort_by,
                sort_dir=sort_dir,
                limit=_pagination.limit,
                offset=_pagination.offset,
            )

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    async def delete_document(self, document: Document) -> None:
        """
        HIGH-2: удаление документа.

        Порядок операций внутри транзакции:
          1. delete_document_scoped_sources — удаляем orphan-источники scope=DOCUMENT
             ДО flush document, пока document_sources ещё существуют (подзапрос их читает).
          2. delete(document) — удаляем саму запись документа;
             FK-каскад по document_sources срабатывает здесь.
          3. commit() — единственный коммит на операцию.
        После коммита — best-effort удаление файлов из MinIO.

        Используется когда ORM-объект уже загружен.
        Для удаления только по ID без предварительного SELECT — см. delete_document_by_id.
        """
        storage_key = document.storage_key
        original_key: str | None = getattr(document, "original_storage_key", None)

        async with self._uow:
            await self._uow.documents.delete_document_scoped_sources(document.id)
            await self._uow.documents.delete(document)
            await self._uow.commit()

        keys_to_delete = {k for k in [storage_key, original_key] if k}
        for key in keys_to_delete:
            try:
                await self._storage.delete(key)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Не удалось удалить файл из MinIO после удаления документа",
                    exc_info=True,
                    extra={"storage_key": key, "document_id": str(document.id)},
                )

    async def delete_document_by_id(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> None:
        """Удалить документ проекта вместе с его собственными источниками.

        Порядок операций внутри транзакции:
          1. delete_document_scoped_sources — удаляем orphan-источники scope=DOCUMENT
             ДО flush документа, пока document_sources ещё существуют (подзапрос их читает).
          2. delete_by_id — DELETE FROM documents RETURNING storage_key.
             FK-каскад по document_sources срабатывает здесь.
          3. commit() — единственный коммит на операцию.
        После коммита — best-effort удаление файлов из MinIO.

        DocumentNotFoundError — документа нет в проекте;
        InvalidDocumentStatusError — документ сейчас анализируется.
        """
        async with self._uow:
            document = await self._uow.documents.get_by_id(document_id)
            if document is None or document.project_id != project_id:
                raise DocumentNotFoundError(
                    f"Документ {document_id} не найден в проекте {project_id}"
                )
            if not DocumentLifecycle.can_delete(document.status):
                raise InvalidDocumentStatusError(
                    "Нельзя удалить документ, пока идёт анализ. Сначала отмените задачу."
                )
            await self._uow.documents.delete_document_scoped_sources(document_id)
            deleted = await self._uow.documents.delete_by_id(
                document_id=document_id,
                project_id=project_id,
            )
            if deleted is None:
                raise DocumentNotFoundError(
                    f"Документ {document_id} не найден в проекте {project_id}"
                )
            storage_key: str = deleted["storage_key"]
            original_key: str | None = deleted.get("original_storage_key")
            await self._uow.commit()

        keys_to_delete = {k for k in [storage_key, original_key] if k}
        for key in keys_to_delete:
            try:
                await self._storage.delete(key)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Не удалось удалить файл из MinIO (delete_by_id)",
                    exc_info=True,
                    extra={"storage_key": key, "document_id": str(document_id)},
                )

    async def get_download_url(self, document: Document) -> tuple[str, int]:
        expires_in = self._download_url_ttl_seconds
        url = await self._storage.get_presigned_url(document.storage_key, expires_in)
        return url, expires_in

    async def get_document_content(self, document: Document) -> ParsedDocument:
        raw_bytes = await self._storage.download(document.storage_key)
        return self._parser_registry.parse_by_filename(document.storage_key, raw_bytes)

    async def get_original_content(self, document: Document) -> ParsedDocument:
        """Режим «Оригинал» (#7): вернуть текст до правок."""
        original_key: str | None = getattr(document, "original_storage_key", None)
        storage_key = original_key or document.storage_key
        raw_bytes = await self._storage.download(storage_key)
        return self._parser_registry.parse_by_filename(storage_key, raw_bytes)
