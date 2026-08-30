"""
Бизнес-логика документов: загрузка в Minio, определение формата по расширению,
получение ссылки на скачивание, получение распарсенного текста для инлайн-отображения правок.

ИСПРАВЛЕНО:
1. list_documents принимает limit/offset и возвращает (items, total).
2. Добавлен get_document_content() — раньше фронтенд мог получить только presigned URL
   на сырой файл (download), а распарсенный текст с позициями секций (DocumentParser/
   DocumentSection) использовался только внутри Celery-пайплайна анализа и не отдавался
   наружу. Без этого фронтенд не может сопоставить suggestion.section_ref с конкретным
   местом в тексте для инлайн-отображения правок в редакторе.
"""

import uuid
from pathlib import Path

from app.core.config import Settings, get_settings
from app.domain.exceptions import DocumentNotFoundError, FileTooLargeError, UnsupportedFileFormatError
from app.domain.interfaces.document_parser import ParsedDocument
from app.domain.interfaces.file_storage import FileStorage
from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.enums import DocumentFormat
from app.infrastructure.db.models.project import Project
from app.infrastructure.db.repositories.document_repository import DocumentRepository
from app.infrastructure.parsers.parser_registry import DocumentParserRegistry

_EXTENSION_TO_FORMAT: dict[str, DocumentFormat] = {
    ".doc": DocumentFormat.DOC,
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
                f"Допустимые форматы: {', '.join(sorted(e.value for e in DocumentFormat))}"
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

    async def get_download_url(self, document: Document) -> tuple[str, int]:
        expires_in = self._settings.minio_presigned_url_expire_seconds
        url = await self._storage.get_presigned_url(document.storage_key, expires_in)
        return url, expires_in

    async def get_document_content(self, document: Document) -> ParsedDocument:
        raw_bytes = await self._storage.download(document.storage_key)
        return self._parser_registry.parse_by_filename(document.storage_key, raw_bytes)

    async def attach_sources(self, document: Document, sources: list) -> Document:
        return await self._documents.attach_sources(document, sources)
