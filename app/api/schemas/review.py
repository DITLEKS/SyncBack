"""
Схемы атомарного сохранения сессии ревью (P0-2).

PUT /projects/{project_id}/documents/{document_id}/review
- Принимает весь набор решений за один вызов.
- Поле review_version обязательно для оптимистической блокировки (If-Match).
- Допускает смешанные решения: часть accepted, часть rejected.
- Принятое решение может нести edited_text — текст правки, скорректированный
  пользователем; при экспорте он заменяет suggested_text.
- После успешного сохранения документ переходит в READY, если pending == 0.
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SuggestionDecision(BaseModel):
    """Одно решение в рамках сессии ревью."""

    suggestion_id: uuid.UUID
    decision: Literal["accepted", "rejected"]
    edited_text: str | None = Field(
        default=None,
        min_length=1,
        max_length=20_000,
        description=(
            "Скорректированный текст правки; допустим только вместе с decision='accepted'"
        ),
    )

    @model_validator(mode="after")
    def _edited_only_when_accepted(self) -> SuggestionDecision:
        if self.edited_text is not None and self.decision != "accepted":
            raise ValueError("edited_text допустим только для decision='accepted'")
        return self


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
