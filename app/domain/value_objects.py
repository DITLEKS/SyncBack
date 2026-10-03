"""
Value-объекты доменного слоя — иммутабельные структуры без identity.

Правила:
  - Никаких импортов из infrastructure.*
  - Никаких импортов из FastAPI / SQLAlchemy
  - Все поля frozen=True (или StrEnum)

4STATUS: DocumentStatusVO возвращён к 4 значениям (draft/in_progress/
    awaiting_approval/ready). ERROR и CANCELLED удалены из публичного
    статуса документа. Техническое состояние анализа теперь хранится
    исключительно в AnalysisJobStatusVO и отдаётся фронту через
    AnalysisStateResponse (см. app/api/schemas/document.py).
    Миграция 0020 переводит существующие строки error/cancelled → draft
    и удаляет эти значения из PostgreSQL enum document_status.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

# ---------------------------------------------------------------------------
# Перечисления
# ---------------------------------------------------------------------------


class DocumentStatusVO(StrEnum):
    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    AWAITING_APPROVAL = "awaiting_approval"
    READY = "ready"
    # ERROR и CANCELLED удалены из публичного статуса документа (4STATUS).
    # Техническое состояние анализа хранится в AnalysisJobStatusVO.


class SuggestionStatusVO(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class AnalysisJobStatusVO(StrEnum):
    PENDING = "pending"
    DISPATCHED = "dispatched"  # синхронизировано с enums.AnalysisJobStatus
    PROCESSING = "processing"
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Обратная совместимость
AnalysisJobState = AnalysisJobStatusVO


class UserRoleVO(StrEnum):
    ADMIN = "admin"
    USER = "user"


class SourceTypeVO(StrEnum):
    """Тип источника истины.

    После P2 (миграция 0018) в БД существуют только 'file' и 'url'.
    TEXT/NOTION/GDOC удалены — использование вызовет ошибку маппинга.
    """

    FILE = "file"
    URL = "url"


class SourceScopeVO(StrEnum):
    PROJECT = "project"
    DOCUMENT = "document"


class DocumentFormatVO(StrEnum):
    DOCX = "docx"
    DOC = "doc"
    TXT = "txt"
    MARKDOWN = "markdown"


class AuditActionVO(StrEnum):
    ACCEPT = "accept"
    REJECT = "reject"
    BULK_ACCEPT = "bulk_accept"
    BULK_REJECT = "bulk_reject"  # M-3 (issue #37)
    FINALIZE = "finalize"  # kept for backward compat
    FINALIZE_REVIEW = "finalize_review"  # S-3 (issue #37) — used by router
    REOPEN = "reopen"
    RESET = "reset"  # отмена ранее принятого/отклонённого решения
    DOWNLOAD = "download"  # синхронизировано с enums.AuditAction


# ---------------------------------------------------------------------------
# Compound value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PaginationParams:
    """Параметры постраничной навигации на основе OFFSET."""

    limit: int
    offset: int

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError(f"limit должен быть >= 1, получено {self.limit}")
        if self.offset < 0:
            raise ValueError(f"offset должен быть >= 0, получено {self.offset}")


@dataclass(frozen=True)
class KeysetPage:
    """Параметры курсорной (keyset) пагинации."""

    limit: int
    before_created_at: datetime | None = None
    before_id: uuid.UUID | None = None

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError(f"limit должен быть >= 1, получено {self.limit}")
        has_ts = self.before_created_at is not None
        has_id = self.before_id is not None
        if has_ts != has_id:
            raise ValueError("KeysetPage: before_created_at и before_id должны быть заданы вместе")


@dataclass(frozen=True)
class SuggestionDecision:
    suggestion_id: uuid.UUID
    status: SuggestionStatusVO
    user_id: uuid.UUID


@dataclass(frozen=True)
class ReviewDecisions:
    decisions: tuple[SuggestionDecision, ...] = field(default_factory=tuple)
    document_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None

    def __post_init__(self) -> None:
        if self.decisions and not self.user_id:
            raise ValueError("ReviewDecisions: user_id обязателен при наличии decisions")
