"""
Сборка финального документа: скачивает исходный файл из Minio, применяет
принятые правки через нужный DocumentExporter и сохраняет результат обратно
в хранилище.

H2.2: убраны прямые импорты из infrastructure.db.models;
      DocumentFormat берётся из app.domain.enums;
      Document используется только через TYPE_CHECKING.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain.enums import DocumentFormat
from app.domain.interfaces.file_storage import FileStorage
from app.domain.services.suggestion_service import SuggestionService
from app.infrastructure.exporters.exporter_registry import DocumentExporterRegistry

if TYPE_CHECKING:
    from app.infrastructure.db.models.document import Document

_MEDIA_TYPES: dict[DocumentFormat, str] = {
    DocumentFormat.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    DocumentFormat.DOC: "application/msword",
    DocumentFormat.TXT: "text/plain",
    DocumentFormat.MARKDOWN: "text/markdown",
}


class DocumentExportService:
    def __init__(
        self,
        file_storage: FileStorage,
        exporter_registry: DocumentExporterRegistry,
        suggestion_service: SuggestionService,
    ) -> None:
        self._storage = file_storage
        self._exporters = exporter_registry
        self._suggestions = suggestion_service

    async def export_document(
        self, document: "Document"
    ) -> tuple[bytes, str, str]:
        """Вернуть (байты, имя файла, media-type) для скачивания."""
        exported_bytes, title = await self._build(document)
        media_type = _MEDIA_TYPES.get(
            document.format, "application/octet-stream"
        )
        return exported_bytes, title, media_type

    async def export_and_save(self, document: "Document") -> None:
        """Применить принятые правки и перезаписать файл в хранилище.

        Вызывается из SuggestionService._run_export при финализации review.
        """
        exported_bytes, _title = await self._build(document)
        await self._storage.upload(
            document.storage_key,
            exported_bytes,
            _MEDIA_TYPES.get(document.format, "application/octet-stream"),
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _build(self, document: "Document") -> tuple[bytes, str]:
        """Скачать, применить правки, вернуть (bytes, title)."""
        raw_bytes = await self._storage.download(document.storage_key)
        changes = await self._suggestions.get_accepted_changes(document.id)
        exporter = self._exporters.get_exporter(document.format)
        exported_bytes = exporter.apply_changes(raw_bytes, changes)
        return exported_bytes, document.title
