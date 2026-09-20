"""
Бизнес-логика работы с правками: точечный accept/reject, bulk-accept,
finalize_review и сборка списка принятых изменений для экспорта.

ИСПРАВЛЕНО:
1. list_suggestions_for_document принимает limit/offset и возвращает (items, total).
2. bulk_accept() фильтр по status перенесён на уровень SQL.
3. get_accepted_changes() — аналогично.
4. get_suggestion_for_document больше НЕ требует статус AWAITING_APPROVAL:
   правки читаются из текущего analysis_job независимо от статуса документа.
   Это позволяет фронтенду отображать правки предыдущего раунда, пока идёт
   повторный анализ (переход №9). Решения по правкам (decide/bulk_accept) по-
   прежнему ограничены статусом AWAITING_APPROVAL.
5. decide(): добавлен явный guard на AWAITING_APPROVAL на уровне сервиса —
   защищает от вызова вне роутера без проверки статуса документа.
"""
import uuid

from app.domain.exceptions import (
    DocumentNotFoundError,
    InvalidDocumentStatusError,
    ReviewNotCompleteError,
    SuggestionAlreadyDecidedError,
    SuggestionNotFoundError,
)
from app.domain.interfaces.document_exporter import AppliedChange
from app.infrastructure.db.models.enums import DocumentStatus, SuggestionStatus
from app.infrastructure.db.models.suggestion import Suggestion
from app.infrastructure.db.repositories.document_repository import DocumentRepository
from app.infrastructure.db.repositories.suggestion_repository import SuggestionRepository


class SuggestionService:
    def __init__(self, suggestion_repository: SuggestionRepository, document_repository: DocumentRepository):
        self._suggestions = suggestion_repository
        self._documents = document_repository

    async def _get_document_or_raise(self, project_id: uuid.UUID, document_id: uuid.UUID):
        document = await self._documents.get_by_id(document_id)
        if document is None or document.project_id != project_id:
            raise DocumentNotFoundError(f"Документ {document_id} не найден в проекте {project_id}")
        return document

    async def list_suggestions_for_document(
        self, project_id: uuid.UUID, document_id: uuid.UUID, limit: int, offset: int
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
        self, project_id: uuid.UUID, document_id: uuid.UUID, suggestion_id: uuid.UUID
    ) -> Suggestion:
        """Вернуть правку по id.

        Проверка статуса документа намеренно убрана: правки доступны для чтения
        в любом статусе (нужно для отображения предыдущего раунда при re-анализе).
        Ограничение на изменение решения (accept/reject) сохраняется в decide() и
        bulk_accept() через явную проверку AWAITING_APPROVAL.
        """
        document = await self._get_document_or_raise(project_id, document_id)
        suggestion = await self._suggestions.get_by_id(suggestion_id)
        if suggestion is None or suggestion.analysis_job_id != document.current_analysis_job_id:
            raise SuggestionNotFoundError(f"Правка {suggestion_id} не найдена для документа {document_id}")
        return suggestion

    async def decide(
        self, project_id: uuid.UUID, document_id: uuid.UUID, suggestion: Suggestion, user_id: uuid.UUID, status: SuggestionStatus
    ) -> Suggestion:
        """Принять решение по правке (accept или reject).

        Guard на AWAITING_APPROVAL добавлен на уровне сервиса: решения по правкам
        разрешены только пока документ ожидает утверждения. Это делает сервис
        безопасным независимо от того, откуда он вызывается.
        """
        document = await self._get_document_or_raise(project_id, document_id)
        if document.status != DocumentStatus.AWAITING_APPROVAL:
            raise InvalidDocumentStatusError(
                "Решения по правкам доступны только в статусе 'awaiting_approval'"
            )
        updated = await self._suggestions.update_status(suggestion, status, user_id)
        if updated is None:
            raise SuggestionAlreadyDecidedError(f"Правка {suggestion.id} уже была обработана другим запросом")
        return updated

    async def bulk_accept(
        self, project_id: uuid.UUID, document_id: uuid.UUID, user_id: uuid.UUID
    ) -> list[Suggestion]:
        document = await self._get_document_or_raise(project_id, document_id)
        if document.status != DocumentStatus.AWAITING_APPROVAL:
            raise InvalidDocumentStatusError("Решения по правкам доступны только в статусе 'awaiting_approval'")
        if document.current_analysis_job_id is None:
            return []
        pending_ids = await self._suggestions.list_ids_by_analysis_job_and_status(
            document.current_analysis_job_id, SuggestionStatus.PENDING
        )
        return await self._suggestions.bulk_update_status(pending_ids, SuggestionStatus.ACCEPTED, user_id)

    async def finalize_review(self, project_id: uuid.UUID, document_id: uuid.UUID):
        """Перевести документ в READY (переход №8).

        Условие: документ в AWAITING_APPROVAL И по всем правкам текущего
        analysis_job принято решение (нет ни одной PENDING). Если пользователь
        отклонил все правки — это тоже валидный финал (→ READY).
        """
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
        return await self._documents.update_status(document, DocumentStatus.READY)

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
