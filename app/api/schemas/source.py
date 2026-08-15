"""
Схемы источников. Загрузка файла-источника идёт отдельным multipart-эндпоинтом
(см. api/v1/routers/sources.py), поэтому здесь описаны только note/link.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class SourceCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    type: Literal["note", "link"]
    text_content: str | None = Field(default=None, max_length=200_000)
    url: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def check_payload_matches_type(self) -> "SourceCreateRequest":
        if self.type == "note" and not self.text_content:
            raise ValueError("text_content обязателен для источника типа note")
        if self.type == "link" and not self.url:
            raise ValueError("url обязателен для источника типа link")
        return self


class SourceResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    type: str
    uploaded_at: datetime

    model_config = {"from_attributes": True}
