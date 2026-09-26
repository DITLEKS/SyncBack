"""
Бизнес-логика работы с правками: точечный accept/reject, bulk-accept,
finalize_review, атомарное сохранение сессии ревью и сборка
списка принятых изменений для экспорта.

H2.2: сервис принимает SuggestionPort / DocumentPort вместо конкретных репозиториев.
      Все enum-импорты перенесены в app.domain.enums.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.domain.enums import DocumentStatus, SuggestionStatus
from app.domain.exceptions import (
    DocumentNotFoundError,
    InvalidDocumentStatusError,
    OptimisticLockError,
    ReviewNotCompleteError,
    ReviewVersionConflictError,
    SuggestionAlreadyDecidedError,
    SuggestionNotFoundError,
)
from app.domain.interfaces.document_exporter import AppliedChange
from app.domain.ports.document_port import DocumentPort
from app.domain.ports.suggestion_port import SuggestionPort

if TYPE_CHECKING:
    from app.domain.services.document_export_service import DocumentExportService
    from app.infrastructure.db.models.document import Document
    from app.infrastructure.db.models.suggestion import Suggestion

logger = logging.getLogger("syncscribe.services.suggestion")


@dataclass
class ReviewSaveResult:
    """Result of atomic review save."""

    document: "Document"
    accepted_count: int = 0
    rejected_count: int = 0
    pending_count: int = 0
    finalized: bool = False


@dataclass
class BulkAcceptResult:
    """Результат bulk-accept — список правок + актуальный документ."""

    suggestions: list["Suggestion"] = field(default_factory=list)
    document: "Document | None" = None


class SuggestionService:
    def __init__(
        self,
        suggestion_repository: SuggestionPort,
        document_repository: DocumentPort,
    ) -> None:
        self._suggestions = suggestion_repository
        self._documents = document_repository

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_document_or_raise(
        self, project_id: uuid.UUID, document_id: uuid.UUID
    ) -> "Document":
        document = await self._documents.get_by_id(document_id)
        if document is None or document.project_id != project_id:
            raise DocumentNotFoundError(
                f"Документ {document_id} не найден в проекте {project_id}"
            )
        return document

    async def _get_suggestion_for_document(
        self,
        document: "Document",
        suggestion_id: uuid.UUID,
    ) -> "Suggestion":
        suggestion = await self._suggestions.get_by_id(suggestion_id)
        if (
            suggestion is None
            or suggestion.analysis_job_id != document.current_analysis_job_id
        ):
            raise SuggestionNotFoundError(
                f"Правка {suggestion_id} не найдена для документа {document.id}"
            )
        return suggestion

    async def _run_export(
        self,
        document: "Document",
        export_service: "DocumentExportService",
    ) -> None:
        """Единый экспорт-блок. Бросает ReviewNotCompleteError при ошибке."""
        try:
            await export_service.export_and_save(document)
        except Exception as err:
            logger.exception(
                "Не удалось материализовать финальный файл",
                extra={"document_id": str(document.id)},
            )
            raise ReviewNotCompleteError(
                "Не удалось применить утверждённые правки к документу."
            ) from err

    async def _apply_decisions(
        self,
        analysis_job_id: uuid.UUID,
        accepted_ids: list[uuid.UUID],
        rejected_ids: list[uuid.UUID],
        user_id: uuid.UUID,
    ) -> None:
        """Применяет списки принятых и отклонённых правок.

        analysis_job_id обязателен — передаётся в bulk_update_status, чтобы
        репозиторий мог отфильтровать правки из чужих документов.
        """
        if accepted_ids:
            await self._suggestions.bulk_update_status(
                accepted_ids, analysis_job_id, SuggestionStatus.ACCEPTED, user_id
            )
        if rejected_ids:
            await self._suggestions.bulk_update_status(
                rejected_ids, analysis_job_id, SuggestionStatus.REJECTED, user_id
            )

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    async def list_suggestions_for_document(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        limit: int,
        offset: int,
    ) -> "tuple[list[Suggestion], int]":
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
    ) -> "Suggestion":
        document = await self._get_document_or_raise(project_id, document_id)
        return await self._get_suggestion_for_document(document, suggestion_id)

    async def get_accepted_changes(
        self, document_id: uuid.UUID
    ) -> list[AppliedChange]:
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
        document: "Document",
        suggestion: "Suggestion",
        user_id: uuid.UUID,
        new_status: SuggestionStatus,
    ) -> "Suggestion":
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
    ) -> "Suggestion":
        document = await self._get_document_or_raise(project_id, document_id)
        suggestion = await self._get_suggestion_for_document(document, suggestion_id)
        return await self._decide(document, suggestion, user_id, SuggestionStatus.ACCEPTED)

    async def reject_suggestion(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        suggestion_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> "Suggestion":
        document = await self._get_document_or_raise(project_id, document_id)
        suggestion = await self._get_suggestion_for_document(document, suggestion_id)
        return await self._decide(document, suggestion, user_id, SuggestionStatus.REJECTED)

    async def bulk_accept(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> BulkAcceptResult:
        """Принимает все pending-правки, возвращает BulkAcceptResult(правки, документ).

        Один UPDATE WHERE analysis_job_id=... AND status='pending' RETURNING *
        вместо двух запросов (SELECT ids → UPDATE WHERE id IN).
        """
        document = await self._get_document_or_raise(project_id, document_id)
        if document.status != DocumentStatus.AWAITING_APPROVAL:
            raise InvalidDocumentStatusError(
                "Решения по правкам доступны только в статусе 'awaiting_approval'"
            )
        if document.current_analysis_job_id is None:
            return BulkAcceptResult(suggestions=[], document=document)
        accepted = await self._suggestions.bulk_update_all_pending(
            document.current_analysis_job_id, SuggestionStatus.ACCEPTED, user_id
        )
        refreshed = await self._documents.get_by_id(document_id)
        return BulkAcceptResult(
            suggestions=accepted,
            document=refreshed or document,
        )

    # ------------------------------------------------------------------
    # apply_review: применить решения и пробить версию
    # ------------------------------------------------------------------

    async def apply_review(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        user_id: uuid.UUID,
        accepted_ids: list[uuid.UUID],
        rejected_ids: list[uuid.UUID],
        current_review_version: int,
    ) -> int:
        """Применяет решения по правкам и атомарно увеличивает review_version.
        Возвращает новую версию. user_id записывается в audit_log.
        """
        document = await self._get_document_or_raise(project_id, document_id)

        if document.review_version != current_review_version:
            raise ReviewVersionConflictError(
                f"Конфликт версий review: ожидалась {current_review_version}, "
                f"текущая версия {document.review_version}. "
                "Обновите страницу и повторите попытку."
            )

        if document.current_analysis_job_id is None:
            raise ReviewNotCompleteError(
                "У документа отсутствует текущий результат анализа"
            )

        await self._apply_decisions(
            document.current_analysis_job_id, accepted_ids, rejected_ids, user_id
        )
        updated_document = await self._documents.increment_review_version(document)
        return updated_document.review_version

    # ------------------------------------------------------------------
    # atomic_review_save: стандартный путь (без If-Match)
    # ------------------------------------------------------------------

    async def atomic_review_save(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        user_id: uuid.UUID,
        review_version: int | None,
        decisions: list[tuple[uuid.UUID, SuggestionStatus]],
        finalize: bool,
    ) -> ReviewSaveResult:
        """Стандартный путь сохранения ревью (без заголовка If-Match).

        1. Проверяет review_version если передана (OptimisticLockError при конфликте).
        2. Применяет все решения из ``decisions``.
        3. Если ``finalize=True`` — переводит документ в статус REVIEWED.
        Возвращает ReviewSaveResult с актуальными счётчиками (1 COUNT-запрос).
        """
        document = await self._get_document_or_raise(project_id, document_id)

        if document.status != DocumentStatus.AWAITING_APPROVAL:
            raise InvalidDocumentStatusError(
                "Сохранение ревью доступно только в статусе 'awaiting_approval'"
            )

        if review_version is not None and document.review_version != review_version:
            raise OptimisticLockError(
                f"review_version устарела: клиент={review_version}, "
                f"сервер={document.review_version}. Обновите страницу."
            )

        if document.current_analysis_job_id is None:
            raise ReviewNotCompleteError(
                "У документа отсутствует текущий результат анализа"
            )

        job_id = document.current_analysis_job_id

        accepted_ids = [
            sid for sid, st in decisions if st == SuggestionStatus.ACCEPTED
        ]
        rejected_ids = [
            sid for sid, st in decisions if st == SuggestionStatus.REJECTED
        ]
        await self._apply_decisions(job_id, accepted_ids, rejected_ids, user_id)

        if finalize:
            pending_count = await self._suggestions.count_by_analysis_job_and_status(
                job_id, SuggestionStatus.PENDING
            )
            if pending_count:
                raise ReviewNotCompleteError(
                    f"Нельзя завершить review: не рассмотрено предложений — {pending_count}"
                )
            document = await self._documents.update_status(
                document, DocumentStatus.REVIEWED
            )

        # Один COUNT-запрос вместо трёх.
        stats = await self._suggestions.count_by_analysis_job_stats(job_id)

        return ReviewSaveResult(
            document=document,
            accepted_count=stats.accepted,
            rejected_count=stats.rejected,
            pending_count=stats.pending,
            finalized=finalize,
        )

    # ------------------------------------------------------------------
    # finalize_review_versioned: versioned путь (с If-Match)
    # ------------------------------------------------------------------

    async def finalize_review_versioned(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        user_id: uuid.UUID,
        accepted_ids: list[uuid.UUID],
        rejected_ids: list[uuid.UUID],
        current_review_version: int,
    ) -> "Document":
        """Versioned финализация ревью с оптимистической блокировкой.

        1. Проверяет review_version (412 при конфликте — StaleReviewVersionError).
        2. Применяет решения пользователя.
        3. Проверяет отсутствие pending-правок.
        4. Переводит документ в REVIEWED.
        """
        from app.domain.exceptions import StaleReviewVersionError  # алиас

        document = await self._get_document_or_raise(project_id, document_id)

        if document.status != DocumentStatus.AWAITING_APPROVAL:
            raise InvalidDocumentStatusError(
                "Завершить review можно только в статусе 'awaiting_approval'"
            )

        if document.review_version != current_review_version:
            raise StaleReviewVersionError(
                f"Конфликт версий: ожидалась {current_review_version}, "
                f"текущая {document.review_version}. Обновите страницу."
            )

        if document.current_analysis_job_id is None:
            raise ReviewNotCompleteError(
                "У документа отсутствует текущий результат анализа"
            )

        await self._apply_decisions(
            document.current_analysis_job_id, accepted_ids, rejected_ids, user_id
        )

        pending_count = await self._suggestions.count_by_analysis_job_and_status(
            document.current_analysis_job_id, SuggestionStatus.PENDING
        )
        if pending_count:
            raise ReviewNotCompleteError(
                f"Нельзя завершить review: не рассмотрено предложений — {pending_count}"
            )

        return await self._documents.update_status(document, DocumentStatus.REVIEWED)

    # ------------------------------------------------------------------
    # finalize_review (устаревший путь, используется Celery worker)
    # ------------------------------------------------------------------

    async def finalize_review(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        export_service: "DocumentExportService | None" = None,
    ) -> "Document":
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
            await self._run_export(document, export_service)
        return await self._documents.update_status(document, DocumentStatus.REVIEWED)
