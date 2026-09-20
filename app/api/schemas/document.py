"""
Схемы документов.

DocumentContentResponse/DocumentSectionResponse — распарсенный текст документа с позициями
секций для инлайн-отображения правок во фронтенде (сопоставляются с
s.section_ref у Suggestion).

ДОБАВЛЕНО (P0-4): DocumentListItem, DocumentListProject, SuggestionCounters —
  схемы для GET /api/v1/documents (глобальный список документов).
"""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.infrastructure.db.models.enums import DocumentStatus


class DocumentResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    format: str
    status: DocumentStatus
    current_analysis_job_id: uuid.UUID | None
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


# ---------------------------------------------------------------------------
# P0-4: глобальный список документов
# ---------------------------------------------------------------------------


class DocumentListProject(BaseModel):
    """Краткая информация о проекте в контексте элемента глобального списка."""

    id: uuid.UUID
    name: str


class SuggestionCounters(BaseModel):
    """Агрегированные счётчики правок для последнего анализа документа."""

    total: int = 0
    pending: int = 0
    accepted: int = 0
    rejected: int = 0


class DocumentListItem(BaseModel):
    """
    Элемент глобального списка документов.

    Используется в GET /api/v1/documents — отображает метаданные документа,
    проект, статус и счётчики правок без необходимости отдельных запросов
    к /projects/{id}/documents/{id}/suggestions.
    """

    id: uuid.UUID
    title: str
    format: str
    status: DocumentStatus
    current_analysis_job_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    project: DocumentListProject
    suggestions: SuggestionCounters


class DocumentListPage(BaseModel):
    """Страница глобального списка документов."""

    items: list[DocumentListItem]
    total: int
    limit: int
    offset: int
