"""
Бизнес-логика работы с правками: точечный accept/reject, bulk-accept,
finalize_review, finalize_review_versioned и сборка принятых изменений.

ИСПРАВЛЕНО (rev-2):
1. decide() принимает project_id + document_id явно.
2. Новые публичные методы accept_suggestion() / reject_suggestion().
3. finalize_review() принимает опциональный DocumentExportService.
4. list_suggestions_for_document принимает limit/offset → (items, total).
5. bulk_accept() фильтр по status перенесён на уровень SQL.
6. get_accepted_changes() — аналогично.

P0-2 (rev-3): добавлен finalize_review_versioned() — атомарная проверка
review_version + инкремент через оптимистическую блокировку.

fix/review-critical-p0 (rev-4):
7. Добавлен apply_review() — атомарное применение accepted/rejected списков
   с проверкой review_version. Вызывается из editor.py (PUT /editor/review).
"""
from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING

from app.domain.exceptions import (
    DocumentNotFoundError,
    InvalidDocumentStatusError,
    ReviewNotCompleteError,
    ReviewVersionConflictError,
    StaleReviewVersionError,
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

    async def _get_suggestion_for_document_or_raise(
        self,
        document: Document,
        suggestion_id: uuid.UUID,
    ) -> Suggestion:
        """Загрузить правку и проверить принадлежность текущему job документа."""
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
    # Read operations
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
        document = await self._get_document_or_raise(project_id, document_id)
        return await self._get_suggestion_for_document_or_raise(document, suggestion_id)

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
        document = await self._get_document_or_raise(project_id, document_id)
        suggestion = await self._get_suggestion_for_document_or_raise(document, suggestion_id)
        return await self._decide(document, suggestion, user_id, SuggestionStatus.ACCEPTED)

    async def reject_suggestion(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        suggestion_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> Suggestion:
        document = await self._get_document_or_raise(project_id, document_id)
        suggestion = await self._get_suggestion_for_document_or_raise(document, suggestion_id)
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

    # ------------------------------------------------------------------
    # P0-2 (rev-4): apply_review — атомарное применение accepted/rejected
    # ------------------------------------------------------------------

    async def apply_review(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        accepted_ids: list[uuid.UUID],
        rejected_ids: list[uuid.UUID],
        current_review_version: int,
    ) -> int:
        """Атомарно применить списки принятых и отклонённых правок.

        Алгоритм:
        1. Загрузить документ и проверить review_version == current_review_version.
           При несовпадении → ReviewVersionConflictError → роутер вернёт 409.
        2. Применить bulk UPDATE для accepted_ids и rejected_ids.
           Правки уже в ACCEPTED/REJECTED пропускаются атомарно (WHERE status = PENDING).
        3. Инкрементировать review_version через finalize_and_bump_version или
           отдельный update_review_version (только версия, без смены статуса).

        Возвращает новую review_version.

        Примечание: этот метод не требует, чтобы document.status == AWAITING_APPROVAL,
        так как PUT /editor/review — «частичное» сохранение, а не финализация.
        """
        document = await self._get_document_or_raise(project_id, document_id)

        if document.review_version != current_review_version:
            raise ReviewVersionConflictError(
                f"Конфликт версий review: ожидалась {current_review_version}, "
                f"текущая версия {document.review_version}. "
                "Обновите страницу и повторите попытку."
            )

        # Применяем bulk-обновления (WHERE status = PENDING — защита от гонки)
        if accepted_ids:
            # user_id не передаётся в apply_review; правки без автора — допустимо
            # для bulk-операции из PUT /editor/review (нет явного user_id в теле).
            # TODO: передавать user_id из depends когда будет auth на этом эндпоинте.
            await self._suggestions.bulk_update_status(
                accepted_ids, SuggestionStatus.ACCEPTED, uuid.UUID(int=0)
            )
        if rejected_ids:
            await self._suggestions.bulk_update_status(
                rejected_ids, SuggestionStatus.REJECTED, uuid.UUID(int=0)
            )

        # Инкрементируем review_version атомарно
        updated_document = await self._documents.bump_review_version(document)
        return updated_document.review_version

    # ------------------------------------------------------------------
    # Finalize
    # ------------------------------------------------------------------

    async def finalize_review(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        export_service: "DocumentExportService | None" = None,
    ) -> Document:
        """Перевести документ в READY. Без проверки версии (legacy POST /finalize)."""
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
                    "Не удалось применить утверждённые правки к документу."
                ) from err
        return await self._documents.update_status(document, DocumentStatus.READY)

    # P0-2: версионированная финализация
    async def finalize_review_versioned(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        client_version: int,
        export_service: "DocumentExportService | None" = None,
    ) -> Document:
        """Финализировать review с проверкой оптимистической блокировки.

        Алгоритм:
        1. Загрузить документ и проверить ``review_version == client_version``.
           При несовпадении → StaleReviewVersionError → 412.
        2. Выполнить все проверки (статус, pending_count).
        3. Инкрементировать review_version атомарно через репозиторий.
        4. Сменить статус документа на READY.

        Идемпотентность: если документ уже READY и версия совпадает,
        возвращаем документ без ошибки.
        """
        document = await self._get_document_or_raise(project_id, document_id)

        # Идемпотентный повтор с совпадающей версией — документ уже READY
        if document.status == DocumentStatus.READY and document.review_version == client_version + 1:
            return document

        # P0-2: проверяем совпадение версии
        if document.review_version != client_version:
            raise StaleReviewVersionError(
                f"Конфликт версий review: ожидалась {document.review_version}, "
                f"клиент прислал {client_version}. "
                "Обновите страницу и повторите попытку."
            )

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
                    "Не удалось материализовать финальный файл при finalize_review_versioned",
                    extra={"document_id": str(document_id)},
                )
                raise ReviewNotCompleteError(
                    "Не удалось применить утверждённые правки к документу."
                ) from err
        # Атомарный инкремент версии + смена статуса
        return await self._documents.finalize_and_bump_version(document)
