"""Схемы источников.

Два типа источников: file (загружается через multipart /sources/file) и url.
Текстовые заметки (/sources/note) сохраняются как файл и возвращаются с type=file.
"""

import uuid
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator


class _ScopedSourceRequest(BaseModel):
    scope: Literal["project", "document"] = "project"
    document_id: uuid.UUID | None = Field(
        default=None, description="Документ, к которому прикрепляется источник (при scope=document)"
    )

    @model_validator(mode="after")
    def _document_scope_requires_document(self) -> Self:
        if self.scope == "document" and self.document_id is None:
            raise ValueError("Для источника со scope=document нужен document_id")
        return self


class SourceCreateRequest(_ScopedSourceRequest):
    name: str = Field(min_length=1, max_length=255)
    type: Literal["url"]
    url: str = Field(min_length=1, max_length=2048)


class NoteCreateRequest(_ScopedSourceRequest):
    name: str = Field(min_length=1, max_length=255)
    text_content: str = Field(min_length=1, max_length=200_000)


def _enum_value(v: object) -> str:
    if hasattr(v, "value"):
        return v.value  # type: ignore[return-value]
    return str(v)


class SourceResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    type: Literal["url", "file"]
    scope: Literal["project", "document"]
    created_at: datetime

    model_config = {"from_attributes": True}

    _normalise_type = field_validator("type", "scope", mode="before")(_enum_value)


class SourceBadge(BaseModel):
    """Минимум для бейджа источника на карточке документа."""

    id: uuid.UUID
    name: str
    type: Literal["url", "file"]

    model_config = {"from_attributes": True}

    _normalise_type = field_validator("type", mode="before")(_enum_value)
