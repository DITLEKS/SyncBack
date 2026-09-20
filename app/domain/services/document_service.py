"""
Бизнес-логика документов.

ДОБАВЛЕНО:
- delete_document() — удаляет MinIO-файл (best-effort), затем запись в БД.
"""

import logging
import uuid
from pathlib import Path
from typing import Literal

from app.core.config import Settings, get_settings
from app.domain.exceptions import DocumentNotFoundError, FileTooLargeError, UnsupportedFileFormatError
from app.domain.interfaces.document_parser import ParsedDocument
from app.domain.interfaces.file_storage import FileStorage
from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.enums import DocumentFormat, DocumentStatus
from app.infrastructure.db.models.project import Project
from app.infrastructure.db.repositories.document_repository import DocumentRepository
from app.infrastructure.parsers.parser_registry import DocumentParserRegistry

logger = logging.getLogger("syncscribe.services.document")

_EXTENSION_TO_FORMAT: dict[str, DocumentFormat] = {
    ".docx": DocumentFormat.DOCX,
    ".txt": DocumentFormat.TXT,
    ".md": DocumentFormat.MARKDOWN,
    ".markdown": DocumentFormat.MARKDOWN,
}


class DocumentService:
    def __init__(
        self,
        document_repository: DocumentRepository,
        file_storage: FileStorage,
        parser_registry: DocumentParserRegistry | None = None,
        settings: Settings | None = None,
    ):
        self._documents = document_repository
        self._storage = file_storage
        self._parser_registry = parser_registry or DocumentParserRegistry()
        self._settings = settings or get_settings()

    def _resolve_format(self, filename: str) -> DocumentFormat:
        suffix = Path(filename).suffix.lower()
        document_format = _EXTENSION_TO_FORMAT.get(suffix)
        if document_format is None:
            raise UnsupportedFileFormatError(
                f"Формат '{suffix or 'без расширения'}' не поддерживается. "
                f"Допустимые форматы: docx, txt, md"
            )
        return document_format

    async def upload_document(
        self, project: Project, filename: str, content: bytes, content_type: str
    ) -> Document:
        if len(content) > self._settings.max_upload_size_bytes:
            raise FileTooLargeError(f"Файл превышает лимит {self._settings.max_upload_size_mb} МБ")

        document_format = self._resolve_format(filename)
        document_id = uuid.uuid4()
        storage_key = f"projects/{project.id}/documents/{document_id}/{filename}"

        await self._storage.upload(storage_key, content, content_type)

        document = Document(
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

    async def list_documents(self, project_id: uuid.UUID, limit: int, offset: int) -> tuple[list[Document], int]:
        items = await self._documents.list_by_project(project_id, limit=limit, offset=offset)
        total = await self._documents.count_by_project(project_id)
        return items, total

    async def get_document(self, project_id: uuid.UUID, document_id: uuid.UUID) -> Document:
        document = await self._documents.get_by_id(document_id)
        if document is None or document.project_id != project_id:
            raise DocumentNotFoundError(f"Документ {document_id} не найден в проекте {project_id}")
        return document

    async def delete_document(self, document: Document) -> None:
        """
        Удаление документа:
        1. Удаляем файл из MinIO (best-effort — не блокируем удаление при отсутствии файла).
        2. Удаляем запись из БД — ON DELETE CASCADE уберёт suggestions, analysis_jobs,
           document_sources.
        """
        if document.storage_key:
            try:
                await self._storage.delete(document.storage_key)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Не удалось удалить файл из MinIO при удалении документа",
                    extra={"storage_key": document.storage_key, "document_id": str(document.id)},
                )
        await self._documents.delete(document)

    async def get_download_url(self, document: Document) -> tuple[str, int]:
        expires_in = self._settings.minio_presigned_url_expire_seconds
        url = await self._storage.get_presigned_url(document.storage_key, expires_in)
        return url, expires_in

    async def get_document_content(self, document: Document) -> ParsedDocument:
        raw_bytes = await self._storage.download(document.storage_key)
        return self._parser_registry.parse_by_filename(document.storage_key, raw_bytes)

    async def attach_sources(self, document: Document, sources: list) -> Document:
        return await self._documents.attach_sources(document, sources)

    # -------------------------------------------------------------------------
    # P0-4: глобальный список документов пользователя
    # -------------------------------------------------------------------------

    async def list_all_for_user(
        self,
        owner_id: uuid.UUID,
        *,
        status: DocumentStatus | None = None,
        search: str | None = None,
        sort_by: Literal["created_at", "updated_at", "title"] = "updated_at",
        sort_dir: Literal["asc", "desc"] = "desc",
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """Список всех документов пользователя с агрегированными счётчиками правок."""
        return await self._documents.list_all_for_user(
            owner_id,
            status=status,
            search=search,
            sort_by=sort_by,
            sort_dir=sort_dir,
            limit=limit,
            offset=offset,
        )
