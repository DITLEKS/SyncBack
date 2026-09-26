"""
Абстрактные репозитории — порты в терминологии DDD/Hexagonal.

Domain-слой зависит ТОЛЬКО от этих интерфейсов и domain value-objects.
Infrastructure-слой предоставляет конкретные адаптеры (SQLAlchemy-реализации).

Правило именования:
  - I<Name>Repository  — порт (этот файл)
  - <Name>Repository   — адаптер (в app/infrastructure/db/repositories/)

Правило импортов:
  - НИКАКИХ импортов из app.infrastructure.*
  - Для возвращаемых типов использовать TYPE_CHECKING + строковые аннотации,
    пока ORM-модели не заменены domain-entities.
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from app.domain.value_objects import (
    AnalysisJobStatusVO,
    DocumentStatusVO,
    DocumentStats,
    SuggestionStatusVO,
)

if TYPE_CHECKING:
    # ORM-модели используются как возвращаемые типы только пока идёт
    # поэтапная миграция к domain entities. Импорт через TYPE_CHECKING
    # означает, что зависимость существует только для type-checker'а,
    # но не при выполнении — нарушения dependency rule нет.
    from app.infrastructure.db.models.analysis_job import AnalysisJob
    from app.infrastructure.db.models.audit_log import AuditLog
    from app.infrastructure.db.models.document import Document
    from app.infrastructure.db.models.suggestion import Suggestion


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------

class IDocumentRepository(ABC):
    @abstractmethod
    async def get_by_id(self, document_id: uuid.UUID) -> "Document | None": ...

    @abstractmethod
    async def list_for_project(
        self, project_id: uuid.UUID, limit: int, offset: int
    ) -> "list[Document]": ...

    @abstractmethod
    async def count_for_project(self, project_id: uuid.UUID) -> int: ...

    @abstractmethod
    async def list_analyzable_for_project(
        self, project_id: uuid.UUID
    ) -> "list[Document]": ...

    @abstractmethod
    async def update_status(
        self, document: "Document", status: DocumentStatusVO
    ) -> "Document": ...

    @abstractmethod
    async def compare_and_increment_review_version(
        self, document_id: uuid.UUID, expected_version: int
    ) -> "Document | None": ...

    @abstractmethod
    async def get_stats_for_project(
        self, project_id: uuid.UUID
    ) -> DocumentStats: ...


# ---------------------------------------------------------------------------
# Suggestion
# ---------------------------------------------------------------------------

class ISuggestionRepository(ABC):
    @abstractmethod
    async def bulk_create(
        self, suggestions: "list[Suggestion]"
    ) -> "list[Suggestion]": ...

    @abstractmethod
    async def get_by_id(
        self, suggestion_id: uuid.UUID
    ) -> "Suggestion | None": ...

    @abstractmethod
    async def list_by_analysis_job(
        self, analysis_job_id: uuid.UUID, limit: int, offset: int
    ) -> "list[Suggestion]": ...

    @abstractmethod
    async def count_by_analysis_job(self, analysis_job_id: uuid.UUID) -> int: ...

    @abstractmethod
    async def count_by_analysis_job_and_status(
        self, analysis_job_id: uuid.UUID, status: SuggestionStatusVO
    ) -> int: ...

    @abstractmethod
    async def list_by_analysis_job_and_status(
        self, analysis_job_id: uuid.UUID, status: SuggestionStatusVO
    ) -> "list[Suggestion]": ...

    @abstractmethod
    async def list_ids_by_analysis_job_and_status(
        self, analysis_job_id: uuid.UUID, status: SuggestionStatusVO
    ) -> list[uuid.UUID]: ...

    @abstractmethod
    async def update_status(
        self,
        suggestion: "Suggestion",
        status: SuggestionStatusVO,
        decided_by: uuid.UUID,
    ) -> "Suggestion | None": ...

    @abstractmethod
    async def bulk_update_status(
        self,
        analysis_job_id: uuid.UUID,
        suggestion_ids: list[uuid.UUID],
        status: SuggestionStatusVO,
        decided_by: uuid.UUID,
    ) -> "list[Suggestion]": ...


# ---------------------------------------------------------------------------
# AnalysisJob
# ---------------------------------------------------------------------------

class IAnalysisJobRepository(ABC):
    @abstractmethod
    async def get_by_id(self, job_id: uuid.UUID) -> "AnalysisJob | None": ...

    @abstractmethod
    async def get_by_idempotency_key(
        self, document_id: uuid.UUID, idempotency_key: str
    ) -> "AnalysisJob | None": ...

    @abstractmethod
    async def get_active_by_document_id(
        self, document_id: uuid.UUID
    ) -> "AnalysisJob | None": ...

    @abstractmethod
    async def create_for_document(
        self, job: "AnalysisJob", document: "Document"
    ) -> "AnalysisJob": ...

    @abstractmethod
    async def mark_dispatched(
        self, job: "AnalysisJob", document: "Document", task_id: str
    ) -> "AnalysisJob": ...

    @abstractmethod
    async def mark_failed_queue_unavailable(
        self, job: "AnalysisJob", document: "Document", message: str | None
    ) -> "AnalysisJob": ...

    @abstractmethod
    async def cancel(
        self, job: "AnalysisJob", document: "Document"
    ) -> "AnalysisJob": ...

    @abstractmethod
    async def mark_processing_if_active(self, job_id: uuid.UUID) -> bool: ...

    @abstractmethod
    async def update_status(
        self,
        job: "AnalysisJob",
        status: AnalysisJobStatusVO,
        error_code: str | None,
        error_message: str | None,
    ) -> "AnalysisJob": ...


# ---------------------------------------------------------------------------
# AuditLog
# ---------------------------------------------------------------------------

class IAuditLogRepository(ABC):
    @abstractmethod
    async def create(self, entry: "AuditLog") -> "AuditLog": ...

    @abstractmethod
    async def list_for_document(
        self,
        document_id: uuid.UUID,
        limit: int,
        offset: int,
    ) -> "list[AuditLog]": ...
