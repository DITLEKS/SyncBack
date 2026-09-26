"""
Сборка финального документа: скачивает исходный файл из Minio, применяет принятые правки
через нужный DocumentExporter и возвращает готовые байты + имя файла + media type.

Экспорт принятых правок реализован через курсорный обход страниц (PAGE_SIZE = 500),
чтобы ограничить пиковое потребление памяти при документах с большим количеством правок.
Публичный метод get_accepted_changes в SuggestionService намеренно не имеет лимита —
он остаётся основным API для небольших и тестовых сценариев.

Доступ к ISuggestionRepository осуществляется через IUnitOfWork.suggestions —
не через приватные атрибуты SuggestionService.
"""
from __future__ import annotations

import uuid

from app.domain.interfaces.document_exporter import AppliedChange
from app.domain.interfaces.file_storage import FileStorage
from app.domain.interfaces.unit_of_work import IUnitOfWork
from app.domain.value_objects import SuggestionStatusVO
from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.enums import DocumentFormat
from app.infrastructure.exporters.exporter_registry import DocumentExporterRegistry

_MEDIA_TYPES: dict[DocumentFormat, str] = {
    DocumentFormat.DOCX: "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    DocumentFormat.DOC: "application/msword",
    DocumentFormat.TXT: "text/plain",
    DocumentFormat.MARKDOWN: "text/markdown",
}

# Размер страницы при курсорном обходе принятых правок.
# 500 строк — компромисс между количеством round-trip к БД и пиком памяти.
_EXPORT_PAGE_SIZE: int = 500


class DocumentExportService:
    def __init__(
        self,
        file_storage: FileStorage,
        exporter_registry: DocumentExporterRegistry,
        uow: IUnitOfWork,
    ) -> None:
        self._storage = file_storage
        self._exporters = exporter_registry
        self._uow = uow

    async def _iter_accepted_changes_pages(
        self, document: Document
    ) -> list[AppliedChange]:
        """Курсорный обход принятых правок страницами по _EXPORT_PAGE_SIZE.

        Обращается к ISuggestionRepository через IUnitOfWork.suggestions —
        без проникновения в приватные атрибуты SuggestionService.

        Возвращает полный список AppliedChange, но загружает данные порциями,
        не материализуя весь набор ORM-объектов единовременно в identity-map
        SQLAlchemy. Это ограничивает пиковый расход памяти при документах
        с тысячами правок.

        Используется только внутри export_document. Внешние потребители
        должны обращаться к SuggestionService.get_accepted_changes.
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

    async def export_document(self, document: Document) -> tuple[bytes, str, str]:
        raw_bytes = await self._storage.download(document.storage_key)
        # Используем постраничный обход вместо однократного запроса всего набора,
        # чтобы ограничить пиковую нагрузку на память при больших документах.
        changes = await self._iter_accepted_changes_pages(document)
        exporter = self._exporters.get_exporter(document.format)
        exported_bytes = exporter.apply_changes(raw_bytes, changes)
        media_type = _MEDIA_TYPES.get(document.format, "application/octet-stream")
        return exported_bytes, document.title, media_type
