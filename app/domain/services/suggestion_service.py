"""
Бизнес-логика работы с правками: точечный accept/reject, bulk-accept,
finalize_review, атомарное сохранение сессии ревью (P0-2) и сборка
списка принятых изменений для экспорта.

ИСПРАВЛЕНО:
- PERF-2: apply_review принимает user_id; больше нет uuid(int=0) в audit_log.
- CODE-3: дублированный export-блок вынесен в _run_export().
- P0-#13: bulk_accept() возвращает BulkAcceptResult(правки, документ).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.domain.exceptions import (
    DocumentNotFoundError,
    InvalidDocumentStatusError,
    OptimisticLockError,
    ReviewNotCompleteError,
    SuggestionAlreadyDecidedError,
    SuggestionNotFoundError,
)
from app.domain.interfaces.document_exporter import AppliedChange
from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.enums import DocumentStatus, SuggestionStatus
from app.infrastructure.db.models.suggestion import Suggestion
from app.infrastructure.db.repositories.document_repository import DocumentRepository
from app.infrastructure.db.repositories.suggestion_repository import SuggestionRepository

if TYPE_CHECKING:
    from app.domain.services.document_export_service import DocumentExportService

logger = logging.getLogger("syncscribe.services.suggestion")


@dataclass
class ReviewSaveResult:
    """Result of atomic review save."""

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

    async def _get_document_or_raise(self, project_id: uuid.UUID, document_id: uuid.UUID) -> Document:
        document = await self._documents.get_by_id(document_id)
        if document is None or document.project_id != project_id:
            raise DocumentNotFoundError(f"Документ {document_id} не найден в проекте {project_id}")
        return document

    async def _get_suggestion_for_document(
        self,
        document: Document,
        suggestion_id: uuid.UUID,
    ) -> Suggestion:
        suggestion = await self._suggestions.get_by_id(suggestion_id)
        if suggestion is None or suggestion.analysis_job_id != document.current_analysis_job_id:
            raise SuggestionNotFoundError(f"Правка {suggestion_id} не найдена для документа {document.id}")
        return suggestion

    async def _run_export(
        self,
        document: Document,
        export_service: DocumentExportService,
    ) -> None:
        try:
            await export_service.export_and_save(document)
        except Exception as err:
            logger.exception(
                "Не удалось материализовать финальный файл",
                extra={"document_id": str(document.id)},
            )
            raise ReviewNotCompleteError("Не удалось применить утверждённые правки к документу.") from err

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
        total = await self._suggestions.count_by_analysis_job(document.current_analysis_job_id)
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
            raise SuggestionAlreadyDecidedError(f"Правка {suggestion.id} уже была обработана другим запросом")
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
            document.current_analysis_job_id, pending_ids, SuggestionStatus.ACCEPTED, user_id
        )
        refreshed = await self._documents.get_by_id(document_id)
        return BulkAcceptResult(suggestions=accepted, document=refreshed or document)

    async def apply_review(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        user_id: uuid.UUID,
        accepted_ids: list[uuid.UUID],
        rejected_ids: list[uuid.UUID],
        current_review_version: int,
    ) -> int:
        decisions = [
            *[(item_id, SuggestionStatus.ACCEPTED) for item_id in accepted_ids],
            *[(item_id, SuggestionStatus.REJECTED) for item_id in rejected_ids],
        ]
        result = await self.atomic_review_save(
            project_id, document_id, user_id, current_review_version, decisions, False
        )
        return result.document.review_version

    async def finalize_review(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        export_service: DocumentExportService | None = None,
    ) -> Document:
        document = await self._get_document_or_raise(project_id, document_id)
        if document.status != DocumentStatus.AWAITING_APPROVAL:
            raise InvalidDocumentStatusError("Завершить review можно только в статусе 'awaiting_approval'")
        if document.current_analysis_job_id is None:
            raise ReviewNotCompleteError("У документа отсутствует текущий результат анализа")
        pending_count = await self._suggestions.count_by_analysis_job_and_status(
            document.current_analysis_job_id, SuggestionStatus.PENDING
        )
        if pending_count:
            raise ReviewNotCompleteError(
                f"Нельзя завершить review: не рассмотрено предложений — {pending_count}"
            )
        if export_service is not None:
            await self._run_export(document, export_service)
        return await self._documents.update_status(document, DocumentStatus.READY)

    async def atomic_review_save(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        user_id: uuid.UUID,
        review_version: int,
        decisions: list[tuple[uuid.UUID, SuggestionStatus]],
        finalize: bool = True,
        export_service: DocumentExportService | None = None,
    ) -> ReviewSaveResult:
        """Save decisions under a single optimistic-lock precondition."""
        document = await self._get_document_or_raise(project_id, document_id)
        if document.status != DocumentStatus.AWAITING_APPROVAL:
            raise InvalidDocumentStatusError("Ревью доступно только в статусе 'awaiting_approval'")
        job_id = document.current_analysis_job_id
        if job_id is None:
            raise ReviewNotCompleteError("У документа отсутствует текущий результат анализа")

        normalized: dict[uuid.UUID, SuggestionStatus] = {}
        for suggestion_id, decision in decisions:
            previous = normalized.get(suggestion_id)
            if previous is not None and previous != decision:
                raise ReviewNotCompleteError(f"Для правки {suggestion_id} переданы противоречивые решения")
            normalized[suggestion_id] = decision

        document = await self._documents.compare_and_increment_review_version(document.id, review_version)
        if document is None:
            raise OptimisticLockError("Документ изменён параллельным запросом. Обновите данные и повторите.")

        accepted_ids = [sid for sid, decision in normalized.items() if decision == SuggestionStatus.ACCEPTED]
        rejected_ids = [sid for sid, decision in normalized.items() if decision == SuggestionStatus.REJECTED]
        accepted = await self._suggestions.bulk_update_status(
            job_id, accepted_ids, SuggestionStatus.ACCEPTED, user_id
        )
        rejected = await self._suggestions.bulk_update_status(
            job_id, rejected_ids, SuggestionStatus.REJECTED, user_id
        )
        if len(accepted) != len(accepted_ids) or len(rejected) != len(rejected_ids):
            raise SuggestionAlreadyDecidedError(
                "Часть правок не найдена в текущем анализе или уже обработана"
            )

        pending_count = await self._suggestions.count_by_analysis_job_and_status(
            job_id, SuggestionStatus.PENDING
        )
        finalized = False
        if finalize:
            if pending_count:
                raise ReviewNotCompleteError(f"Нельзя завершить ревью: осталось правок — {pending_count}")
            if export_service is not None:
                await self._run_export(document, export_service)
            document = await self._documents.update_status(document, DocumentStatus.READY)
            finalized = True

        return ReviewSaveResult(
            document=document,
            accepted_count=len(accepted),
            rejected_count=len(rejected),
            pending_count=pending_count,
            finalized=finalized,
        )
