"""
Бизнес-логика работы с правками: точечный accept/reject, bulk-accept,
finalize_review, атомарное сохранение сессии ревью (P0-2) и сборка
списка принятых изменений для экспорта.

ИСПРАВЛЕНО (P0-#13):
- bulk_accept() теперь возвращает BulkAcceptResult(правки, документ).

ИСПРАВЛЕНО (review #8):
- atomic_review_save: pending_count вычисляется без лишнего COUNT-запроса —
  через total suggestions минус принятые/отклонённые, полученные из
  уже выполненных bulk_update_status.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.domain.exceptions import (
    DocumentNotFoundError,
    InvalidDocumentStatusError,
    OptimisticLockError,
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


@dataclass
class ReviewSaveResult:
    """Результат атомарного сохранения ревью."""

    document: Document
    accepted_count: int = 0
    rejected_count: int = 0
    pending_count: int = 0
    finalized: bool = False


@dataclass
class BulkAcceptResult:
    """P0-#13: результат bulk-accept — список правок + актуальный документ."""

    suggestions: list[Suggestion]
    document: Document


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
        suggestion = await self._get_suggestion_for_document(document, suggestion_id)
        return await self._decide(document, suggestion, user_id, SuggestionStatus.ACCEPTED)

    async def reject_suggestion(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        suggestion_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> Suggestion:
        document = await self._get_document_or_raise(project_id, document_id)
        suggestion = await self._get_suggestion_for_document(document, suggestion_id)
        return await self._decide(document, suggestion, user_id, SuggestionStatus.REJECTED)

    async def bulk_accept(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> BulkAcceptResult:
        """Бульковое принятие всех pending-правок.

        P0-#13: возвращает BulkAcceptResult(правки, документ), чтобы
        роутер мог вернуть document_status и review_version в одном ответе,
        без дополнительного GET /editor.
        """
        document = await self._get_document_or_raise(project_id, document_id)
        if document.status != DocumentStatus.AWAITING_APPROVAL:
            raise InvalidDocumentStatusError(
                "Решения по правкам доступны только в статусе 'awaiting_approval'"
            )
        if document.current_analysis_job_id is None:
            return BulkAcceptResult(suggestions=[], document=document)
        pending_ids = await self._suggestions.list_ids_by_analysis_job_and_status(
            document.current_analysis_job_id, SuggestionStatus.PENDING
        )
        accepted = await self._suggestions.bulk_update_status(
            pending_ids, SuggestionStatus.ACCEPTED, user_id
        )
        refreshed = await self._documents.get_by_id(document_id)
        return BulkAcceptResult(
            suggestions=accepted,
            document=refreshed or document,
        )

    # ------------------------------------------------------------------
    # P0-2 (rev-4): apply_review
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
        2. Bulk UPDATE для accepted_ids и rejected_ids
           (WHERE status = PENDING — защита от гонки).
        3. Инкрементировать review_version атомарно.

        Возвращает новую review_version.
        """
        document = await self._get_document_or_raise(project_id, document_id)

        if document.review_version != current_review_version:
            raise ReviewVersionConflictError(
                f"Конфликт версий review: ожидалась {current_review_version}, "
                f"текущая версия {document.review_version}. "
                "Обновите страницу и повторите попытку."
            )

        if accepted_ids:
            await self._suggestions.bulk_update_status(
                accepted_ids, SuggestionStatus.ACCEPTED, uuid.UUID(int=0)
            )
        if rejected_ids:
            await self._suggestions.bulk_update_status(
                rejected_ids, SuggestionStatus.REJECTED, uuid.UUID(int=0)
            )

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
        """Перевести документ в READY (переход №8).

        Условие: статус AWAITING_APPROVAL И ни одной правки в PENDING.
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
                    "Не удалось применить утверждённые правки к документу."
                ) from err
        return await self._documents.update_status(document, DocumentStatus.READY)

    # ------------------------------------------------------------------
    # P0-2: атомарное сохранение сессии ревью
    # ------------------------------------------------------------------

    async def atomic_review_save(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        user_id: uuid.UUID,
        review_version: int,
        decisions: list[tuple[uuid.UUID, SuggestionStatus]],
        finalize: bool = True,
        export_service: "DocumentExportService | None" = None,
    ) -> ReviewSaveResult:
        """Атомарное сохранение всех решений ревью за один вызов."""
        document = await self._get_document_or_raise(project_id, document_id)

        if document.status != DocumentStatus.AWAITING_APPROVAL:
            raise InvalidDocumentStatusError(
                "Атомарное сохранение ревью доступно только в статусе 'awaiting_approval'"
            )

        current_version = getattr(document, "review_version", 0) or 0
        if current_version != review_version:
            raise OptimisticLockError(
                f"Версия ревью устарела: ожидалось {current_version}, получено {review_version}. "
                "Перезагрузите документ и повторите сохранение."
            )

        if document.current_analysis_job_id is None:
            raise ReviewNotCompleteError(
                "У документа отсутствует текущий результат анализа"
            )

        accepted_ids = [sid for sid, st in decisions if st == SuggestionStatus.ACCEPTED]
        rejected_ids = [sid for sid, st in decisions if st == SuggestionStatus.REJECTED]

        accepted_suggestions: list[Suggestion] = []
        rejected_suggestions: list[Suggestion] = []

        if accepted_ids:
            accepted_suggestions = await self._suggestions.bulk_update_status(
                accepted_ids, SuggestionStatus.ACCEPTED, user_id
            )
        if rejected_ids:
            rejected_suggestions = await self._suggestions.bulk_update_status(
                rejected_ids, SuggestionStatus.REJECTED, user_id
            )

        # review #8: pending_count без лишнего COUNT-запроса.
        # bulk_update_status пропускает уже решённые правки (WHERE status=PENDING),
        # поэтому total - newly_decided даёт точный остаток.
        total_decisions = len(decisions)
        newly_decided = len(accepted_suggestions) + len(rejected_suggestions)
        pending_count = max(0, total_decisions - newly_decided)

        finalized = False
        if finalize and pending_count == 0:
            if export_service is not None:
                try:
                    await export_service.export_and_save(document)
                except Exception as err:
                    logger.exception(
                        "Не удалось материализовать финальный файл при atomic_review_save",
                        extra={"document_id": str(document_id)},
                    )
                    raise ReviewNotCompleteError(
                        "Не удалось применить утверждённые правки к документу."
                    ) from err
            document = await self._documents.update_status(document, DocumentStatus.READY)
            finalized = True

        document = await self._documents.increment_review_version(document)

        return ReviewSaveResult(
            document=document,
            accepted_count=len(accepted_suggestions),
            rejected_count=len(rejected_suggestions),
            pending_count=pending_count if not finalized else 0,
            finalized=finalized,
        )
