"""
Схемы источников.

P2: убраны note/text_content. Теперь два типа:
  - file: загружается через multipart /upload-эндпоинт (FileUpload).
  - url:  передаётся через SourceCreateRequest с type='url'.

P3: UrlConnector реализован, поэтому type='url' полностью функционален.
I-1: добавлен SourceBadge — лёгкое представление источника для карточек
     DocumentListItem.sources (id + name + type, без дат и лишних полей).
FIX-3: SourceResponse.uploaded_at → created_at.
     Поле uploaded_at удалено из модели Source в R-4 (дублировал
     created_at). Схема не была обновлена → ValidationError на всех
     GET/POST /sources эндпоинтах.
FIX-9: SourceCreateRequest и NoteCreateRequest получили опциональное
     поле document_id: uuid.UUID | None = None.
     При scope='document' без document_id источник создавался без
     привязки к документу — M2M-запись в document_sources не вставлялась.
FIX-ревью: SourceBadge.type ужесточен с str до Literal["url", "file", "note"];
     добавлен field_validator для нормализации enum-значений (принимает как
     SourceTypeVO.URL так и строку "url").
WARN-1 (ревью): SourceBadge.type исправлен с Literal["url","file","note"] на
     Literal["url","file"]. SourceType enum (enums.py) содержит только FILE и URL
     после миграции 0018. Значение "note" никогда не возвращается из БД —
     мёртвый вариант в Literal вводил в заблуждение.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class SourceCreateRequest(BaseModel):
    """Creating a URL source.

    FIX-9: добавлено document_id — при scope='document' необходимо
    указать документ, к которому привязывается источник.
    Если scope='project', document_id игнорируется (должен быть None).
    """
    name: str = Field(min_length=1, max_length=255)
    type: Literal["url"]
    url: str = Field(min_length=1, max_length=2048)
    scope: Literal["project", "document"] = "project"
    document_id: uuid.UUID | None = None


class NoteCreateRequest(BaseModel):
    """Creating a text note (P2: сохраняется как .txt в MinIO).

    FIX-9: добавлено document_id — при scope='document' необходимо
    указать документ, к которому привязывается источник.
    """
    name: str = Field(min_length=1, max_length=255)
    text_content: str = Field(min_length=1, max_length=200_000)
    scope: Literal["project", "document"] = "project"
    document_id: uuid.UUID | None = None


class SourceResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    type: str
    scope: str
    # FIX-3: uploaded_at удалён из модели Source в R-4 — используем created_at.
    created_at: datetime

    model_config = {"from_attributes": True}


# I-1: лёгкое представление источника для карточек документов.
# Используется в DocumentListItem.sources — фронт получает иконки/бейджи
# источников без отдельного GET /sources.
class SourceBadge(BaseModel):
    """Minimal source representation for document card badges.

    Содержит только данные, нужные для бейджей: идентификатор, читаемое имя
    и тип (url | file) для выбора иконки на фронте.

    WARN-1: "note" удалён из Literal — SourceType enum (после миграции 0018)
    содержит только FILE и URL. Pydantic принимал "note" без ошибки, но
    такое значение никогда не приходило из БД.
    field_validator нормализует SourceTypeVO enum → строку.
    """
    id: uuid.UUID
    name: str
    type: Literal["url", "file"]

    model_config = {"from_attributes": True}

    @field_validator("type", mode="before")
    @classmethod
    def _normalise_type(cls, v: object) -> str:
        """Accept SourceTypeVO enum or plain string; return the string value."""
        if hasattr(v, "value"):
            return v.value  # type: ignore[return-value]
        return str(v)
