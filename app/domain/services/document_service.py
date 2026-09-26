"""
Бизнес-логика документов.

Архитектурные правила (DDD):
  - Сервис зависит только от абстракций (IDocumentRepository, FileStorage,
    DocumentParserRegistry), Settings и domain VO.
  - ORM-модели (кроме Document как возвращаемого результата) под TYPE_CHECKING.
  - ORM-enum DocumentStatus/DocumentFormat импортируются локально там, где нужны.
  - Непосредственная зависимость от DocumentRepository (конкретный класс) удалена.

ИЗМЕНЕНИЯ:
  - __init__ работает через IDocumentRepository (порт), не DocumentRepository.
  - list_documents / list_all_for_user принимают PaginationParams.
  - DocumentStatus/DocumentFormat ORM-enum импортируются отложенно.
  - Project импортируется под TYPE_CHECKING.
"""
from __future__ import annotations

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
from app.domain.interfaces.repositories import IDocumentRepository
from app.domain.value_objects import DocumentStatusVO, PaginationParams
from app.infrastructure.parsers.parser_registry import DocumentParserRegistry

if TYPE_CHECKING:
    from app.infrastructure.db.models.document import Document
    from app.infrastructure.db.models.project import Project

logger = logging.getLogger("syncscribe.services.document")


def _extension_to_format(suffix: str):
    """Отложенный лукап: расширение → ORM DocumentFormat enum."""
    from app.infrastructure.db.models.enums import DocumentFormat
    _MAP = {
        ".docx": DocumentFormat.DOCX,
        ".txt": DocumentFormat.TXT,
        ".md": DocumentFormat.MARKDOWN,
        ".markdown": DocumentFormat.MARKDOWN,
    }
    fmt = _MAP.get(suffix)
    if fmt is None:
        raise UnsupportedFileFormatError(
            f"Формат '{suffix or 'без расширения'}' не поддерживается. "
            "Допустимые форматы: docx, txt, md"
        )
    return fmt


class DocumentService:
    def __init__(
        self,
        document_repository: IDocumentRepository,   # порт, не конкретный класс
        file_storage: FileStorage,
        parser_registry: DocumentParserRegistry | None = None,
        settings: Settings | None = None,
    ):
        self._documents = document_repository
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
        project: "Project",
        filename: str,
        content: bytes,
        content_type: str,
    ) -> "Document":
        if len(content) > self._settings.max_upload_size_bytes:
            raise FileTooLargeError(
                f"Файл превышает лимит {self._settings.max_upload_size_mb} МБ"
            )
        document_format = self._resolve_format(filename)
        document_id = uuid.uuid4()
        storage_key = f"projects/{project.id}/documents/{document_id}/{filename}"
        await self._storage.upload(storage_key, content, content_type)

        from app.infrastructure.db.models.document import Document as DocumentModel
        document = DocumentModel(
            id=document_id,
            project_id=project.id,
            title=filename,
            format=document_format,
            storage_key=storage_key,
        )
        try:
            return await self._documents.create(document)
        except Exception:
            await self._storage.delete(storage_key)
            raise

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def list_documents(
        self,
        project_id: uuid.UUID,
        pagination: PaginationParams,
    ) -> tuple[list[Document], int]:
        items = await self._documents.list_for_project(project_id, pagination)
        total = await self._documents.count_for_project(project_id)
        return items, total

    async def get_document(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> "Document":
        document = await self._documents.get_by_id(document_id)
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
    ) -> tuple[list[dict], int]:
        """Список всех документов пользователя с агрегированными счётчиками правок."""
        _pagination = pagination or PaginationParams(limit=50, offset=0)
        # Перевод VO → ORM-enum выполняется на стороне репозитория
        return await self._documents.list_all_for_user(
            owner_id,
            status=status,
            search=search,
            sort_by=sort_by,
            sort_dir=sort_dir,
            pagination=_pagination,
        )

    # ------------------------------------------------------------------
    # File operations
    # ------------------------------------------------------------------

    async def delete_document(self, document: "Document") -> None:
        """
        1. Удаляем файлы из MinIO (best-effort).
        2. Удаляем запись из БД — ON DELETE CASCADE уберёт связанные сущности.
        """
        keys = {
            k
            for k in [
                document.storage_key,
                getattr(document, "original_storage_key", None),
            ]
            if k
        }
        for key in keys:
            try:
                await self._storage.delete(key)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Не удалось удалить файл из MinIO",
                    extra={"storage_key": key, "document_id": str(document.id)},
                )
        await self._documents.delete(document)

    async def get_download_url(
        self, document: "Document"
    ) -> tuple[str, int]:
        expires_in = self._settings.minio_presigned_url_expire_seconds
        url = await self._storage.get_presigned_url(document.storage_key, expires_in)
        return url, expires_in

    async def get_document_content(
        self, document: "Document"
    ) -> ParsedDocument:
        raw_bytes = await self._storage.download(document.storage_key)
        return self._parser_registry.parse_by_filename(document.storage_key, raw_bytes)

    async def get_original_content(
        self, document: "Document"
    ) -> ParsedDocument:
        """Pежим «Оригинал»: вернуть текст до правок.

        Пайплайн анализа записывает снапшот исходного файла в MinIO под
        ключом original_storage_key. Если ключ отсутствует — отдаём текущий файл.
        """
        original_key: str | None = getattr(document, "original_storage_key", None)
        storage_key = original_key or document.storage_key
        raw_bytes = await self._storage.download(storage_key)
        return self._parser_registry.parse_by_filename(storage_key, raw_bytes)

    async def attach_sources(
        self, document: "Document", sources: list
    ) -> "Document":
        return await self._documents.attach_sources(document, sources)
