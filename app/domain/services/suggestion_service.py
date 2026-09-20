"""
Бизнес-логика работы с правками: точечный accept/reject, bulk-accept,
finalize_review и сборка списка принятых изменений для экспорта.

ИСПРАВЛЕНО (rev-3):
1. decide() принимает project_id + document_id явно — убрана скрытая
   зависимость на повторный get_document в роутере (Баг #1 / #2).
2. Новые публичные методы accept_suggestion() / reject_suggestion() —
   один SELECT на документ, нет обращения к _documents из роутера (Баг #2).
3. finalize_review() принимает опциональный DocumentExportService и
   материализует финальный файл перед переходом в READY (Баг #3).
   При export_service=None поведение MVP-совместимо (ленивый экспорт).
4. list_suggestions_for_document принимает limit/offset → (items, total).
5. bulk_accept() фильтр по status перенесён на уровень SQL.
6. get_accepted_changes() — аналогично.
7. Чтение правок разрешено в любом статусе документа; write-операции
   ограничены AWAITING_APPROVAL на уровне сервиса.
8. _get_suggestion_for_document: дублированный lookup accept/reject вынесен
   в приватный helper — один SELECT на документ + один на правку.
"""
from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from app.domain.exceptions import (
    DocumentNotFoundError,
    InvalidDocumentStatusError,
    ReviewNotCompleteError,
    SuggestionAlreadyDecidedError,
    SuggestionNotFoundError,
)
from app.domain.interfaces.document_exporter import AppliedChange
from app.infrastructure.db.models.enums import DocumentStatus, SuggestionStatus
from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.suggestion import Suggestion
from app.infrastructure.db.repositories.document_repository import DocumentRepository
from app.infrastructure.db.repositories.suggestion_repository import SuggestionRepository

if TYPE_CHECKING:
    from app.domain.services.document_export_service import DocumentExportService

logger = logging.getLogger("syncscribe.services.suggestion")


