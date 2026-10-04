"""
Схемы проектов.

I-4: GET /projects/{id}?include=documents,sources расширяет ProjectResponse
     дополнительными связанными данными без нового эндпоинта:
       include=sources   → project-scope источники (SourceResponse[])
       include=documents → документы проекта (DocumentListItem[] с SourceBadge)
     None = поле не было запрошено; [] = запрошено, данных нет.
     Лёгкий ответ для GET /projects (список карточек) не изменился.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.api.schemas.document import DocumentListItem
from app.api.schemas.source import SourceResponse
from app.domain.project_appearance import PROJECT_COLORS as DOMAIN_PROJECT_COLORS

# Палитра определена в домене; реэкспорт оставлен для существующих импортов схемы.
PROJECT_COLORS = list(DOMAIN_PROJECT_COLORS)


class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=2000)
    color: str | None = Field(
        default=None,
        max_length=7,
        description=(
            "Hex-цвет карточки из палитры PROJECT_COLORS, '#' необязателен. "
            "Если не задан — выбирается следующий цвет палитры."
        ),
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
    color: str | None = Field(default=None, max_length=7, description="Цвет из палитры")
    icon: str | None = Field(
        default=None, max_length=64, description="Пустая строка возвращает иконку по умолчанию"
    )


class ProjectResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    owner_id: uuid.UUID
    created_at: datetime
    document_count: int = 0
    # Только базовые (project-scope) источники; источники документов сюда не входят.
    source_count: int = 0
    color: str | None = None
    icon: str | None = None

    # I-4: расширенные данные через ?include=sources,documents.
    # Заполняются только при явном запросе — не влияют на GET /projects (список).
    # None  = не запрошено (поле отсутствует в ответе при сериализации exclude_none).
    # []    = запрошено, данных нет.
    sources: list[SourceResponse] | None = None
    documents: list[DocumentListItem] | None = None

    model_config = {"from_attributes": True}
