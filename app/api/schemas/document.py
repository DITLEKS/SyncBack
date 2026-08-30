"""
Схемы документов.

DocumentContentResponse/DocumentSectionResponse — распарсенный текст документа с позициями
секций для инлайн-отображения правок во фронтенде (сопоставляются с
s.section_ref у Suggestion).
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class DocumentResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    format: str
    status: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class DocumentDownloadResponse(BaseModel):
    download_url: str
    expires_in: int


class AttachSourcesRequest(BaseModel):
    source_ids: list[uuid.UUID] = Field(min_length=1)


class DocumentSectionResponse(BaseModel):
    ref: str
    start_offset: int
    end_offset: int


class DocumentContentResponse(BaseModel):
    plain_text: str
    sections: list[DocumentSectionResponse]
