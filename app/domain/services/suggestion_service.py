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
            raise SuggestionNotFoundError(
                f"Правка {suggestion_id} не найдена"
            )
        if suggestion.analysis_job_id != document.current_analysis_job_id:
            raise StaleSuggestionJobError(
                f"Правка {suggestion_id} принадлежит устаревшему analysis job "
                f"(job_id={suggestion.analysis_job_id}). "
                f"Документ был переанализирован — перезагрузите список правок."
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
        """Один SELECT с COUNT(*) OVER() вместо двух запросов (H-3)."""
        async with self._uow:
            document = await self._get_document_or_raise(project_id, document_id)
            if document.current_analysis_job_id is None:
                return [], 0
            items, total = await self._uow.suggestions.list_with_total(
                document.current_analysis_job_id, pagination
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

    # ------------------------------------------------------------------
    # Single-suggestion decisions
    # ------------------------------------------------------------------

    async def _decide(
        self,
        document: DocumentProtocol,
        suggestion: SuggestionProtocol,
        decision: SuggestionDecision,
    ) -> SuggestionProtocol:
        """CAS-обновление одной правки."""
        self._assert_awaiting_approval(document)
        updated = await self._uow.suggestions.update_status(suggestion, decision)
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
    ) -> SuggestionProtocol:
        async with self._uow:
            document = await self._get_document_or_raise(project_id, document_id)
            suggestion = await self._get_suggestion_for_document(document, suggestion_id)
            decision = SuggestionDecision(
                suggestion_id=suggestion_id,
                status=SuggestionStatusVO.ACCEPTED,
                user_id=user_id,
            )
            result = await self._decide(document, suggestion, decision)
            await self._uow.commit()
        return result

    async def reject_suggestion(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        suggestion_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> SuggestionProtocol:
        async with self._uow:
            document = await self._get_document_or_raise(project_id, document_id)
            suggestion = await self._get_suggestion_for_document(document, suggestion_id)
            decision = SuggestionDecision(
                suggestion_id=suggestion_id,
                status=SuggestionStatusVO.REJECTED,
                user_id=user_id,
            )
            result = await self._decide(document, suggestion, decision)
            await self._uow.commit()
        return result

    async def reset_suggestion(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        suggestion_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> SuggestionProtocol:
        """Отменить ранее принятое или отклонённое решение — вернуть в PENDING.

        Бизнес-правила:
          1. Документ обязан быть в статусе AWAITING_APPROVAL — только
             в этом окне редактор имеет право менять решения.
          2. Правка обязана принадлежать текущему analysis job (stale-проверка).
          3. Правка должна быть ACCEPTED или REJECTED — сбрасывать PENDING
             бессмысленно и является ошибкой клиента (409).

        Атомарность обеспечивается UPDATE WHERE status != PENDING в репозитории.
        """
        async with self._uow:
            document = await self._get_document_or_raise(project_id, document_id)
            self._assert_awaiting_approval(document)
            suggestion = await self._get_suggestion_for_document(document, suggestion_id)

            if suggestion.status == SuggestionStatusVO.PENDING:
                raise SuggestionResetNotAllowedError(
                    f"Правка {suggestion_id} уже в статусе PENDING — сбрасывать нечего"
                )

            updated = await self._uow.suggestions.reset_status(suggestion)
            if updated is None:
                # Гонка: параллельный reset уже отработал между нашим get_by_id и UPDATE.
                # Возвращаем текущее состояние (уже PENDING) — идемпотентный исход.
                raise SuggestionResetNotAllowedError(
                    f"Правка {suggestion_id} уже была сброшена параллельным запросом"
                )

            await self._uow.commit()
        return updated

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------

    async def bulk_accept(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> BulkAcceptResult:
        """Принять все PENDING-правки одним UPDATE без загрузки UUID в память (H-4)."""
        async with self._uow:
            document = await self._get_document_or_raise(project_id, document_id)
            self._assert_awaiting_approval(document)
            job_id = self._assert_has_active_job(document)
            accepted = await self._uow.suggestions.bulk_accept_all(job_id, user_id)
            refreshed = await self._uow.documents.get_by_id(document_id)
            await self._uow.commit()
        return BulkAcceptResult(suggestions=accepted, document=refreshed or document)

    async def bulk_reject(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> BulkRejectResult:
        """Отклонить все PENDING-правки одним UPDATE (зеркало bulk_accept).

        Использует bulk_reject_all репозитория — один UPDATE WHERE status=PENDING.
        Не загружает UUID в память: репозиторий возвращает обновлённые объекты.
        """
        async with self._uow:
            document = await self._get_document_or_raise(project_id, document_id)
            self._assert_awaiting_approval(document)
            job_id = self._assert_has_active_job(document)
            rejected = await self._uow.suggestions.bulk_reject_all(job_id, user_id)
            refreshed = await self._uow.documents.get_by_id(document_id)
            await self._uow.commit()
        return BulkRejectResult(suggestions=rejected, document=refreshed or document)

    async def apply_review(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        user_id: uuid.UUID,
        review_version: int,
        accepted_ids: tuple[uuid.UUID, ...],
        rejected_ids: tuple[uuid.UUID, ...],
    ) -> int:
        """Применить решения ревью без финализации.
        Возвращает новую review_version документа.
        """
        result = await self.atomic_review_save(
            project_id=project_id,
            document_id=document_id,
            user_id=user_id,
            review_version=review_version,
            accepted_ids=accepted_ids,
            rejected_ids=rejected_ids,
            finalize=False,
        )
        return result.document.review_version

    async def finalize_review(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        user_id: uuid.UUID,
        export_service: "DocumentExportService | None" = None,
    ) -> DocumentProtocol:
        """M-7: принимает user_id для аудита кто финализировал ревью."""
        async with self._uow:
            document = await self._get_document_or_raise(project_id, document_id)
            self._assert_awaiting_approval(document)
            job_id = self._assert_has_active_job(document)

            pending_count = await self._uow.suggestions.count_by_analysis_job_and_status(
                job_id, SuggestionStatusVO.PENDING
            )
            if pending_count:
                raise ReviewNotCompleteError(
                    f"Нельзя завершить review: не рассмотрено предложений — {pending_count}"
                )
            if export_service is not None:
                await self._run_export(document, export_service)
            document = await self._uow.documents.update_status(
                document, DocumentStatusVO.READY
            )
            await self._uow.commit()
        return document

    async def atomic_review_save(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        user_id: uuid.UUID,
        review_version: int,
        accepted_ids: tuple[uuid.UUID, ...] = (),
        rejected_ids: tuple[uuid.UUID, ...] = (),
        finalize: bool = True,
        export_service: "DocumentExportService | None" = None,
    ) -> ReviewSaveResult:
        """Сохранить решения под одним оптимистичным локом.

        HIGH-2-FIX: единственный `async with self._uow` — вложенный блок удалён.
        IUnitOfWork (SQLAlchemy) не реализует reentrant-семантику: повторный
        вход в `async with uow` открывал бы новую сессию/транзакцию, теряя
        все flush() внешнего уровня и нарушая атомарность.

        ВАЖНО: метод сам открывает uow-блок — НЕ вызывать внутри
        уже открытого `async with self._uow`.
        """
        async with self._uow:
            document = await self._get_document_or_raise(project_id, document_id)
            self._assert_awaiting_approval(document)
            job_id = self._assert_has_active_job(document)

            from app.domain.value_objects import ReviewDecisions, SuggestionDecision
            decisions_list = tuple([
                *[
                    SuggestionDecision(
                        suggestion_id=sid,
                        status=SuggestionStatusVO.ACCEPTED,
                        user_id=user_id,
                    )
                    for sid in accepted_ids
                ],
                *[
                    SuggestionDecision(
                        suggestion_id=sid,
                        status=SuggestionStatusVO.REJECTED,
                        user_id=user_id,
                    )
                    for sid in rejected_ids
                ],
            ])
            decisions_vo = ReviewDecisions(
                decisions=decisions_list,
                document_id=document_id,
                user_id=user_id,
            )

            from app.domain.exceptions import OptimisticLockError
            locked_doc = await self._uow.documents.compare_and_increment_review_version(
                document.id, review_version
            )
            if locked_doc is None:
                raise OptimisticLockError(
                    "Документ изменён параллельным запросом. Обновите данные и повторите."
                )
            document = locked_doc

            updated = await self._uow.suggestions.bulk_update_status(decisions_vo)

            expected_total = len(decisions_list)
            if updated != expected_total:
                raise SuggestionAlreadyDecidedError(
                    "Часть правок не найдена в текущем анализе или уже обработана"
                )

            accepted_count = len(accepted_ids)
            rejected_count = len(rejected_ids)

            pending_count = await self._uow.suggestions.count_by_analysis_job_and_status(
                job_id, SuggestionStatusVO.PENDING
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
                    document, DocumentStatusVO.READY
                )
                finalized = True

            await self._uow.commit()

        return ReviewSaveResult(
            document=document,
            accepted_count=accepted_count,
            rejected_count=rejected_count,
            pending_count=pending_count,
            finalized=finalized,
        )
