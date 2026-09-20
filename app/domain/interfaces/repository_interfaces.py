"""
ABC-интерфейсы репозиториев для domain-слоя.

Domain-сервисы зависят только от этих протоколов, а не от конкретных
SQLAlchemy-реализаций — соответствие принципу инверсии зависимостей (DDD).
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod

from app.infrastructure.db.models.analysis_job import AnalysisJob
from app.infrastructure.db.models.document import Document
from app.infrastructure.db.models.enums import AnalysisJobStatus, DocumentStatus, SuggestionStatus
from app.infrastructure.db.models.source import Source
from app.infrastructure.db.models.suggestion import Suggestion


class IDocumentRepository(ABC):
    @abstractmethod
    async def get_by_id(self, document_id: uuid.UUID) -> Document | None: ...

    @abstractmethod
    async def create(self, document: Document) -> Document: ...

    @abstractmethod
    async def save(self, document: Document) -> Document: ...

    @abstractmethod
    async def delete(self, document: Document) -> None: ...

    @abstractmethod
    async def update_status(self, document: Document, new_status: DocumentStatus) -> Document: ...

    @abstractmethod
    async def update_current_job(self, document: Document, job_id: uuid.UUID | None) -> Document: ...

    @abstractmethod
    async def list_by_project(self, project_id: uuid.UUID, limit: int, offset: int) -> list[Document]: ...

    @abstractmethod
    async def count_by_project(self, project_id: uuid.UUID) -> int: ...

    @abstractmethod
    async def list_all_for_user(
        self,
        user_id: uuid.UUID,
        limit: int,
        offset: int,
        status: DocumentStatus | None = None,
        search: str | None = None,
    ) -> tuple[list[Document], int]: ...

    @abstractmethod
    async def list_analyzable_for_project(self, project_id: uuid.UUID) -> list[Document]: ...

    @abstractmethod
    async def increment_review_version(self, document: Document) -> Document: ...

    @abstractmethod
    async def bump_review_version(self, document: Document) -> Document: ...


class ISuggestionRepository(ABC):
    @abstractmethod
    async def bulk_create(self, suggestions: list[Suggestion]) -> list[Suggestion]: ...

    @abstractmethod
    async def get_by_id(self, suggestion_id: uuid.UUID) -> Suggestion | None: ...

    @abstractmethod
    async def list_by_analysis_job(self, analysis_job_id: uuid.UUID, limit: int, offset: int) -> list[Suggestion]: ...

    @abstractmethod
    async def count_by_analysis_job(self, analysis_job_id: uuid.UUID) -> int: ...

    @abstractmethod
    async def count_by_analysis_job_and_status(self, analysis_job_id: uuid.UUID, status: SuggestionStatus) -> int: ...

    @abstractmethod
    async def list_by_analysis_job_and_status(self, analysis_job_id: uuid.UUID, status: SuggestionStatus) -> list[Suggestion]: ...

    @abstractmethod
    async def list_ids_by_analysis_job_and_status(self, analysis_job_id: uuid.UUID, status: SuggestionStatus) -> list[uuid.UUID]: ...

    @abstractmethod
    async def update_status(self, suggestion: Suggestion, status: SuggestionStatus, decided_by: uuid.UUID) -> Suggestion | None: ...

    @abstractmethod
    async def bulk_update_status(self, suggestion_ids: list[uuid.UUID], status: SuggestionStatus, decided_by: uuid.UUID) -> list[Suggestion]: ...


class IAnalysisJobRepository(ABC):
    @abstractmethod
    async def get_by_id(self, job_id: uuid.UUID) -> AnalysisJob | None: ...

    @abstractmethod
    async def get_by_idempotency_key(self, document_id: uuid.UUID, idempotency_key: str) -> AnalysisJob | None: ...

    @abstractmethod
    async def get_active_by_document_id(self, document_id: uuid.UUID) -> AnalysisJob | None: ...

    @abstractmethod
    async def create_for_document(self, job: AnalysisJob, document: Document) -> AnalysisJob: ...

    @abstractmethod
    async def mark_dispatched(self, job: AnalysisJob, document: Document, task_id: str) -> AnalysisJob: ...

    @abstractmethod
    async def mark_failed_queue_unavailable(self, job: AnalysisJob, document: Document, message: str | None) -> AnalysisJob: ...

    @abstractmethod
    async def cancel(self, job: AnalysisJob, document: Document) -> AnalysisJob: ...

    @abstractmethod
    async def mark_processing_if_active(self, job_id: uuid.UUID) -> bool: ...

    @abstractmethod
    async def update_status(self, job: AnalysisJob, status: AnalysisJobStatus) -> AnalysisJob: ...


class ISourceRepository(ABC):
    @abstractmethod
    async def create(self, source: Source) -> Source: ...

    @abstractmethod
    async def get_by_id(self, source_id: uuid.UUID) -> Source | None: ...

    @abstractmethod
    async def get_many_by_ids(self, source_ids: list[uuid.UUID]) -> list[Source]: ...

    @abstractmethod
    async def list_by_project(self, project_id: uuid.UUID, limit: int, offset: int) -> list[Source]: ...

    @abstractmethod
    async def count_by_project(self, project_id: uuid.UUID) -> int: ...

    @abstractmethod
    async def replace_document_sources(self, document_id: uuid.UUID, sources: list[Source]) -> list[Source]: ...
