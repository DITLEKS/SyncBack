"""
Абстрактные репозитории — порты в терминологии DDD/Hexagonal.

Domain-слой зависит ТОЛЬКО от этих интерфейсов и domain value-objects.
Infrastructure-слой предоставляет конкретные адаптеры (SQLAlchemy-реализации).

Правило именования:
  - I<Name>Repository  — порт (этот файл)
  - <Name>Repository   — адаптер (в app/infrastructure/db/repositories/)

Правило импортов:
  - Document и Suggestion — через DocumentProtocol/SuggestionProtocol (не ORM).
  - AnalysisJob и AuditLog — document/entry через Protocol, возвращаемые значения под TYPE_CHECKING.
  - Project, Source, User — всё ещё под TYPE_CHECKING.
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from app.domain.interfaces.entities import DocumentProtocol, SuggestionProtocol
from app.domain.value_objects import (
    AnalysisJobStatusVO,
    DocumentStatusVO,
    KeysetPage,
    PaginationParams,
    ReviewDecisions,
    SourceScopeVO,
    SourceTypeVO,
    SuggestionDecision,
    SuggestionStatusVO,
    UserRoleVO,
)

if TYPE_CHECKING:
    from app.infrastructure.db.models.analysis_job import AnalysisJob
    from app.infrastructure.db.models.audit_log import AuditLog
    from app.infrastructure.db.models.document import Document
    from app.infrastructure.db.models.enums import DocumentFormat
    from app.infrastructure.db.models.project import Project
    from app.infrastructure.db.models.source import Source
    from app.infrastructure.db.models.user import User


# ---------------------------------------------------------------------------
# Document
# ---------------------------------------------------------------------------

class IDocumentRepository(ABC):

    @abstractmethod
    async def create(
        self,
        *,
        id: uuid.UUID,
        project_id: uuid.UUID,
        title: str,
        format: "DocumentFormat",
        storage_key: str,
    ) -> "Document": ...

    @abstractmethod
    async def get_by_id(self, document_id: uuid.UUID) -> DocumentProtocol | None: ...

    @abstractmethod
    async def list_for_project(
        self,
        project_id: uuid.UUID,
        pagination: KeysetPage | PaginationParams,
    ) -> list[DocumentProtocol]: ...

    @abstractmethod
    async def count_for_project(self, project_id: uuid.UUID) -> int:
        """
        M-NEW-2: отдельный запрос подсчёта является ценой. Связка list_for_project + count_for_project
        даёт два round-trip. Если в будущем потребуется оптимизация — добавить
        list_for_project_with_total() с COUNT(*) OVER() (window function).
        """
        ...

    @abstractmethod
    async def list_analyzable_for_project(
        self, project_id: uuid.UUID
    ) -> list[DocumentProtocol]: ...

    @abstractmethod
    async def update_status(
        self, document: DocumentProtocol, status: DocumentStatusVO
    ) -> DocumentProtocol: ...

    @abstractmethod
    async def compare_and_increment_review_version(
        self, document_id: uuid.UUID, expected_version: int
    ) -> DocumentProtocol | None: ...

    @abstractmethod
    async def get_stats_for_project(
        self, project_id: uuid.UUID
    ) -> dict[str, int]: ...

    @abstractmethod
    async def update_exported_key(
        self, document: DocumentProtocol, export_key: str
    ) -> None: ...

    @abstractmethod
    async def list_all_for_user(
        self,
        user_id: uuid.UUID,
        limit: int,
        offset: int,
        status: DocumentStatusVO | None = None,
        search: str | None = None,
        sort_by: str = "updated_at",
        sort_dir: str = "desc",
    ) -> tuple[list[DocumentProtocol], int]: ...

    @abstractmethod
    async def delete(self, document: DocumentProtocol) -> None: ...


# ---------------------------------------------------------------------------
# Suggestion
# ---------------------------------------------------------------------------

class ISuggestionRepository(ABC):
    @abstractmethod
    async def bulk_create(
        self, suggestions: list[SuggestionProtocol]
    ) -> list[SuggestionProtocol]: ...

    @abstractmethod
    async def get_by_id(
        self, suggestion_id: uuid.UUID
    ) -> SuggestionProtocol | None: ...

    @abstractmethod
    async def list_by_analysis_job(
        self,
        analysis_job_id: uuid.UUID,
        pagination: KeysetPage | PaginationParams,
    ) -> list[SuggestionProtocol]: ...

    @abstractmethod
    async def list_with_total(
        self,
        analysis_job_id: uuid.UUID,
        pagination: PaginationParams,
    ) -> tuple[list[SuggestionProtocol], int]: ...

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
    ) -> list[SuggestionProtocol]: ...

    @abstractmethod
    async def list_ids_by_analysis_job_and_status(
        self, analysis_job_id: uuid.UUID, status: SuggestionStatusVO
    ) -> list[uuid.UUID]: ...

    @abstractmethod
    async def update_status(
        self,
        suggestion: SuggestionProtocol,
        decision: SuggestionDecision,
    ) -> SuggestionProtocol | None: ...

    @abstractmethod
    async def bulk_update_status(
        self,
        decisions: ReviewDecisions,
    ) -> list[SuggestionProtocol]: ...

    @abstractmethod
    async def bulk_accept_all(
        self,
        analysis_job_id: uuid.UUID,
        user_id: uuid.UUID,
    ) -> list[SuggestionProtocol]: ...


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
    async def list_by_document(
        self,
        document_id: uuid.UUID,
        pagination: PaginationParams | KeysetPage,
    ) -> list["AnalysisJob"]:
        """
        MED: История задач анализа по документу — необходима для эндпоинта GET /documents/{id}/jobs.
        Сортировка по created_at DESC.
        """
        ...

    @abstractmethod
    async def create_for_document(
        self,
        document: DocumentProtocol,
        *,
        job_id: uuid.UUID | None = None,
        status: AnalysisJobStatusVO = AnalysisJobStatusVO.PENDING,
        idempotency_key: str | None = None,
    ) -> "AnalysisJob": ...

    @abstractmethod
    async def mark_dispatched(
        self,
        job: "AnalysisJob",
        task_id: str,
    ) -> "tuple[AnalysisJob, DocumentStatusVO | None]": ...

    @abstractmethod
    async def mark_failed_queue_unavailable(
        self,
        job: "AnalysisJob",
        message: str | None,
    ) -> "tuple[AnalysisJob, DocumentStatusVO]": ...

    @abstractmethod
    async def cancel(
        self,
        job: "AnalysisJob",
    ) -> "tuple[AnalysisJob, DocumentStatusVO]": ...

    @abstractmethod
    async def mark_processing_if_active(self, job_id: uuid.UUID) -> bool: ...

    @abstractmethod
    async def update_status(
        self,
        job: "AnalysisJob",
        status: AnalysisJobStatusVO,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> "AnalysisJob": ...


# ---------------------------------------------------------------------------
# AuditLog
# ---------------------------------------------------------------------------

class IAuditLogRepository(ABC):
    @abstractmethod
    async def create(
        self,
        *,
        document_id: uuid.UUID,
        user_id: uuid.UUID | None,
        action: str,
        details: Any | None = None,
    ) -> "AuditLog":
        """
        M-NEW-3: фабричный метод. Сервис передаёт параметры, репозиторий строит AuditLog-объект.
        Сохраняет паттерн фабричных методов, введённый для Document и AnalysisJob.
        """
        ...

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
    async def collect_storage_keys(self, project_id: uuid.UUID) -> list[str]: ...


# ---------------------------------------------------------------------------
# Source
# ---------------------------------------------------------------------------

class ISourceRepository(ABC):
    @abstractmethod
    async def get_by_id(self, source_id: uuid.UUID) -> "Source | None": ...

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
    ) -> "Source": ...

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
    ) -> "list[Source]": ...

    @abstractmethod
    async def update(
        self,
        source: "Source",
        name: str | None = None,
        text_content: str | None = None,
        url: str | None = None,
    ) -> "Source":
        """
        HIGH: Изменить метаданные источника. Передавать только значения, которые необходимо изменить;
        None — поле остаётся неизменным. storage_key изменяется в инфра-слое при замене файла.
        """
        ...

    @abstractmethod
    async def delete(self, source: "Source") -> None:
        """
        HIGH: Удалить источник. Инфра-слой обязан удалить связанные файлы из MinIO
        бест-эффорт до удаления записи из БД.
        """
        ...


# ---------------------------------------------------------------------------
# User
# ---------------------------------------------------------------------------

class IUserRepository(ABC):
    """
    HIGH: Порт для репозитория пользователей.

    IUnitOfWork.users теперь ссылается на этот интерфейс,
    а не на конкретный UserRepository из infrastructure.
    """

    @abstractmethod
    async def get_by_id(self, user_id: uuid.UUID) -> "User | None": ...

    @abstractmethod
    async def get_by_email(self, email: str) -> "User | None": ...

    @abstractmethod
    async def create(
        self,
        email: str,
        hashed_password: str,
        role: UserRoleVO = UserRoleVO.EDITOR,
    ) -> "User": ...

    @abstractmethod
    async def update_role(
        self, user: "User", role: UserRoleVO
    ) -> "User": ...

    @abstractmethod
    async def list_all(self, limit: int, offset: int) -> "list[User]": ...

    @abstractmethod
    async def count_all(self) -> int: ...


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

class IDashboardRepository(ABC):
    @abstractmethod
    async def get_stats(self, user_id: uuid.UUID) -> dict: ...

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
