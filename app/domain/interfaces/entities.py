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
"""
from __future__ import annotations

import uuid
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
    old_text: str | None
    new_text: str | None
    status: SuggestionStatusVO
