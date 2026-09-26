"""
DocumentExportService — скачивает исходный файл из MinIO,
применяет принятые правки через DocumentExporter и возвращает
готовые байты + имя файла + media type.

Экспорт принятых правок реализован через курсорный обход страниц
(PAGE_SIZE = 500), чтобы ограничить пиковое потребление памяти
при документах с большим количеством правок.

Архитектурные правила:
  - Зависит только от IUnitOfWork (порт), FileStorage (порт)
    и DocumentExporterRegistry (порт).
  - Нет импортов из app.infrastructure.* при выполнении.
  - Не вызывает SuggestionService — принятые правки читаются
    напрямую через uow.suggestions, устраняя circular dependency.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain.interfaces.document_exporter import AppliedChange
from app.domain.interfaces.file_storage import FileStorage
from app.domain.interfaces.unit_of_work import IUnitOfWork
from app.domain.value_objects import DocumentFormatVO, SuggestionStatusVO

if TYPE_CHECKING:
    from app.domain.interfaces.exporter_registry import DocumentExporterRegistry
    from app.infrastructure.db.models.document import Document

_MEDIA_TYPES: dict[DocumentFormatVO, str] = {
    DocumentFormatVO.DOCX: (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    DocumentFormatVO.DOC: "application/msword",
    DocumentFormatVO.TXT: "text/plain",
    DocumentFormatVO.MARKDOWN: "text/markdown",
}

# Размер страницы при курсорном обходе принятых правок.
# 500 строк — компромисс между количеством round-trip к БД и пиком памяти.
_EXPORT_PAGE_SIZE: int = 500


class DocumentExportService:
    def __init__(
        self,
        uow: IUnitOfWork,
        file_storage: FileStorage,
        exporter_registry: "DocumentExporterRegistry",
    ) -> None:
        self._uow = uow
        self._storage = file_storage
        self._exporters = exporter_registry

    async def export_document(
        self, document: "Document"
    ) -> tuple[bytes, str, str]:
        """Вернуть (bytes, filename, media_type) финального документа."""
        raw_bytes = await self._storage.download(document.storage_key)
        # Постраничный обход вместо однократной загрузки всего набора.
        changes = await self._iter_accepted_changes_pages(document)
        exporter = self._exporters.get_exporter(document.format)
        exported_bytes = exporter.apply_changes(raw_bytes, changes)
        media_type = _MEDIA_TYPES.get(document.format, "application/octet-stream")
        return exported_bytes, document.title, media_type

    async def export_and_save(
        self, document: "Document"
    ) -> None:
        """Применить правки, загрузить результат в MinIO и обновить storage_key."""
        exported_bytes, _, media_type = await self.export_document(document)
        export_key = f"{document.storage_key}.exported"
        await self._storage.upload(export_key, exported_bytes, media_type)
        async with self._uow:
            await self._uow.documents.update_exported_key(document, export_key)
            await self._uow.commit()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _iter_accepted_changes_pages(
        self, document: "Document"
    ) -> list[AppliedChange]:
        """Курсорный обход принятых правок страницами по _EXPORT_PAGE_SIZE.

        Обращается к ISuggestionRepository через IUnitOfWork.suggestions —
        без проникновения в приватные атрибуты SuggestionService.

        Останавливается, когда:
          - страница пуста (первая страница или за данными нет ничего), или
          - последняя страница (количество записей < _EXPORT_PAGE_SIZE).
        """
        if document.current_analysis_job_id is None:
            return []

        changes: list[AppliedChange] = []
        offset = 0

        async with self._uow:
            while True:
                page = await self._uow.suggestions.list_by_analysis_job_and_status_page(
                    document.current_analysis_job_id,
                    SuggestionStatusVO.ACCEPTED,
                    limit=_EXPORT_PAGE_SIZE,
                    offset=offset,
                )
                if not page:
                    break
                changes.extend(
                    AppliedChange(
                        section_ref=s.section_ref,
                        change_type=s.change_type.value,
                        old_text=s.old_text,
                        new_text=s.new_text,
                    )
                    for s in page
                )
                if len(page) < _EXPORT_PAGE_SIZE:
                    # Последняя страница — дальше данных нет.
                    break
                offset += _EXPORT_PAGE_SIZE

        return changes
