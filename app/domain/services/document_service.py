"""
Бизнес-логика документов.

Архитектурные правила:
  - Зависит только от IUnitOfWork (порт), FileStorage (порт)
    и DocumentParserRegistry (инфра-singleton без сайд-эффектов).
  - Нет module-level импортов из app.infrastructure.db.*.
  - Один uow.commit() на операцию.

ИСПРАВЛЕНИЯ:
- CRIT-3 / H-NEW-1: _FORMAT_MAP убран полностью. Вместо global-переменной
  используется @functools.cache на _extension_to_format() — потокобезопасно,
  не требует global statement, кэшируется автоматически.
- HIGH-2: delete_document сначала commit(), потом MinIO.
- M-3: upload_document принимает project_id: UUID вместо ORM-объекта Project.
- H-5: ORM-объект Document создаётся внутри репозитория через фабричный метод.

CRIT-NEW-2: list_documents передаёт PaginationParams-объект, а не limit/offset позиционно.
LOW: exc_info=True добавлен в logger.warning внутри delete_document.
"""
from __future__ import annotations

import functools
import logging
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from app.core.config import Settings, get_settings
from app.domain.exceptions import (
    DocumentNotFoundError,
    FileTooLargeError,
    UnsupportedFileFormatError,
)
from app.domain.interfaces.document_parser import ParsedDocument
from app.domain.interfaces.file_storage import FileStorage
from app.domain.interfaces.unit_of_work import IUnitOfWork
from app.domain.value_objects import DocumentStatusVO, KeysetPage, PaginationParams
from app.infrastructure.parsers.parser_registry import DocumentParserRegistry

if TYPE_CHECKING:
    from app.infrastructure.db.models.document import Document

logger = logging.getLogger("syncscribe.services.document")


@functools.cache
def _get_format_map() -> dict[str, object]:
    """H-NEW-1: ленивый импорт + кэш через functools.cache (без global).

    Первый вызов строит словарь и кэширует его навсегда в рамках процесса.
    functools.cache потокобезопасен для read-after-first-write — GIL гарантирует
    единственное создание словаря при первом вызове.
    """
    from app.infrastructure.db.models.enums import DocumentFormat  # noqa: PLC0415
    return {
        ".docx": DocumentFormat.DOCX,
        ".txt": DocumentFormat.TXT,
        ".md": DocumentFormat.MARKDOWN,
        ".markdown": DocumentFormat.MARKDOWN,
    }


def _extension_to_format(suffix: str):
    """Лукап: расширение → ORM DocumentFormat enum."""
    fmt = _get_format_map().get(suffix)
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
        parser_registry: DocumentParserRegistry | None = None,
        settings: Settings | None = None,
    ):
        self._uow = uow
        self._storage = file_storage
        self._parser_registry = parser_registry or DocumentParserRegistry()
        self._settings = settings or get_settings()

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def _resolve_format(self, filename: str):
        return _extension_to_format(Path(filename).suffix.lower())

    async def upload_document(
        self,
        project_id: uuid.UUID,
        filename: str,
        content: bytes,
        content_type: str,
    ) -> "Document":
        """Загрузить документ в MinIO и создать запись в БД.

        M-3: принимает project_id: UUID, а не ORM-объект Project.
        """
        if len(content) > self._settings.max_upload_size_bytes:
            raise FileTooLargeError(
                f"Файл превышает лимит {self._settings.max_upload_size_mb} МБ"
            )
        document_format = self._resolve_format(filename)
        document_id = uuid.uuid4()
        storage_key = f"projects/{project_id}/documents/{document_id}/{filename}"
        await self._storage.upload(storage_key, content, content_type)

        try:
            async with self._uow:
                # H-5: ORM-объект строится внутри репозитория — сервис не знает про Document ORM.
                saved = await self._uow.documents.create(
                    id=document_id,
                    project_id=project_id,
                    title=filename,
                    format=document_format,
                    storage_key=storage_key,
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
    ) -> tuple[list["Document"], int]:
        """CRIT-NEW-2: передаём pagination-объект целиком, не limit/offset позиционно."""
        async with self._uow:
            items = await self._uow.documents.list_for_project(
                project_id, pagination
            )
            total = await self._uow.documents.count_for_project(project_id)
        return items, total

    async def get_document(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> "Document":
        async with self._uow:
            document = await self._uow.documents.get_by_id(document_id)
        if document is None or document.project_id != project_id:
            raise DocumentNotFoundError(
                f"Документ {document_id} не найден в проекте {project_id}"
            )
        return document

    async def list_all_for_user(
        self,
        owner_id: uuid.UUID,
        *,
        status: DocumentStatusVO | None = None,
        search: str | None = None,
        sort_by: Literal["created_at", "updated_at", "title"] = "updated_at",
        sort_dir: Literal["asc", "desc"] = "desc",
        pagination: PaginationParams | None = None,
    ) -> tuple[list["Document"], int]:
        """Список всех документов пользователя с агрегированными счётчиками правок.

        M-NEW-3: тип возврата выровнен с IDocumentRepository.list_all_for_user
        — tuple[list[Document], int], не tuple[list[dict], int].
        """
        _pagination = pagination or PaginationParams(limit=50, offset=0)
        async with self._uow:
            return await self._uow.documents.list_all_for_user(
                owner_id,
                status=status,
                search=search,
                sort_by=sort_by,
                sort_dir=sort_dir,
                limit=_pagination.limit,
                offset=_pagination.offset,
            )

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    async def delete_document(self, document: "Document") -> None:
        """
        HIGH-2: удаление документа.

        Порядок: commit() сначала, MinIO-удаление потом.
        """
        storage_key = document.storage_key
        original_key: str | None = getattr(document, "original_storage_key", None)

        # 1. Удаляем запись из БД.
        async with self._uow:
            await self._uow.documents.delete(document)
            await self._uow.commit()

        # 2. Удаляем файлы из MinIO (бест-эффорт).
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

    async def get_download_url(self, document: "Document") -> tuple[str, int]:
        expires_in = self._settings.minio_presigned_url_expire_seconds
        url = await self._storage.get_presigned_url(document.storage_key, expires_in)
        return url, expires_in

    async def get_document_content(self, document: "Document") -> ParsedDocument:
        raw_bytes = await self._storage.download(document.storage_key)
        return self._parser_registry.parse_by_filename(document.storage_key, raw_bytes)

    async def get_original_content(self, document: "Document") -> ParsedDocument:
        """Режим «Оригинал» (#7): вернуть текст до правок."""
        original_key: str | None = getattr(document, "original_storage_key", None)
        storage_key = original_key or document.storage_key
        raw_bytes = await self._storage.download(storage_key)
        return self._parser_registry.parse_by_filename(storage_key, raw_bytes)
