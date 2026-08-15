"""
Сборка финального документа: скачивает исходный файл из Minio, применяет принятые правки
через нужный DocumentExporter и возвращает готовые байты + имя файла + media type.
"""
from app.domain.interfaces.file_storage import FileStorage
from app.domain.services.suggestion_service import SuggestionService
from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.enums import DocumentFormat
from app.infrastructure.exporters.exporter_registry import DocumentExporterRegistry

_MEDIA_TYPES: dict[DocumentFormat, str] = {
    DocumentFormat.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    DocumentFormat.DOC: "application/msword",
    DocumentFormat.TXT: "text/plain",
    DocumentFormat.MARKDOWN: "text/markdown",
}


class DocumentExportService:
    def __init__(self, file_storage: FileStorage, exporter_registry: DocumentExporterRegistry, suggestion_service: SuggestionService):
        self._storage = file_storage
        self._exporters = exporter_registry
        self._suggestions = suggestion_service

    async def export_document(self, document: Document) -> tuple[bytes, str, str]:
        raw_bytes = await self._storage.download(document.storage_key)
        changes = await self._suggestions.get_accepted_changes(document.id)
        exporter = self._exporters.get_exporter(document.format)
        exported_bytes = exporter.apply_changes(raw_bytes, changes)
        media_type = _MEDIA_TYPES.get(document.format, "application/octet-stream")
        return exported_bytes, document.title, media_type
