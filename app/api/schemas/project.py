"""
Схемы проектов.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

# Допустимые цвета карточки проекта (hex без #, 6 символов).
# Фронт использует их для визуального различения карточек (#11).
PROJECT_COLORS = [
    "3B82F6",  # blue
    "8B5CF6",  # violet
    "10B981",  # emerald
    "F59E0B",  # amber
    "EF4444",  # red
    "EC4899",  # pink
    "14B8A6",  # teal
    "F97316",  # orange
]


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    # P0-#11: визуальные атрибуты карточки
    color: str | None = Field(
        default=None,
        max_length=6,
        description="Hex-цвет карточки без '#' (6 символов). Если не задан — выбирается автоматически.",
        examples=["3B82F6"],
    )
    icon: str | None = Field(
        default=None,
        max_length=64,
        description="Имя иконки (Lucide / emoji). Если не задан — используется иконка по умолчанию.",
        examples=["folder", "📘"],
    )


class ProjectUpdateRequest(BaseModel):
    """PATCH /projects/{id} — все поля опциональны (partial update)."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    # P0-#11
    color: str | None = Field(default=None, max_length=6)
    icon: str | None = Field(default=None, max_length=64)


class ProjectResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    owner_id: uuid.UUID
    created_at: datetime
    # P0-4: счётчики для карточки проекта
    document_count: int = 0
    source_count: int = 0
    # P0-#11: визуальные атрибуты карточки
    color: str | None = None
    icon: str | None = None

    model_config = {"from_attributes": True}
