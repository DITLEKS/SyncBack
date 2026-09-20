"""
Схемы проектов.

refactor(#14): поле color валидируется как строго 6-символьный hex через @field_validator.
"""

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

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

_HEX_RE = re.compile(r"^[0-9A-Fa-f]{6}$")


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

    @field_validator("color")
    @classmethod
    def validate_hex_color(cls, v: str | None) -> str | None:
        """Принимает только строки вида RRGGBB (без #). None — разрешён."""
        if v is not None and not _HEX_RE.match(v):
            raise ValueError(
                f"color должен быть 6-символьным hex без '#', например '3B82F6'. Получено: '{v}'"
            )
        return v


class ProjectUpdateRequest(BaseModel):
    """PATCH /projects/{id} — все поля опциональны (partial update)."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    # P0-#11
    color: str | None = Field(default=None, max_length=6)
    icon: str | None = Field(default=None, max_length=64)

    @field_validator("color")
    @classmethod
    def validate_hex_color(cls, v: str | None) -> str | None:
        if v is not None and not _HEX_RE.match(v):
            raise ValueError(
                f"color должен быть 6-символьным hex без '#', например '3B82F6'. Получено: '{v}'"
            )
        return v


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
