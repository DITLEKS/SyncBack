"""
Схемы атомарного сохранения сессии ревью (P0-2).

PUT /projects/{project_id}/documents/{document_id}/review
- Принимает весь набор решений за один вызов.
- Поле review_version обязательно для оптимистической блокировки (If-Match).
- Допускает смешанные решения: часть accepted, часть rejected.
- После успешного сохранения документ переходит в READY, если pending == 0.
"""
from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field


class SuggestionDecision(BaseModel):
    """Одно решение в рамках сессии ревью."""

    suggestion_id: uuid.UUID
    decision: Literal["accepted", "rejected"]


class ReviewSaveRequest(BaseModel):
    """Тело запроса для атомарного сохранения ревью.

    review_version — текущая версия документа на клиенте; при несовпадении
    с серверной версией вернётся 409 (OptimisticLockError).
    Если decisions пустой — запрос применяет finalize без изменений статусов.
    """

    review_version: int = Field(
        ...,
        description="Версия ревью документа на клиенте (оптимистическая блокировка)",
    )
    decisions: list[SuggestionDecision] = Field(
        default_factory=list,
        description="Список решений accept/reject. Может быть пустым (finalize без изменений).",
    )
    finalize: bool = Field(
        default=True,
        description="Перевести документ в READY после применения решений, если pending == 0.",
    )


class ReviewSaveResponse(BaseModel):
    """Результат атомарного сохранения ревью."""

    document_id: uuid.UUID
    document_status: str
    review_version: int
    accepted_count: int
    rejected_count: int
    pending_count: int
    finalized: bool
