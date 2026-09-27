"""
Схемы источников.

P2: убраны note/text_content. Теперь два типа:
  - file: загружается через multipart /upload-эндпоинт (FileUpload).
  - url:  передаётся через SourceCreateRequest с type='url'.

P3: UrlConnector реализован, поэтому type='url' полностью функционален.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SourceCreateRequest(BaseModel):
    """Создание URL-источника. Файлы и текстовые заметки — через отдельные эндпоинты."""
    name: str = Field(min_length=1, max_length=255)
    type: Literal["url"]
    url: str = Field(min_length=1, max_length=2048)
    scope: Literal["project", "document"] = "project"


class NoteCreateRequest(BaseModel):
    """Создание текстовой заметки (P2: сохраняется как .txt в MinIO)."""
    name: str = Field(min_length=1, max_length=255)
    text_content: str = Field(min_length=1, max_length=200_000)
    scope: Literal["project", "document"] = "project"


class SourceResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    type: str
    scope: str
    uploaded_at: datetime

    model_config = {"from_attributes": True}
