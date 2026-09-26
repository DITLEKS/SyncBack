"""
Абстрактные репозитории — порты в терминологии DDD/Hexagonal.

Domain-слой зависит ТОЛЬКО от этих интерфейсов и domain value-objects.
Infrastructure-слой предоставляет конкретные адаптеры (SQLAlchemy-реализации).

Правило именования:
  - I<Name>Repository  — порт (этот файл)
  - <Name>Repository   — адаптер (в app/infrastructure/db/repositories/)

Правило импортов:
  - НИКАКИХ импортов из app.infrastructure.* при выполнении.
  - ORM-модели под TYPE_CHECKING — временная мера до замены domain entities.
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from app.domain.value_objects import (
    AnalysisJobStatusVO,
    DocumentStatusVO,
    DocumentStats,
    KeysetPage,
    PaginationParams,
    ReviewDecisions,
    SourceScopeVO,
    SourceTypeVO,
    SuggestionDecision,
    SuggestionStatusVO,
)

if TYPE_CHECKING:
    from app.infrastructure.db.models.analysis_job import AnalysisJob
    from app.infrastructure.db.models.audit_log import AuditLog
    from app.infrastructure.db.models.document import Document
    from app.infrastructure.db.models.project import Project
    from app.infrastructure.db.models.source import Source
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

    @abstractmethod
    async def update_exported_key(
        self, document: "Document", export_key: str
    ) -> None:
        """Сохранить storage_key экспортированного файла."""


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
        self,
        analysis_job_id: uuid.UUID,
        pagination: KeysetPage | PaginationParams,
    ) -> "list[Suggestion]": ...

    @abstractmethod
    async def list_with_total(
        self,
        analysis_job_id: uuid.UUID,
        pagination: PaginationParams,
    ) -> "tuple[list[Suggestion], int]":
        """Один SELECT с COUNT(*) OVER() — список + общий счётчик за один round-trip."""

    @abstractmethod
    async def count_by_analysis_job(self, analysis_job_id: uuid.UUID) -> int: ...

    @abstractmethod
    async def count_by_analysis_job_and_status(
        self, analysis_job_id: uuid.UUID, status: SuggestionStatusVO
    ) -> int: ...

    @abstractmethod
    async def list_by_analysis_job_and_status(
        self,
        analysis_job_id: uuid.UUID,
        status: SuggestionStatusVO,
    ) -> "list[Suggestion]":
        """
        Без LIMIT: источник для экспорта — возвращает все принятые правки.
        Пагинация для экспорта реализована на уровне DocumentExportService.
        """

    @abstractmethod
    async def list_ids_by_analysis_job_and_status(
        self, analysis_job_id: uuid.UUID, status: SuggestionStatusVO
    ) -> list[uuid.UUID]: ...

    @abstractmethod
    async def update_status(
        self,
        suggestion: "Suggestion",
        decision: SuggestionDecision,
    ) -> "Suggestion | None":
        """CAS-обновление статуса одной правки. None — уже обработана."""

    @abstractmethod
    async def bulk_update_status(
        self,
        decisions: ReviewDecisions,
    ) -> "list[Suggestion]":
        """Единый UPDATE для accepted + rejected через ReviewDecisions VO."""

    @abstractmethod
    async def bulk_accept_all(
        self,
        analysis_job_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> "list[Suggestion]":
        """UPDATE WHERE job_id=X AND status='pending' RETURNING * без SELECT UUID в память."""


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


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------

class IProjectRepository(ABC):
    @abstractmethod
    async def create(
        self,
        owner_id: uuid.UUID,
        name: str,
        description: str | None,
    ) -> "Project": ...

    @abstractmethod
    async def get_by_id(self, project_id: uuid.UUID) -> "Project | None": ...

    @abstractmethod
    async def list_all(self, limit: int, offset: int) -> "list[Project]": ...

    @abstractmethod
    async def list_by_owner(
        self, owner_id: uuid.UUID, limit: int, offset: int
    ) -> "list[Project]": ...

    @abstractmethod
    async def count_all(self) -> int: ...

    @abstractmethod
    async def count_by_owner(self, owner_id: uuid.UUID) -> int: ...

    @abstractmethod
    async def update(
        self,
        project: "Project",
        name: str | None,
        description: str | None,
    ) -> "Project": ...

    @abstractmethod
    async def delete(self, project: "Project") -> None: ...

    @abstractmethod
    async def collect_storage_keys(self, project_id: uuid.UUID) -> list[str]:
        """Вернуть все storage_key файлов, принадлежащих проекту."""


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------

class ISourceRepository(ABC):
    @abstractmethod
    async def create(
        self,
        project_id: uuid.UUID,
        name: str,
        source_type: SourceTypeVO,
        text_content: str | None = None,
        url: str | None = None,
        scope: SourceScopeVO = SourceScopeVO.PROJECT,
    ) -> "Source": ...

    @abstractmethod
    async def create_with_id(
        self,
        source_id: uuid.UUID,
        project_id: uuid.UUID,
        name: str,
        source_type: SourceTypeVO,
        storage_key: str,
        scope: SourceScopeVO = SourceScopeVO.PROJECT,
    ) -> "Source":
        """Создать файловый источник с заранее известным UUID (после upload)."""

    @abstractmethod
    async def get_many_by_ids(
        self, source_ids: list[uuid.UUID]
    ) -> "list[Source]": ...

    @abstractmethod
    async def list_by_project(
        self, project_id: uuid.UUID, limit: int, offset: int
    ) -> "list[Source]": ...

    @abstractmethod
    async def count_by_project(self, project_id: uuid.UUID) -> int: ...

    @abstractmethod
    async def replace_document_sources(
        self,
        document_id: uuid.UUID,
        sources: "list[Source]",
    ) -> "list[Source]":
        """Атомарная замена document-specific источников."""


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

class IDashboardRepository(ABC):
    @abstractmethod
    async def get_stats(self, user_id: uuid.UUID) -> dict:
        """Один агрегатный запрос: total / awaiting / ready (PERF-1)."""

    @abstractmethod
    async def get_activity_last_7_days(
        self, user_id: uuid.UUID
    ) -> list[dict]: ...

    @abstractmethod
    async def get_attention_documents(
        self, user_id: uuid.UUID, limit: int
    ) -> list[dict]: ...

    @abstractmethod
    async def get_recent_documents(
        self, user_id: uuid.UUID, limit: int
    ) -> list[dict]: ...

    @abstractmethod
    async def upsert_open(
        self, user_id: uuid.UUID, document_id: uuid.UUID
    ) -> None: ...
