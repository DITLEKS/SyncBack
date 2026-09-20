"""
P0-2: добавлен review_version в DocumentResponse для оптимистической блокировки.
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
