"""
Бизнес-логика работы с правками: точечный accept/reject, bulk-accept и сборка списка
принятых изменений для последующего экспорта документа.

ИСПРАВЛЕНО:
1. list_suggestions_for_document теперь принимает limit/offset и возвращает (items, total).
2. bulk_accept() раньше тянул из БД ВСЕ suggestions документа (любого статуса) и фильтровал
   PENDING в Python — лишний трафик и память на документах с большим количеством правок. Теперь
   фильтр по status перенесён на уровень SQL (list_ids_by_analysis_job_and_status).
3. get_accepted_changes() — аналогично: раньше тянул все suggestions и фильтровал ACCEPTED в
   Python, теперь фильтр по status в SQL (list_by_analysis_job_and_status).
"""
import uuid

from app.domain.exceptions import (
    DocumentNotFoundError,
    SuggestionAlreadyDecidedError,
    SuggestionNotFoundError,
)
from app.domain.interfaces.document_exporter import AppliedChange
from app.infrastructure.db.models.enums import SuggestionStatus
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
        items = await self._suggestions.list_by_analysis_job(document.current_analysis_job_id, limit=limit, offset=offset)
        total = await self._suggestions.count_by_analysis_job(document.current_analysis_job_id)
        return items, total

    async def get_suggestion_for_document(self, project_id: uuid.UUID, document_id: uuid.UUID, suggestion_id: uuid.UUID) -> Suggestion:
        document = await self._get_document_or_raise(project_id, document_id)
        suggestion = await self._suggestions.get_by_id(suggestion_id)
        if suggestion is None or suggestion.analysis_job_id != document.current_analysis_job_id:
            raise SuggestionNotFoundError(f"Правка {suggestion_id} не найдена для документа {document_id}")
        return suggestion

    async def decide(self, suggestion: Suggestion, user_id: uuid.UUID, status: SuggestionStatus) -> Suggestion:
        updated = await self._suggestions.update_status(suggestion, status, user_id)
        if updated is None:
            raise SuggestionAlreadyDecidedError(f"Правка {suggestion.id} уже была обработана другим запросом")
        return updated

    async def bulk_accept(self, project_id: uuid.UUID, document_id: uuid.UUID, user_id: uuid.UUID) -> list[Suggestion]:
        document = await self._get_document_or_raise(project_id, document_id)
        if document.current_analysis_job_id is None:
            return []
        pending_ids = await self._suggestions.list_ids_by_analysis_job_and_status(
            document.current_analysis_job_id, SuggestionStatus.PENDING
        )
        return await self._suggestions.bulk_update_status(pending_ids, SuggestionStatus.ACCEPTED, user_id)

    async def get_accepted_changes(self, document_id: uuid.UUID) -> list[AppliedChange]:
        document = await self._documents.get_by_id(document_id)
        if document is None or document.current_analysis_job_id is None:
            return []
        suggestions = await self._suggestions.list_by_analysis_job_and_status(
            document.current_analysis_job_id, SuggestionStatus.ACCEPTED
        )
        return [
            AppliedChange(section_ref=s.section_ref, change_type=s.change_type.value, old_text=s.old_text, new_text=s.new_text)
            for s in suggestions
        ]
