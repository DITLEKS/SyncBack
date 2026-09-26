"""
Бизнес-логика работы с правками (suggestions).

Архитектурные правила:
  - Сервис зависит только от IUnitOfWork (порт) — не от конкретных репозиториев.
  - Единственный вызов uow.commit() на операцию — атомарность гарантируется UoW.
  - Импорт из infrastructure.* запрещён (кроме TYPE_CHECKING для экспортёра).

ИСПРАВЛЕНО:
  - PERF-2: apply_review принимает user_id — нет uuid(int=0) в audit_log.
  - CODE-3: дублированный export-блок вынесен в _run_export().
  - atomic_review_save теперь делает один uow.commit() на весь use-case.
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
from app.domain.interfaces.unit_of_work import IUnitOfWork
from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.enums import DocumentStatus, SuggestionStatus
from app.infrastructure.db.models.suggestion import Suggestion

if TYPE_CHECKING:
    from app.domain.services.document_export_service import DocumentExportService

logger = logging.getLogger("syncscribe.services.suggestion")


@dataclass
class ReviewSaveResult:
    """Результат атомарного сохранения сессии ревью."""
    document: Document
    accepted_count: int = 0
    rejected_count: int = 0
    pending_count: int = 0
    finalized: bool = False


@dataclass
class BulkAcceptResult:
    """Результат bulk-accept — список правок + актуальный документ."""
    suggestions: list[Suggestion]
    document: Document


class SuggestionService:
    def __init__(self, uow: IUnitOfWork) -> None:
        self._uow = uow

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_document_or_raise(
        self, project_id: uuid.UUID, document_id: uuid.UUID
    ) -> Document:
        document = await self._uow.documents.get_by_id(document_id)
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
        suggestion = await self._uow.suggestions.get_by_id(suggestion_id)
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
            raise ReviewNotCompleteError(
                "Не удалось применить утверждённые правки к документу."
            ) from err

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def list_suggestions_for_document(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        limit: int,
        offset: int,
    ) -> tuple[list[Suggestion], int]:
        async with self._uow:
            document = await self._get_document_or_raise(project_id, document_id)
            if document.current_analysis_job_id is None:
                return [], 0
            items = await self._uow.suggestions.list_by_analysis_job(
                document.current_analysis_job_id, limit=limit, offset=offset
            )
            total = await self._uow.suggestions.count_by_analysis_job(
                document.current_analysis_job_id
            )
        return items, total

    async def get_suggestion_for_document(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        suggestion_id: uuid.UUID,
    ) -> Suggestion:
        async with self._uow:
            document = await self._get_document_or_raise(project_id, document_id)
            return await self._get_suggestion_for_document(document, suggestion_id)

    async def get_accepted_changes(self, document_id: uuid.UUID) -> list[AppliedChange]:
        async with self._uow:
            document = await self._uow.documents.get_by_id(document_id)
            if document is None or document.current_analysis_job_id is None:
                return []
            suggestions = await self._uow.suggestions.list_by_analysis_job_and_status(
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
    # Single-suggestion decisions
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
        updated = await self._uow.suggestions.update_status(suggestion, new_status, user_id)
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
        async with self._uow:
            document = await self._get_document_or_raise(project_id, document_id)
            suggestion = await self._get_suggestion_for_document(document, suggestion_id)
            result = await self._decide(document, suggestion, user_id, SuggestionStatus.ACCEPTED)
            await self._uow.commit()
        return result

    async def reject_suggestion(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        suggestion_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> Suggestion:
        async with self._uow:
            document = await self._get_document_or_raise(project_id, document_id)
            suggestion = await self._get_suggestion_for_document(document, suggestion_id)
            result = await self._decide(document, suggestion, user_id, SuggestionStatus.REJECTED)
            await self._uow.commit()
        return result

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------

    async def bulk_accept(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> BulkAcceptResult:
        async with self._uow:
            document = await self._get_document_or_raise(project_id, document_id)
            if document.status != DocumentStatus.AWAITING_APPROVAL:
                raise InvalidDocumentStatusError(
                    "Решения по правкам доступны только в статусе 'awaiting_approval'"
                )
            if document.current_analysis_job_id is None:
                return BulkAcceptResult(suggestions=[], document=document)
            pending_ids = await self._uow.suggestions.list_ids_by_analysis_job_and_status(
                document.current_analysis_job_id, SuggestionStatus.PENDING
            )
            accepted = await self._uow.suggestions.bulk_update_status(
                document.current_analysis_job_id,
                pending_ids,
                SuggestionStatus.ACCEPTED,
                user_id,
            )
            refreshed = await self._uow.documents.get_by_id(document_id)
            await self._uow.commit()
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
            *[(sid, SuggestionStatus.ACCEPTED) for sid in accepted_ids],
            *[(sid, SuggestionStatus.REJECTED) for sid in rejected_ids],
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
        async with self._uow:
            document = await self._get_document_or_raise(project_id, document_id)
            if document.status != DocumentStatus.AWAITING_APPROVAL:
                raise InvalidDocumentStatusError(
                    "Завершить review можно только в статусе 'awaiting_approval'"
                )
            if document.current_analysis_job_id is None:
                raise ReviewNotCompleteError("У документа отсутствует текущий результат анализа")
            pending_count = await self._uow.suggestions.count_by_analysis_job_and_status(
                document.current_analysis_job_id, SuggestionStatus.PENDING
            )
            if pending_count:
                raise ReviewNotCompleteError(
                    f"Нельзя завершить review: не рассмотрено предложений — {pending_count}"
                )
            if export_service is not None:
                await self._run_export(document, export_service)
            document = await self._uow.documents.update_status(
                document, DocumentStatus.READY
            )
            await self._uow.commit()
        return document

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
        """Сохранить решения под одним оптимистичным локом.

        Весь use-case — один uow.commit():
          1. CAS review_version (OptimisticLock при конфликте)
          2. bulk UPDATE accepted
          3. bulk UPDATE rejected
          4. (опц.) export + статус READY
          5. commit()
        """
        async with self._uow:
            document = await self._get_document_or_raise(project_id, document_id)
            if document.status != DocumentStatus.AWAITING_APPROVAL:
                raise InvalidDocumentStatusError(
                    "Ревью доступно только в статусе 'awaiting_approval'"
                )
            job_id = document.current_analysis_job_id
            if job_id is None:
                raise ReviewNotCompleteError(
                    "У документа отсутствует текущий результат анализа"
                )

            # Дедупликация и проверка противоречий
            normalized: dict[uuid.UUID, SuggestionStatus] = {}
            for suggestion_id, decision in decisions:
                previous = normalized.get(suggestion_id)
                if previous is not None and previous != decision:
                    raise ReviewNotCompleteError(
                        f"Для правки {suggestion_id} переданы противоречивые решения"
                    )
                normalized[suggestion_id] = decision

            # CAS review_version
            document = await self._uow.documents.compare_and_increment_review_version(
                document.id, review_version
            )
            if document is None:
                raise OptimisticLockError(
                    "Документ изменён параллельным запросом. Обновите данные и повторите."
                )

            accepted_ids = [
                sid for sid, dec in normalized.items() if dec == SuggestionStatus.ACCEPTED
            ]
            rejected_ids = [
                sid for sid, dec in normalized.items() if dec == SuggestionStatus.REJECTED
            ]
            accepted = await self._uow.suggestions.bulk_update_status(
                job_id, accepted_ids, SuggestionStatus.ACCEPTED, user_id
            )
            rejected = await self._uow.suggestions.bulk_update_status(
                job_id, rejected_ids, SuggestionStatus.REJECTED, user_id
            )
            if len(accepted) != len(accepted_ids) or len(rejected) != len(rejected_ids):
                raise SuggestionAlreadyDecidedError(
                    "Часть правок не найдена в текущем анализе или уже обработана"
                )

            pending_count = await self._uow.suggestions.count_by_analysis_job_and_status(
                job_id, SuggestionStatus.PENDING
            )
            finalized = False
            if finalize:
                if pending_count:
                    raise ReviewNotCompleteError(
                        f"Нельзя завершить ревью: осталось правок — {pending_count}"
                    )
                if export_service is not None:
                    await self._run_export(document, export_service)
                document = await self._uow.documents.update_status(
                    document, DocumentStatus.READY
                )
                finalized = True

            await self._uow.commit()

        return ReviewSaveResult(
            document=document,
            accepted_count=len(accepted),
            rejected_count=len(rejected),
            pending_count=pending_count,
            finalized=finalized,
        )
