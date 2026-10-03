"""
Доменные Protocol-интерфейсы для сущностей.

Зачем:
  Domain-сервисы должны работать с абстракциями (портами), а не с ORM-моделями.
  Этот файл определяет минимальные Protocol-контракты, которыми
  должны удовлетворять объекты, передаваемые в сервисы.

Правило:
  - В Protocol входят только атрибуты/методы, которые domain-сервис реально читает.
  - Не нужно дублировать весь ORM-контракт — только минимальную поверхность.
  - SQLAlchemy-модели удовлетворяют Protocol-ам структурно — никаких
    изменений в конкретных реализациях не требуется.
  - @runtime_checkable позволяет использовать isinstance() в тестах.

C-4 (issue #37): добавлены decided_by и decided_at в SuggestionProtocol —
  нужны для SuggestionResponse.model_validate(suggestion, from_attributes=True).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Protocol, runtime_checkable

from app.domain.value_objects import DocumentStatusVO, SuggestionStatusVO


@runtime_checkable
class DocumentProtocol(Protocol):
    """Минимальный контракт документа, нужный domain-сервисам.

    Удовлетворяется любым объектом, у которого есть эти атрибуты —
    в том числе SQLAlchemy Document и test fakes.
    """

    id: uuid.UUID
    project_id: uuid.UUID
    status: DocumentStatusVO
    current_analysis_job_id: uuid.UUID | None
    review_version: int


@runtime_checkable
class SuggestionProtocol(Protocol):
    """Минимальный контракт правки, нужный domain-сервисам.

    Покрывает атрибуты, читаемые в SuggestionService
    и DocumentExportService.
    """

    id: uuid.UUID
    analysis_job_id: uuid.UUID
    section_ref: str
    change_type: object  # SuggestionChangeType enum — доступен через .value
    original_text: str | None
    suggested_text: str | None
    status: SuggestionStatusVO
    # C-4: required by SuggestionResponse serialization
    decided_by: uuid.UUID | None
    decided_at: datetime | None


@runtime_checkable
class UserProtocol(Protocol):
    """Минимальный контракт пользователя, нужный AuthService.

    REVIEW-7: добавлен, чтобы AuthService не импортировал ORM-модель User
    из app.infrastructure.db.models.user.

    role возвращается как str (.value enum'а или plain string) —
    AuthService передаёт его в JWTHandler.create_access_token(role=...).
    """

    id: uuid.UUID
    email: str
    password_hash: str
    role: str
