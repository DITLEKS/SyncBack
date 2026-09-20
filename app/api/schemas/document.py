"""
P0-2: добавлен review_version в DocumentResponse для оптимистической блокировки.
P0-6: добавлен UploadDocumentRequest — загрузка документа с явным project_id
      (нужно для маршрута «Мои документы» → кнопка «Загрузить документ»).
P0-#12: поле переименовано title→name в DocumentListItem, чтобы совпадало
        с DocumentResponse.name и ORM-атрибутом Document.name.
"""
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class DocumentResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    format: str
    size_bytes: int
    uploaded_at: datetime
    status: str
    current_analysis_job_id: uuid.UUID | None = None
    # P0-2: версия для If-Match / ETag
    review_version: int = 0
    model_config = {"from_attributes": True}


class DocumentContentResponse(BaseModel):
    plain_text: str
    sections: list["DocumentSectionResponse"]


class DocumentSectionResponse(BaseModel):
    ref: str
    start_offset: int
    end_offset: int


class DocumentDownloadResponse(BaseModel):
    download_url: str
    expires_in: int


class AttachSourcesRequest(BaseModel):
    source_ids: list[uuid.UUID]


# ── P0-6 ──────────────────────────────────────────────────────────────────────

class DocumentListProject(BaseModel):
    id: uuid.UUID
    name: str


class SuggestionCounters(BaseModel):
    total: int
    pending: int
    accepted: int
    rejected: int


class DocumentListItem(BaseModel):
    id: uuid.UUID
    # P0-#12: было title — переименовано в name для единообразия с
    # DocumentResponse.name и ORM-полем Document.name.
    name: str
    format: str
    status: str
    current_analysis_job_id: uuid.UUID | None = None
    created_at: datetime
    updated_at: datetime
    project: DocumentListProject
    suggestions: SuggestionCounters


class DocumentListPage(BaseModel):
    items: list[DocumentListItem]
    total: int
    limit: int
    offset: int
