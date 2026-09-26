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
    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    AWAITING_APPROVAL = "awaiting_approval"
    READY = "ready"


class SuggestionStatusVO(StrEnum):
    """Доменное перечисление статусов правки."""
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class AnalysisJobStatusVO(StrEnum):
    """Доменное перечисление состояний задачи анализа.

    Зеркалит AnalysisJobStatus из инфраструктурного слоя, но не зависит от него.
    Конвертация: адаптер (репозиторий) читает/пишет ORM-поле через .value.
    """
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Обратная совместимость: старое имя всё ещё работает
AnalysisJobState = AnalysisJobStatusVO


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

    Заменяет пару (accepted_ids, rejected_ids) + review_version,
    которые раньше передавались как отдельные примитивы.
    """
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
