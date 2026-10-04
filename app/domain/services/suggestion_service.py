"""Бизнес-логика работы с правками (suggestions).

Сервис зависит только от IUnitOfWork и доменных value objects; каждая публичная
операция сама открывает `async with self._uow` и делает один commit, поэтому
вызывать их внутри уже открытого UoW-блока нельзя (UoW не реентерабелен).
Принятые правки читаются напрямую через uow.suggestions, а не через
DocumentExportService — иначе возникает циклическая зависимость сервисов.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

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
from app.domain.interfaces.event_publisher import IEventPublisher
from app.domain.interfaces.unit_of_work import IUnitOfWork
from app.domain.lifecycle import DocumentLifecycle
from app.domain.services.document_events import DocumentEventOutbox
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
class PatchSuggestionsResult:
    """Результат массового изменения статуса правок."""

    document: DocumentProtocol
    updated_ids: list[uuid.UUID] = field(default_factory=list)

    @property
    def updated_count(self) -> int:
        return len(self.updated_ids)


PatchFilter = Literal["pending", "decided", "all"]


class SuggestionService:
    def __init__(self, uow: IUnitOfWork, *, events: IEventPublisher | None = None) -> None:
        self._uow = uow
        self._outbox = DocumentEventOutbox(events)

    async def _commit(self) -> None:
        try:
            await self._uow.commit()
        except BaseException:
            self._outbox.discard()
            raise
        await self._outbox.flush(self._uow)

    async def _finish_review(self, document: DocumentProtocol) -> DocumentProtocol:
        document = await self._uow.documents.update_status(
            document, DocumentLifecycle.transition(document.status, DocumentStatusVO.READY)
        )
        self._outbox.record(document)
        return document

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_document_or_raise(
        self, project_id: uuid.UUID, document_id: uuid.UUID
    ) -> DocumentProtocol:
        document = await self._uow.documents.get_by_id(document_id)
        if document is None or document.project_id != project_id:
            raise DocumentNotFoundError(f"Документ {document_id} не найден в проекте {project_id}")
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
                f"Документ был переанализирован — перезагрузите список правок."
            )
        return suggestion

    async def _run_export(
        self,
        document: DocumentProtocol,
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

    def _assert_awaiting_approval(self, document: DocumentProtocol) -> None:
        if not DocumentLifecycle.can_review(document.status):
            raise InvalidDocumentStatusError(
                "Решения по правкам доступны только в статусе 'awaiting_approval'"
            )

    def _assert_has_active_job(self, document: DocumentProtocol) -> uuid.UUID:
        if document.current_analysis_job_id is None:
            raise ReviewNotCompleteError("У документа отсутствует текущий результат анализа")
        return document.current_analysis_job_id

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def list_suggestions_for_document(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        pagination: PaginationParams,
        status_filter: SuggestionStatusVO | None = None,
    ) -> tuple[list[SuggestionProtocol], int]:
        """Страница правок текущего анализа документа и их общее число."""
        async with self._uow:
            document = await self._get_document_or_raise(project_id, document_id)
            if document.current_analysis_job_id is None:
                return [], 0
            items, total = await self._uow.suggestions.list_with_total(
                document.current_analysis_job_id,
                limit=pagination.limit,
                offset=pagination.offset,
                status=status_filter,
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

    async def get_accepted_changes(self, document_id: uuid.UUID) -> list[AppliedChange]:
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
                old_text=s.original_text,
                new_text=s.suggested_text,
            )
            for s in suggestions
        ]

    async def count_by_document_and_status(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
    ) -> dict[str, int]:
        """Счётчики правок текущего анализа: {'pending': N, 'accepted': N, 'rejected': N}."""
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
    # Decisions
    # ------------------------------------------------------------------

    async def reset_suggestion(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        suggestion_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> SuggestionProtocol:
        """Отменить ранее принятое или отклонённое решение — вернуть правку в PENDING.

        Документ должен быть в AWAITING_APPROVAL, правка — принадлежать текущему
        анализу и быть ACCEPTED/REJECTED.
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
                raise SuggestionResetNotAllowedError(
                    f"Правка {suggestion_id} была сброшена параллельным запросом. "
                    "Обновите список правок и повторите."
                )
            await self._commit()
        return updated

    # ------------------------------------------------------------------
    # Bulk operations
    # ------------------------------------------------------------------

    async def patch_suggestions(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        user_id: uuid.UUID,
        target_status: SuggestionStatusVO,
        ids: Sequence[uuid.UUID] | None = None,
        filter: PatchFilter | None = None,
    ) -> PatchSuggestionsResult:
        """Перевести правки текущего анализа в target_status одним UPDATE.

        Выбор правок — либо явный список ids, либо filter ("pending", "decided",
        "all"). Допустимые переходы: pending → accepted/rejected и
        accepted/rejected → pending (сброс решения). Для явного списка ids все
        правки обязаны быть в допустимом исходном статусе, иначе
        SuggestionAlreadyDecidedError / SuggestionResetNotAllowedError; для
        фильтра неподходящие правки просто пропускаются.
        """
        if (ids is None) == (filter is None):
            raise ValueError("Укажите ровно одно из: ids или filter")

        async with self._uow:
            document = await self._get_document_or_raise(project_id, document_id)
            self._assert_awaiting_approval(document)
            job_id = self._assert_has_active_job(document)

            if target_status == SuggestionStatusVO.PENDING:
                updated_ids = await self._reset_to_pending(job_id, ids, filter)
            else:
                updated_ids = await self._decide_pending(
                    document_id, job_id, user_id, target_status, ids, filter
                )

            refreshed = await self._uow.documents.get_by_id(document_id)
            await self._commit()
        return PatchSuggestionsResult(document=refreshed or document, updated_ids=updated_ids)

    async def _decide_pending(
        self,
        document_id: uuid.UUID,
        job_id: uuid.UUID,
        user_id: uuid.UUID,
        target_status: SuggestionStatusVO,
        ids: Sequence[uuid.UUID] | None,
        filter: PatchFilter | None,
    ) -> list[uuid.UUID]:
        if ids is not None:
            unique_ids = list(dict.fromkeys(ids))
            decisions = ReviewDecisions(
                decisions=tuple(
                    SuggestionDecision(suggestion_id=sid, status=target_status, user_id=user_id)
                    for sid in unique_ids
                ),
                document_id=document_id,
                user_id=user_id,
                analysis_job_id=job_id,
            )
            updated = await self._uow.suggestions.bulk_update_status(decisions)
            if updated != len(unique_ids):
                raise SuggestionAlreadyDecidedError(
                    "Часть правок не найдена в текущем анализе или уже обработана"
                )
            return unique_ids

        if filter == "decided":
            raise SuggestionAlreadyDecidedError(
                "Принять или отклонить можно только правки в статусе pending"
            )
        if target_status == SuggestionStatusVO.ACCEPTED:
            updated_suggestions = await self._uow.suggestions.bulk_accept_all(job_id, user_id)
        else:
            updated_suggestions = await self._uow.suggestions.bulk_reject_all(job_id, user_id)
        return [s.id for s in updated_suggestions]

    async def _reset_to_pending(
        self,
        job_id: uuid.UUID,
        ids: Sequence[uuid.UUID] | None,
        filter: PatchFilter | None,
    ) -> list[uuid.UUID]:
        if ids is None:
            if filter == "pending":
                return []
            return await self._uow.suggestions.reset_to_pending(job_id)

        unique_ids = list(dict.fromkeys(ids))
        reset_ids = await self._uow.suggestions.reset_to_pending(job_id, unique_ids)
        if len(reset_ids) != len(unique_ids):
            raise SuggestionResetNotAllowedError(
                "Часть правок не найдена в текущем анализе или уже в статусе pending"
            )
        return reset_ids

    async def finalize_review(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        user_id: uuid.UUID,
        export_service: DocumentExportService | None = None,
    ) -> DocumentProtocol:
        """Завершить ревью: все правки рассмотрены → документ в READY."""
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
            document = await self._finish_review(document)
            await self._commit()
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
        export_service: DocumentExportService | None = None,
    ) -> ReviewSaveResult:
        """Сохранить решения ревью под одним оптимистичным локом (review_version).

        При finalize=True документ переводится в READY, если не осталось
        pending-правок.
        """
        async with self._uow:
            document = await self._get_document_or_raise(project_id, document_id)
            self._assert_awaiting_approval(document)
            job_id = self._assert_has_active_job(document)

            decisions_list = (
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
            )
            decisions_vo = ReviewDecisions(
                decisions=decisions_list,
                document_id=document_id,
                user_id=user_id,
                analysis_job_id=job_id,
            )

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
                document = await self._finish_review(document)
                finalized = True

            await self._commit()

        return ReviewSaveResult(
            document=document,
            accepted_count=accepted_count,
            rejected_count=rejected_count,
            pending_count=pending_count,
            finalized=finalized,
        )
