"""
Бизнес-логика работы с правками (suggestions).

Архитектурные правила:
  - Сервис зависит только от IUnitOfWork (порт) и domain value-objects.
  - Никаких импортов из app.infrastructure.* — ни при выполнении, ни под TYPE_CHECKING.
  - Один uow.commit() на операцию — атомарность гарантируется UoW.

CONTRACT-FIX (согласование с роутером и репозиторием):
  - patch_suggestions() + PatchSuggestionsResult — единая точка PATCH /suggestions
    (селекторы ids / filter, целевой статус accepted|rejected|pending).
  - list_suggestions_for_document(status_filter=...) пробрасывается в list_with_total.
  - Решения строятся как SuggestionDecision(suggestion_id, status, user_id) и
    ReviewDecisions(decisions, document_id, user_id) — как ждёт репозиторий.
    Прежние SuggestionDecision.ACCEPT / .REJECT и ReviewDecisions.accept/.reject
    не существуют.
  - bulk_accept_all / bulk_reject_all передают user_id.
  - accept/reject/reset принимают user_id (4-м позиционным аргументом).
  - atomic_review_save(user_id, review_version, accepted_ids, rejected_ids, finalize):
    CAS по document.review_version (OptimisticLockError), инкремент версии,
    finalize=True при pending>0 -> ReviewNotCompleteError.

НЕ вызывать публичные методы внутри уже открытого `async with self._uow`.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal, Sequence

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

PatchFilter = Literal["pending", "decided", "all"]
PatchTarget = Literal["accepted", "rejected", "pending"]


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
    suggestions: list[SuggestionProtocol]
    document: DocumentProtocol


@dataclass
class BulkRejectResult:
    suggestions: list[SuggestionProtocol]
    document: DocumentProtocol


@dataclass
class PatchSuggestionsResult:
    """Результат PATCH /suggestions."""

    document: DocumentProtocol
    updated_ids: list[uuid.UUID] = field(default_factory=list)

    @property
    def updated_count(self) -> int:
        return len(self.updated_ids)


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
        """Разграничивает «не найдена» vs «не та версия анализа»."""
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

    @staticmethod
    def _bump_review_version(document: DocumentProtocol) -> None:
        document.review_version = (document.review_version or 0) + 1  # type: ignore[attr-defined]

    async def _apply_decisions(
        self,
        document_id: uuid.UUID,
        user_id: uuid.UUID,
        accepted: Sequence[uuid.UUID],
        rejected: Sequence[uuid.UUID],
    ) -> int:
        """Один batch-UPDATE через ReviewDecisions. Возвращает rowcount."""
        decisions = tuple(
            [
                SuggestionDecision(sid, SuggestionStatusVO.ACCEPTED, user_id)
                for sid in accepted
            ]
            + [
                SuggestionDecision(sid, SuggestionStatusVO.REJECTED, user_id)
                for sid in rejected
            ]
        )
        if not decisions:
            return 0
        return await self._uow.suggestions.bulk_update_status(
            ReviewDecisions(
                decisions=decisions, document_id=document_id, user_id=user_id
            )
        )

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
        async with self._uow:
            document = await self._get_document_or_raise(project_id, document_id)
            if document.current_analysis_job_id is None:
                return {"pending": 0, "accepted": 0, "rejected": 0}
            job_id = document.current_analysis_job_id
            counts = {}
            for vo in (
                SuggestionStatusVO.PENDING,
                SuggestionStatusVO.ACCEPTED,
                SuggestionStatusVO.REJECTED,
            ):
                counts[vo.value] = (
                    await self._uow.suggestions.count_by_analysis_job_and_status(
                        job_id, vo
                    )
                )
        return counts

    # ------------------------------------------------------------------
    # PATCH /suggestions (single + bulk)
    # ------------------------------------------------------------------

    async def patch_suggestions(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        user_id: uuid.UUID,
        ids: list[uuid.UUID] | None,
        filter: PatchFilter | None,
        target_status: PatchTarget,
    ) -> PatchSuggestionsResult:
        """Единая точка изменения статуса правок.

        ids    — строгий режим: любая правка не в нужном исходном статусе
                 -> SuggestionAlreadyDecidedError / SuggestionResetNotAllowedError.
        filter — мягкий режим: берутся только подходящие правки, остальные
                 пропускаются.
        """
        if (ids is not None and len(ids) > 0) == (filter is not None):
            raise ValueError("Нужно указать ровно одно из: ids или filter")

        target = SuggestionStatusVO(target_status)
        is_reset = target == SuggestionStatusVO.PENDING

        async with self._uow:
            document = await self._get_document_or_raise(project_id, document_id)
            self._assert_awaiting_approval(document)
            job_id = self._assert_has_active_job(document)

            if ids:
                candidates: list[SuggestionProtocol] = []
                for sid in dict.fromkeys(ids):
                    candidates.append(
                        await self._get_suggestion_for_document(document, sid)
                    )
                for s in candidates:
                    if is_reset and s.status == SuggestionStatusVO.PENDING:
                        raise SuggestionResetNotAllowedError(
                            f"Правка {s.id} уже находится в статусе PENDING"
                        )
                    if not is_reset and s.status != SuggestionStatusVO.PENDING:
                        raise SuggestionAlreadyDecidedError(
                            f"Правка {s.id} уже имеет статус '{s.status.value}'"
                        )
            else:
                all_items = await self._uow.suggestions.list_by_analysis_job(job_id)
                if filter == "pending":
                    pool = [
                        s for s in all_items if s.status == SuggestionStatusVO.PENDING
                    ]
                elif filter == "decided":
                    pool = [
                        s for s in all_items if s.status != SuggestionStatusVO.PENDING
                    ]
                else:
                    pool = list(all_items)
                if is_reset:
                    candidates = [
                        s for s in pool if s.status != SuggestionStatusVO.PENDING
                    ]
                else:
                    candidates = [
                        s for s in pool if s.status == SuggestionStatusVO.PENDING
                    ]

            updated_ids: list[uuid.UUID] = []
            if candidates:
                if is_reset:
                    for s in candidates:
                        if await self._uow.suggestions.reset_status(s) is not None:
                            updated_ids.append(s.id)
                else:
                    cand_ids = [s.id for s in candidates]
                    accepted = cand_ids if target == SuggestionStatusVO.ACCEPTED else []
                    rejected = cand_ids if target == SuggestionStatusVO.REJECTED else []
                    await self._apply_decisions(
                        document_id, user_id, accepted, rejected
                    )
                    updated_ids = cand_ids

            if updated_ids:
                self._bump_review_version(document)
            await self._uow.commit()

        return PatchSuggestionsResult(document=document, updated_ids=updated_ids)

    # ------------------------------------------------------------------
    # Single-suggestion decisions
    # ------------------------------------------------------------------

    async def _decide(
        self,
        document: DocumentProtocol,
        suggestion: SuggestionProtocol,
        status: SuggestionStatusVO,
        user_id: uuid.UUID,
    ) -> SuggestionProtocol:
        """CAS-обновление одной правки. update_status() бросает исключение."""
        self._assert_awaiting_approval(document)
        await self._uow.suggestions.update_status(
            suggestion, SuggestionDecision(suggestion.id, status, user_id)
        )
        self._bump_review_version(document)
        return suggestion

    async def _decide_single(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        suggestion_id: uuid.UUID,
        user_id: uuid.UUID,
        if_match: int | None,
        status: SuggestionStatusVO,
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
            result = await self._decide(document, suggestion, status, user_id)
            await self._uow.commit()
        return result

    async def accept_suggestion(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        suggestion_id: uuid.UUID,
        user_id: uuid.UUID,
        if_match: int | None = None,
    ) -> SuggestionProtocol:
        return await self._decide_single(
            project_id, document_id, suggestion_id, user_id, if_match,
            SuggestionStatusVO.ACCEPTED,
        )

    async def reject_suggestion(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        suggestion_id: uuid.UUID,
        user_id: uuid.UUID,
        if_match: int | None = None,
    ) -> SuggestionProtocol:
        return await self._decide_single(
            project_id, document_id, suggestion_id, user_id, if_match,
            SuggestionStatusVO.REJECTED,
        )

    async def reset_suggestion(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        suggestion_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> SuggestionProtocol:
        """Отмена ранее принятого/отклонённого решения -> PENDING.

        user_id принят для единообразия сигнатуры и аудита (пишет роутер).
        """
        async with self._uow:
            document = await self._get_document_or_raise(project_id, document_id)
            self._assert_awaiting_approval(document)
            suggestion = await self._get_suggestion_for_document(document, suggestion_id)
            if suggestion.status == SuggestionStatusVO.PENDING:
                raise SuggestionResetNotAllowedError(
                    f"Правка {suggestion_id} уже находится в статусе PENDING"
                )
            if await self._uow.suggestions.reset_status(suggestion) is None:
                raise SuggestionResetNotAllowedError(
                    f"Правка {suggestion_id} уже сброшена конкурентным запросом"
                )
            self._bump_review_version(document)
            await self._uow.commit()
        return suggestion

    # ------------------------------------------------------------------
    # Bulk decisions
    # ------------------------------------------------------------------

    async def _bulk_by_ids(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        suggestion_ids: list[uuid.UUID],
        user_id: uuid.UUID,
        status: SuggestionStatusVO,
    ) -> tuple[list[SuggestionProtocol], DocumentProtocol]:
        async with self._uow:
            document = await self._get_document_or_raise(project_id, document_id)
            self._assert_awaiting_approval(document)
            job_id = self._assert_has_active_job(document)
            accepted = suggestion_ids if status == SuggestionStatusVO.ACCEPTED else []
            rejected = suggestion_ids if status == SuggestionStatusVO.REJECTED else []
            await self._apply_decisions(document_id, user_id, accepted, rejected)
            wanted = set(suggestion_ids)
            decided = await self._uow.suggestions.list_by_analysis_job_and_status(
                job_id, status
            )
            updated = [s for s in decided if s.id in wanted]
            self._bump_review_version(document)
            await self._uow.commit()
        return updated, document

    async def bulk_accept_suggestions(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        suggestion_ids: list[uuid.UUID],
        user_id: uuid.UUID,
    ) -> BulkAcceptResult:
        updated, document = await self._bulk_by_ids(
            project_id, document_id, suggestion_ids, user_id,
            SuggestionStatusVO.ACCEPTED,
        )
        return BulkAcceptResult(suggestions=updated, document=document)

    async def bulk_reject_suggestions(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        suggestion_ids: list[uuid.UUID],
        user_id: uuid.UUID,
    ) -> BulkRejectResult:
        updated, document = await self._bulk_by_ids(
            project_id, document_id, suggestion_ids, user_id,
            SuggestionStatusVO.REJECTED,
        )
        return BulkRejectResult(suggestions=updated, document=document)

    async def bulk_accept_all(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> BulkAcceptResult:
        async with self._uow:
            document = await self._get_document_or_raise(project_id, document_id)
            self._assert_awaiting_approval(document)
            job_id = self._assert_has_active_job(document)
            updated = await self._uow.suggestions.bulk_accept_all(job_id, user_id)
            if updated:
                self._bump_review_version(document)
            await self._uow.commit()
        return BulkAcceptResult(suggestions=updated, document=document)

    async def bulk_reject_all(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> BulkRejectResult:
        async with self._uow:
            document = await self._get_document_or_raise(project_id, document_id)
            self._assert_awaiting_approval(document)
            job_id = self._assert_has_active_job(document)
            updated = await self._uow.suggestions.bulk_reject_all(job_id, user_id)
            if updated:
                self._bump_review_version(document)
            await self._uow.commit()
        return BulkRejectResult(suggestions=updated, document=document)

    # ------------------------------------------------------------------
    # Review session (atomic save)
    # ------------------------------------------------------------------

    async def atomic_review_save(
        self,
        project_id: uuid.UUID,
        document_id: uuid.UUID,
        user_id: uuid.UUID,
        review_version: int | None,
        accepted_ids: Sequence[uuid.UUID],
        rejected_ids: Sequence[uuid.UUID],
        finalize: bool = False,
        export_service: "DocumentExportService | None" = None,
    ) -> ReviewSaveResult:
        """Единственный `async with self._uow` — НЕ вызывать внутри uow-блока.

          1. Загрузить документ, проверить статус и review_version (CAS).
          2. Применить решения одним batch-UPDATE.
          3. Подсчитать итоги, увеличить review_version.
          4. finalize: при pending>0 -> ReviewNotCompleteError, иначе статус READY.
          5. Один commit(); экспорт файла (если передан export_service) — после него.
        """
        async with self._uow:
            document = await self._get_document_or_raise(project_id, document_id)
            self._assert_awaiting_approval(document)
            job_id = self._assert_has_active_job(document)

            current_version = getattr(document, "review_version", 0) or 0
            if review_version is not None and current_version != review_version:
                raise OptimisticLockError(
                    f"Версия ревью изменилась: ожидалась {review_version}, "
                    f"текущая {current_version}"
                )

            await self._apply_decisions(
                document_id, user_id, list(accepted_ids), list(rejected_ids)
            )

            counts: dict[SuggestionStatusVO, int] = {}
            for vo in SuggestionStatusVO:
                counts[vo] = await self._uow.suggestions.count_by_analysis_job_and_status(
                    job_id, vo
                )
            pending = counts[SuggestionStatusVO.PENDING]

            finalized = False
            if finalize:
                if pending > 0:
                    raise ReviewNotCompleteError(
                        f"Нельзя завершить ревью: осталось {pending} нерешённых правок"
                    )
                await self._uow.documents.update_status(
                    document, DocumentStatusVO.READY
                )
                finalized = True

            self._bump_review_version(document)
            await self._uow.commit()

        if finalized and export_service is not None:
            await self._run_export(document, export_service)

        return ReviewSaveResult(
            document=document,
            accepted_count=counts[SuggestionStatusVO.ACCEPTED],
            rejected_count=counts[SuggestionStatusVO.REJECTED],
            pending_count=pending,
            finalized=finalized,
        )
