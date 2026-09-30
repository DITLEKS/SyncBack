"""
Бизнес-логика работы с правками (suggestions).

Архитектурные правила:
  - Сервис зависит только от IUnitOfWork (порт) и domain value-objects.
  - Никаких импортов из app.infrastructure.* — ни при выполнении, ни под TYPE_CHECKING.
  - Один uow.commit() на операцию — атомарность гарантируется УоУ.
  - M-1: Document/Suggestion аннотируются через Protocol,
    а не ORM-модель.

HIGH-2-FIX: atomic_review_save — публичный метод с единственным `async with self._uow`.
  Вложенный `async with self._uow` удалён: если IUnitOfWork не реализует
  reentrant-семантику (а SQLAlchemy UoW её не реализует), повторный вход
  открывает новую сессию/транзакцию, и все flush() первого уровня пропадают.
  Метод помечен комментарием — НЕ вызывать внутри уже открытого uow-блока.

REVIEW-5: get_suggestion_by_id удалён — был мёртвым алиасом
  get_suggestion_for_document. Используйте get_suggestion_for_document напрямую.

RESET: reset_suggestion() — отмена ранее принятого/отклонённого решения.
  Проверяет awaiting_approval, делегирует атомарный UPDATE в репозиторий.

S-1 (issue #37): list_suggestions_for_document — PaginationParams распакован
  в limit/offset при вызове list_with_total.

S-2 (issue #37): удалена мёртвая проверка `if updated is None` в _decide();
  update_status() бросает исключение, None никогда не возвращается.

review #3: reset_suggestion выровнен с _decide — проверка `if updated is None`
  заменена на ожидание исключения из reset_status(). Это устраняет расхождение
  стиля обработки ошибок внутри одного класса. reset_status() обязан бросать
  SuggestionResetNotAllowedError если правка уже PENDING (concurrent reset),
  а не возвращать None.

review #5: удалены дублирующие импорты внутри atomic_review_save —
  ReviewDecisions, SuggestionDecision и OptimisticLockError уже импортированы
  на уровне модуля.

PR4-FIX:
  - count_by_document_and_status() добавлен — делегирует в репозиторий
    по каждому статусу; устраняет AttributeError в editor._safe_count_by_status.
  - reset_suggestion(): если reset_status() вернул None (concurrent reset
    уже сбросил правку) — бросаем SuggestionResetNotAllowedError.

MYPY-FIX: все методы получили явные аннотации возврата.
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
    StaleSuggestionJobError,
    SuggestionAlreadyDecidedError,
    SuggestionNotFoundError,
    SuggestionResetNotAllowedError,
)
from app.domain.interfaces.document_exporter import AppliedChange
from app.domain.interfaces.entities import DocumentProtocol, SuggestionProtocol
from app.domain.interfaces.unit_of_work import IUnitOfWork
from app.domain.value_objects import (
    DocumentStatusVO,
    PaginationParams,
    ReviewDecisions,
    SuggestionDecision,
    SuggestionStatusVO,
)

if TYPE_CHECKING:
    from app.domain.services.document_export_service import DocumentExportService

logger = logging.getLogger("syncscribe.services.suggestion")


@dataclass
class ReviewSaveResult:
    """Результат атомарного сохранения сессии ревью."""

    document: DocumentProtocol
    accepted_count: int = 0
    rejected_count: int = 0
    pending_count: int = 0
    finalized: bool = False


@dataclass
class BulkAcceptResult:
    """Результат bulk-accept — список правок + актуальный документ."""

    suggestions: list[SuggestionProtocol]
    document: DocumentProtocol


@dataclass
class BulkRejectResult:
    """Результат bulk-reject — список правок + актуальный документ."""

    suggestions: list[SuggestionProtocol]
    document: DocumentProtocol


class SuggestionService:
    def __init__(self, uow: IUnitOfWork) -> None:
        self._uow = uow

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_document_or_raise(
        self, project_id: uuid.UUID, document_id: uuid.UUID
    ) -> DocumentProtocol:
        document = await self._uow.documents.get_by_id(document_id)
        if document is None or document.project_id != project_id:
            raise DocumentNotFoundError(
                f"Документ {document_id} не найден в проекте {project_id}"
            )
        return document

    async def _get_suggestion_for_document(
        self,
        document: DocumentProtocol,
        suggestion_id: uuid.UUID,
    ) -> SuggestionProtocol:
        """M-9: явно разграничивает «не найдена» vs «не та версия анализа»."""
        suggestion = await self._uow.suggestions.get_by_id(suggestion_id)
        if suggestion is None:
            raise SuggestionNotFoundError(f"Правка {suggestion_id} не найдена")
        if suggestion.analysis_job_id != document.current_analysis_job_id:
            raise StaleSuggestionJobError(
                f"Правка {suggestion_id} принадлежит устаревшему analysis job "
                f"(job_id={suggestion.analysis_job_id}). "
                "Документ был переанализирован — перезагрузите список правок."
            )
        return suggestion

    async def _run_export(
        self,
        document: DocumentProtocol,
        export_service: "DocumentExportService",
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

    def _assert_awaiting_approval(self, document: DocumentProtocol) -> None:
        if document.status != DocumentStatusVO.AWAITING_APPROVAL:
            raise InvalidDocumentStatusError(
                "Решения по правкам доступны только в статусе 'awaiting_approval'"
            )

    def _assert_has_active_job(self, document: DocumentProtocol) -> uuid.UUID:
        if document.current_analysis_job_id is None:
            raise ReviewNotCompleteError(
                "У документа отсутствует текущий результат анализа"
            )
        return document.current_analysis_job_id

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def list_suggestions_for_document(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        pagination: PaginationParams,
    ) -> tuple[list[SuggestionProtocol], int]:
        """S-1 (issue #37): PaginationParams распакован в limit/offset."""
        async with self._uow:
            document = await self._get_document_or_raise(project_id, document_id)
            if document.current_analysis_job_id is None:
                return [], 0
            items, total = await self._uow.suggestions.list_with_total(
                document.current_analysis_job_id,
                limit=pagination.limit,
                offset=pagination.offset,
            )
        return items, total

    async def get_suggestion_for_document(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        suggestion_id: uuid.UUID,
    ) -> SuggestionProtocol:
        async with self._uow:
            document = await self._get_document_or_raise(project_id, document_id)
            return await self._get_suggestion_for_document(document, suggestion_id)

    async def get_accepted_changes(
        self, document_id: uuid.UUID
    ) -> list[AppliedChange]:
        async with self._uow:
            document = await self._uow.documents.get_by_id(document_id)
            if document is None or document.current_analysis_job_id is None:
                return []
            suggestions = await self._uow.suggestions.list_by_analysis_job_and_status(
                document.current_analysis_job_id, SuggestionStatusVO.ACCEPTED
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

    async def count_by_document_and_status(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> dict[str, int]:
        """PR4-FIX: агрегатный счётчик правок по статусам для текущего job."""
        async with self._uow:
            document = await self._get_document_or_raise(project_id, document_id)
            if document.current_analysis_job_id is None:
                return {"pending": 0, "accepted": 0, "rejected": 0}
            job_id = document.current_analysis_job_id
            pending = await self._uow.suggestions.count_by_analysis_job_and_status(
                job_id, SuggestionStatusVO.PENDING
            )
            accepted = await self._uow.suggestions.count_by_analysis_job_and_status(
                job_id, SuggestionStatusVO.ACCEPTED
            )
            rejected = await self._uow.suggestions.count_by_analysis_job_and_status(
                job_id, SuggestionStatusVO.REJECTED
            )
        return {"pending": pending, "accepted": accepted, "rejected": rejected}

    # ------------------------------------------------------------------
    # Single-suggestion decisions
    # ------------------------------------------------------------------

    async def _decide(
        self,
        document: DocumentProtocol,
        suggestion: SuggestionProtocol,
        decision: SuggestionDecision,
    ) -> SuggestionProtocol:
        """CAS-обновление одной правки. S-2: update_status() бросает исключение — None не проверяем."""
        self._assert_awaiting_approval(document)
        await self._uow.suggestions.update_status(suggestion, decision)
        return suggestion

    async def accept_suggestion(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        suggestion_id: uuid.UUID,
        if_match: int | None = None,
    ) -> SuggestionProtocol:
        async with self._uow:
            document = await self._get_document_or_raise(project_id, document_id)
            suggestion = await self._get_suggestion_for_document(document, suggestion_id)
            if suggestion.status != SuggestionStatusVO.PENDING:
                raise SuggestionAlreadyDecidedError(
                    f"Правка {suggestion_id} уже имеет статус '{suggestion.status.value}'"
                )
            if if_match is not None and suggestion.version != if_match:
                raise OptimisticLockError(
                    f"Версия правки изменилась: ожидалась {if_match}, текущая {suggestion.version}"
                )
            result = await self._decide(document, suggestion, SuggestionDecision.ACCEPT)
            await self._uow.commit()
        return result

    async def reject_suggestion(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        suggestion_id: uuid.UUID,
        if_match: int | None = None,
    ) -> SuggestionProtocol:
        async with self._uow:
            document = await self._get_document_or_raise(project_id, document_id)
            suggestion = await self._get_suggestion_for_document(document, suggestion_id)
            if suggestion.status != SuggestionStatusVO.PENDING:
                raise SuggestionAlreadyDecidedError(
                    f"Правка {suggestion_id} уже имеет статус '{suggestion.status.value}'"
                )
            if if_match is not None and suggestion.version != if_match:
                raise OptimisticLockError(
                    f"Версия правки изменилась: ожидалась {if_match}, текущая {suggestion.version}"
                )
            result = await self._decide(document, suggestion, SuggestionDecision.REJECT)
            await self._uow.commit()
        return result

    async def reset_suggestion(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        suggestion_id: uuid.UUID,
    ) -> SuggestionProtocol:
        """Отмена ранее принятого/отклонённого решения → PENDING."""
        async with self._uow:
            document = await self._get_document_or_raise(project_id, document_id)
            self._assert_awaiting_approval(document)
            suggestion = await self._get_suggestion_for_document(document, suggestion_id)
            if suggestion.status == SuggestionStatusVO.PENDING:
                raise SuggestionResetNotAllowedError(
                    f"Правка {suggestion_id} уже находится в статусе PENDING"
                )
            # reset_status() бросает SuggestionResetNotAllowedError при concurrent reset
            await self._uow.suggestions.reset_status(suggestion)
            await self._uow.commit()
        return suggestion

    # ------------------------------------------------------------------
    # Bulk decisions
    # ------------------------------------------------------------------

    async def bulk_accept_suggestions(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        suggestion_ids: list[uuid.UUID],
    ) -> BulkAcceptResult:
        async with self._uow:
            document = await self._get_document_or_raise(project_id, document_id)
            self._assert_awaiting_approval(document)
            job_id = self._assert_has_active_job(document)
            updated = await self._uow.suggestions.bulk_update_status(
                job_id=job_id,
                suggestion_ids=suggestion_ids,
                decision=SuggestionDecision.ACCEPT,
            )
            await self._uow.commit()
        return BulkAcceptResult(suggestions=updated, document=document)

    async def bulk_reject_suggestions(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        suggestion_ids: list[uuid.UUID],
    ) -> BulkRejectResult:
        async with self._uow:
            document = await self._get_document_or_raise(project_id, document_id)
            self._assert_awaiting_approval(document)
            job_id = self._assert_has_active_job(document)
            updated = await self._uow.suggestions.bulk_update_status(
                job_id=job_id,
                suggestion_ids=suggestion_ids,
                decision=SuggestionDecision.REJECT,
            )
            await self._uow.commit()
        return BulkRejectResult(suggestions=updated, document=document)

    async def bulk_accept_all(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> BulkAcceptResult:
        """C-3: принять все PENDING правки текущего job одним UPDATE."""
        async with self._uow:
            document = await self._get_document_or_raise(project_id, document_id)
            self._assert_awaiting_approval(document)
            job_id = self._assert_has_active_job(document)
            updated = await self._uow.suggestions.bulk_accept_all(job_id)
            await self._uow.commit()
        return BulkAcceptResult(suggestions=updated, document=document)

    async def bulk_reject_all(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> BulkRejectResult:
        """C-3: отклонить все PENDING правки текущего job одним UPDATE."""
        async with self._uow:
            document = await self._get_document_or_raise(project_id, document_id)
            self._assert_awaiting_approval(document)
            job_id = self._assert_has_active_job(document)
            updated = await self._uow.suggestions.bulk_reject_all(job_id)
            await self._uow.commit()
        return BulkRejectResult(suggestions=updated, document=document)

    # ------------------------------------------------------------------
    # Review session (atomic save)
    # ------------------------------------------------------------------

    async def atomic_review_save(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        decisions: ReviewDecisions,
        export_service: "DocumentExportService | None" = None,
    ) -> ReviewSaveResult:
        """HIGH-2-FIX: единственный `async with self._uow` — НЕ вызывать внутри uow-блока.

        Алгоритм:
          1. Загрузить документ и job_id.
          2. Применить все решения батчем через bulk_update_status.
          3. Подсчитать итоги.
          4. Если все решены и передан export_service — финализировать.
          5. Один commit().
        """
        async with self._uow:
            document = await self._get_document_or_raise(project_id, document_id)
            self._assert_awaiting_approval(document)
            job_id = self._assert_has_active_job(document)

            if decisions.accept:
                await self._uow.suggestions.bulk_update_status(
                    job_id=job_id,
                    suggestion_ids=decisions.accept,
                    decision=SuggestionDecision.ACCEPT,
                )
            if decisions.reject:
                await self._uow.suggestions.bulk_update_status(
                    job_id=job_id,
                    suggestion_ids=decisions.reject,
                    decision=SuggestionDecision.REJECT,
                )

            pending = await self._uow.suggestions.count_by_analysis_job_and_status(
                job_id, SuggestionStatusVO.PENDING
            )
            accepted = await self._uow.suggestions.count_by_analysis_job_and_status(
                job_id, SuggestionStatusVO.ACCEPTED
            )
            rejected = await self._uow.suggestions.count_by_analysis_job_and_status(
                job_id, SuggestionStatusVO.REJECTED
            )

            finalized = False
            if pending == 0 and export_service is not None:
                await self._uow.documents.update_status(
                    document, DocumentStatusVO.READY
                )
                finalized = True

            await self._uow.commit()

        if finalized and export_service is not None:
            await self._run_export(document, export_service)

        return ReviewSaveResult(
            document=document,
            accepted_count=accepted,
            rejected_count=rejected,
            pending_count=pending,
            finalized=finalized,
        )