class SuggestionService:
    def __init__(
        self,
        suggestion_repository: SuggestionRepository,
        document_repository: DocumentRepository,
    ) -> None:
        self._suggestions = suggestion_repository
        self._documents = document_repository

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_document_or_raise(
        self, project_id: uuid.UUID, document_id: uuid.UUID
    ) -> Document:
        document = await self._documents.get_by_id(document_id)
        if document is None or document.project_id != project_id:
            raise DocumentNotFoundError(
                f"Документ {document_id} не найден в проекте {project_id}"
            )
        return document

    async def _get_suggestion_for_document(
        self,
        document: Document,
        suggestion_id: uuid.UUID,
    ) -> Suggestion:
        """Загрузить правку и убедиться, что она принадлежит текущему job документа.

        Выносит дублированный lookup из accept_suggestion / reject_suggestion.
        """
        suggestion = await self._suggestions.get_by_id(suggestion_id)
        if (
            suggestion is None
            or suggestion.analysis_job_id != document.current_analysis_job_id
        ):
            raise SuggestionNotFoundError(
                f"Правка {suggestion_id} не найдена для документа {document.id}"
            )
        return suggestion

    # ------------------------------------------------------------------
    # Read operations (статус документа не проверяется — доступны везде)
    # ------------------------------------------------------------------

    async def list_suggestions_for_document(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        limit: int,
        offset: int,
    ) -> tuple[list[Suggestion], int]:
        document = await self._get_document_or_raise(project_id, document_id)
        if document.current_analysis_job_id is None:
            return [], 0
        items = await self._suggestions.list_by_analysis_job(
            document.current_analysis_job_id, limit=limit, offset=offset
        )
        total = await self._suggestions.count_by_analysis_job(
            document.current_analysis_job_id
        )
        return items, total

    async def get_suggestion_for_document(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        suggestion_id: uuid.UUID,
    ) -> Suggestion:
        """Вернуть правку по id (только чтение, без проверки статуса документа)."""
        document = await self._get_document_or_raise(project_id, document_id)
        return await self._get_suggestion_for_document(document, suggestion_id)

    async def get_accepted_changes(self, document_id: uuid.UUID) -> list[AppliedChange]:
        document = await self._documents.get_by_id(document_id)
        if document is None or document.current_analysis_job_id is None:
            return []
        suggestions = await self._suggestions.list_by_analysis_job_and_status(
            document.current_analysis_job_id, SuggestionStatus.ACCEPTED
        )
        return [
            AppliedChange(
                section_ref=s.section_ref,
                change_type=s.change_type.value,
                old_text=s.old_text,
                new_text=s.new_text,
            )
            for s in suggestions
        ]

    # ------------------------------------------------------------------
    # Write operations (требуют AWAITING_APPROVAL)
    # ------------------------------------------------------------------

    async def _decide(
        self,
        document: Document,
        suggestion: Suggestion,
        user_id: uuid.UUID,
        new_status: SuggestionStatus,
    ) -> Suggestion:
        """Применить решение к уже загруженной правке."""
        if document.status != DocumentStatus.AWAITING_APPROVAL:
            raise InvalidDocumentStatusError(
                "Решения по правкам доступны только в статусе 'awaiting_approval'"
            )
        updated = await self._suggestions.update_status(suggestion, new_status, user_id)
        if updated is None:
            raise SuggestionAlreadyDecidedError(
                f"Правка {suggestion.id} уже была обработана другим запросом"
            )
        return updated

    async def accept_suggestion(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        suggestion_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> Suggestion:
        """Принять правку. Один SELECT на документ, один — на правку."""
        document = await self._get_document_or_raise(project_id, document_id)
        suggestion = await self._get_suggestion_for_document(document, suggestion_id)
        return await self._decide(document, suggestion, user_id, SuggestionStatus.ACCEPTED)

    async def reject_suggestion(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        suggestion_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> Suggestion:
        """Отклонить правку. Один SELECT на документ, один — на правку."""
        document = await self._get_document_or_raise(project_id, document_id)
        suggestion = await self._get_suggestion_for_document(document, suggestion_id)
        return await self._decide(document, suggestion, user_id, SuggestionStatus.REJECTED)

    async def bulk_accept(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> list[Suggestion]:
        document = await self._get_document_or_raise(project_id, document_id)
        if document.status != DocumentStatus.AWAITING_APPROVAL:
            raise InvalidDocumentStatusError(
                "Решения по правкам доступны только в статусе 'awaiting_approval'"
            )
        if document.current_analysis_job_id is None:
            return []
        pending_ids = await self._suggestions.list_ids_by_analysis_job_and_status(
            document.current_analysis_job_id, SuggestionStatus.PENDING
        )
        return await self._suggestions.bulk_update_status(
            pending_ids, SuggestionStatus.ACCEPTED, user_id
        )

    async def finalize_review(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        export_service: "DocumentExportService | None" = None,
    ) -> Document:
        """Перевести документ в READY (переход №8).

        Условие: статус AWAITING_APPROVAL И ни одной правки в PENDING.
        Если все правки отклонены — документ всё равно переходит в READY.

        Параметр export_service (опциональный):
        - Если передан — материализует финальный файл с применёнными правками
          в MinIO перед сменой статуса.
        - Если None — статус меняется без применения правок (ленивый экспорт).
        """
        document = await self._get_document_or_raise(project_id, document_id)
        if document.status != DocumentStatus.AWAITING_APPROVAL:
            raise InvalidDocumentStatusError(
                "Завершить review можно только в статусе 'awaiting_approval'"
            )
        if document.current_analysis_job_id is None:
            raise ReviewNotCompleteError(
                "У документа отсутствует текущий результат анализа"
            )
        pending_count = await self._suggestions.count_by_analysis_job_and_status(
            document.current_analysis_job_id, SuggestionStatus.PENDING
        )
        if pending_count:
            raise ReviewNotCompleteError(
                f"Нельзя завершить review: не рассмотрено предложений — {pending_count}"
            )

        if export_service is not None:
            try:
                await export_service.export_and_save(document)
            except Exception as err:
                logger.exception(
                    "Не удалось материализовать финальный файл при finalize_review",
                    extra={"document_id": str(document_id)},
                )
                raise ReviewNotCompleteError(
                    "Не удалось применить утверждённые правки к документу. "
                    "Повторите попытку или обратитесь к администратору."
                ) from err

        return await self._documents.update_status(document, DocumentStatus.READY)
