"""
Value-объекты доменного слоя — иммутабельные структуры без identity.

Правила:
  - Никаких импортов из infrastructure.*
  - Никаких импортов из FastAPI / SQLAlchemy
  - Все поля frozen=True (или StrEnum)
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum


# ---------------------------------------------------------------------------
# Перечисления
# ---------------------------------------------------------------------------

class DocumentStatusVO(StrEnum):
    """Доменное перечисление статусов документа."""
    DRAFT             = "draft"
    IN_PROGRESS       = "in_progress"
    AWAITING_APPROVAL = "awaiting_approval"
    READY             = "ready"


class SuggestionStatusVO(StrEnum):
    """Доменное перечисление статусов правки."""
    PENDING  = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class AnalysisJobStatusVO(StrEnum):
    """Доменное перечисление состояний задачи анализа."""
    PENDING         = "pending"
    PROCESSING      = "processing"
    SUCCESS         = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILED          = "failed"
    CANCELLED       = "cancelled"


# Обратная совместимость
AnalysisJobState = AnalysisJobStatusVO


class UserRoleVO(StrEnum):
    """Роль пользователя в системе."""
    ADMIN  = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"


class SourceTypeVO(StrEnum):
    """Тип источника истины."""
    FILE     = "file"
    TEXT     = "text"
    URL      = "url"
    NOTION   = "notion"
    GDOC     = "gdoc"


class SourceScopeVO(StrEnum):
    """Область видимости источника."""
    PROJECT  = "project"
    DOCUMENT = "document"


class DocumentFormatVO(StrEnum):
    """Формат исходного документа."""
    DOCX     = "docx"
    DOC      = "doc"
    TXT      = "txt"
    MARKDOWN = "markdown"


class AuditActionVO(StrEnum):
    """Тип действия в журнале аудита."""
    ACCEPT       = "accept"
    REJECT       = "reject"
    BULK_ACCEPT  = "bulk_accept"
    FINALIZE     = "finalize"
    REOPEN       = "reopen"


# ---------------------------------------------------------------------------
# Compound value objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PaginationParams:
    """Параметры постраничной навигации."""
    limit: int
    offset: int

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError(f"limit должен быть >= 1, получено {self.limit}")
        if self.offset < 0:
            raise ValueError(f"offset должен быть >= 0, получено {self.offset}")


@dataclass(frozen=True)
class SuggestionDecision:
    """Одно решение по правке: принять или отклонить."""
    suggestion_id: uuid.UUID
    accepted: bool


@dataclass(frozen=True)
class ReviewDecisions:
    """Набор решений одной сессии ревью с оптимистичной блокировкой.

    Содержит:
      - analysis_job_id  — к какому job относятся правки
      - decided_by       — кто принял решения
      - review_version   — ожидаемая версия для CAS-проверки
      - decisions        — набор SuggestionDecision
    """
    analysis_job_id: uuid.UUID
    decided_by: uuid.UUID
    review_version: int
    decisions: tuple[SuggestionDecision, ...]

    @property
    def accepted_ids(self) -> list[uuid.UUID]:
        return [d.suggestion_id for d in self.decisions if d.accepted]

    @property
    def rejected_ids(self) -> list[uuid.UUID]:
        return [d.suggestion_id for d in self.decisions if not d.accepted]


@dataclass(frozen=True)
class DocumentStats:
    """Агрегированная статистика документов проекта (для дашборда)."""
    total: int
    draft: int
    in_progress: int
    awaiting_approval: int
    ready: int
    failed: int
