"""
Value-объекты доменного слоя — иммутабельные структуры без identity.

Правила:
  - Никаких импортов из infrastructure.*
  - Никаких импортов из FastAPI / SQLAlchemy
  - Все поля frozen=True (или StrEnum)
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
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
    FILE   = "file"
    TEXT   = "text"
    URL    = "url"
    NOTION = "notion"
    GDOC   = "gdoc"


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
    ACCEPT      = "accept"
    REJECT      = "reject"
    BULK_ACCEPT = "bulk_accept"
    FINALIZE    = "finalize"
    REOPEN      = "reopen"


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
    """Одно решение по правке: принять или отклонить.

    Используется в:
      - ISuggestionRepository.update_status()  — одиночное CAS-обновление
      - ReviewDecisions                         — батч-обновление
    """
    suggestion_id: uuid.UUID
    status: SuggestionStatusVO    # ACCEPTED | REJECTED (PENDING недопустим)
    decided_by: uuid.UUID

    def __post_init__(self) -> None:
        if self.status == SuggestionStatusVO.PENDING:
            raise ValueError(
                "SuggestionDecision.status не может быть PENDING; "
                "используйте ACCEPTED или REJECTED"
            )

    @property
    def is_accepted(self) -> bool:
        return self.status == SuggestionStatusVO.ACCEPTED


@dataclass(frozen=True)
class ReviewDecisions:
    """Набор решений одной сессии ревью с оптимистичной блокировкой.

    Поля:
      analysis_job_id — к какому job относятся правки
      decided_by      — кто принял решения (обязателен, PERF-2)
      review_version  — ожидаемая версия для CAS-проверки
      accepted_ids    — UUID правок к принятию
      rejected_ids    — UUID правок к отклонению

    Инварианты (проверяются при создании):
      - accepted ∩ rejected = ∅
      - нет дублей внутри каждого списка
    """
    analysis_job_id: uuid.UUID
    decided_by: uuid.UUID
    review_version: int
    accepted_ids: tuple[uuid.UUID, ...] = field(default_factory=tuple)
    rejected_ids: tuple[uuid.UUID, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if len(self.accepted_ids) != len(set(self.accepted_ids)):
            raise ValueError("accepted_ids содержат дубли")
        if len(self.rejected_ids) != len(set(self.rejected_ids)):
            raise ValueError("rejected_ids содержат дубли")
        overlap = set(self.accepted_ids) & set(self.rejected_ids)
        if overlap:
            raise ValueError(
                f"Правки одновременно в accepted и rejected: {overlap}"
            )

    @property
    def all_ids(self) -> list[uuid.UUID]:
        """Все затронутые UUID правок."""
        return [*self.accepted_ids, *self.rejected_ids]

    def to_decisions(self) -> list[SuggestionDecision]:
        """Развернуть в список SuggestionDecision для поштучной обработки."""
        decisions: list[SuggestionDecision] = []
        for sid in self.accepted_ids:
            decisions.append(
                SuggestionDecision(
                    suggestion_id=sid,
                    status=SuggestionStatusVO.ACCEPTED,
                    decided_by=self.decided_by,
                )
            )
        for sid in self.rejected_ids:
            decisions.append(
                SuggestionDecision(
                    suggestion_id=sid,
                    status=SuggestionStatusVO.REJECTED,
                    decided_by=self.decided_by,
                )
            )
        return decisions


@dataclass(frozen=True)
class DocumentStats:
    """Агрегированная статистика документов проекта (для дашборда)."""
    total: int
    draft: int
    in_progress: int
    awaiting_approval: int
    ready: int
    failed: int
