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
  - _iter_accepted_changes_pages НЕ управляет контекстом UoW —
    вызывающий обязан открыть `async with self._uow` до вызова.
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
        """Вернуть (bytes, filename, media_type) финального документа.

        Открывает единственный UoW-контекст для чтения принятых правок.
        Загрузка файла из хранилища выполняется вне UoW — I/O независим
        от транзакции БД.
        """
        raw_bytes = await self._storage.download(document.storage_key)
        async with self._uow:
            # _iter_accepted_changes_pages работает внутри этого контекста;
            # вложенных `async with self._uow` внутри него нет.
            changes = await self._iter_accepted_changes_pages(document)
        exporter = self._exporters.get_exporter(document.format)
        exported_bytes = exporter.apply_changes(raw_bytes, changes)
        media_type = _MEDIA_TYPES.get(document.format, "application/octet-stream")
        return exported_bytes, document.title, media_type

    async def export_and_save(
        self, document: "Document"
    ) -> None:
        """Применить правки, загрузить результат в MinIO и обновить storage_key.

        Структура:
          1. Скачать исходный файл из хранилища.
          2. Прочитать принятые правки (один UoW → одна сессия).
          3. Применить экспортер.
          4. Загрузить результат в хранилище.
          5. Записать export_key в БД (тот же UoW, новый commit).

        Шаги 2 и 5 используют отдельные `async with self._uow` —
        транзакции намеренно разделены: чтение правок и запись ключа
        не должны держать одну транзакцию открытой во время I/O с MinIO.
        """
        raw_bytes = await self._storage.download(document.storage_key)

        # Шаг 2 — читаем правки в отдельной (read-only по смыслу) транзакции.
        async with self._uow:
            changes = await self._iter_accepted_changes_pages(document)

        # Шаг 3 — применяем правки (CPU, без I/O к БД).
        exporter = self._exporters.get_exporter(document.format)
        exported_bytes = exporter.apply_changes(raw_bytes, changes)
        media_type = _MEDIA_TYPES.get(document.format, "application/octet-stream")

        # Шаг 4 — загружаем в хранилище.
        export_key = f"{document.storage_key}.exported"
        await self._storage.upload(export_key, exported_bytes, media_type)

        # Шаг 5 — обновляем storage_key (отдельный commit, не смешан с чтением).
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

        ВАЖНО: метод НЕ открывает `async with self._uow`.
        Вызывающий обязан вызвать этот метод уже внутри открытого
        `async with self._uow` блока, иначе сессия не будет доступна.

        Причина: вложенные UoW-контексты используют одну сессию;
        исключение в этом методе вызвало бы rollback() через __aexit__
        вложенного контекстного менеджера, уничтожая состояние внешней
        транзакции.

        Останавливается когда:
          - страница пуста (данные закончились), или
          - последняя страница (количество записей < _EXPORT_PAGE_SIZE).
        """
        if document.current_analysis_job_id is None:
            return []

        changes: list[AppliedChange] = []
        offset = 0

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
