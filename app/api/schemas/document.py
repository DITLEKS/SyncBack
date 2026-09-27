"""
P0-2: добавлен review_version в DocumentResponse для оптимистической блокировки.
P0-6: добавлен UploadDocumentRequest — загрузка документа с явным project_id
      (нужно для маршрута «Мои документы» → кнопка «Загрузить документ»).
P0-#12: поле переименовано title→name в DocumentListItem, чтобы совпадало
        с DocumentResponse.name и ORM-атрибутом Document.name.
UI-fix: добавлено поле size_bytes в DocumentListItem (карточка документа в UI).
R-1: добавлен AttachSourcesResponse — возвращается вместо голого DocumentResponse
     после POST /{id}/sources; содержит document + sources, устраняя лишний
     GET /sources на фронте.
I-1: DocumentListItem.sources: list[SourceBadge] — источники документа
     (document-scope) для отображения бейджей без доп-запроса.
     None = поле не было запрошено/загружено; [] = загружено, источников нет.
FIX-review-4: DocumentListItem.created_at → uploaded_at.
     DocumentResponse.uploaded_at — семантически «когда загружен файл».
     DocumentListItem использовал created_at (server_default=now), что
     расходилось с DocumentResponse и смущало фронт. ORM Document содержит
     оба поля; меняем на uploaded_at для консистентности API.
"""
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel

from app.api.schemas.source import SourceBadge

if TYPE_CHECKING:
    from app.api.schemas.source import SourceResponse


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


# R-1: ответ POST /{id}/sources — document + прикреплённые источники
class AttachSourcesResponse(BaseModel):
    """Ответ POST /documents/{id}/sources (R-1).

    Возвращает обновлённые метаданные документа и полный список
    прикреплённых к нему источников, чтобы фронт не делал дополнительный
    GET /sources после операции attach.
    """
    document: DocumentResponse
    sources: list["SourceResponse"]

    model_config = {"from_attributes": True}


# ── P0-6 ──────────────────────────────────────────────────────────────────────────

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
    name: str
    format: str
    size_bytes: int
    status: str
    current_analysis_job_id: uuid.UUID | None = None
    # FIX-review-4: uploaded_at вместо created_at — консистентно с
    # DocumentResponse.uploaded_at и семантикой «когда загружен файл».
    uploaded_at: datetime
    updated_at: datetime
    project: DocumentListProject
    suggestions: SuggestionCounters
    # I-1: бейджи источников документа (document-scope).
    # None  = поле не запрашивалось (например, в лёгком листинге).
    # []    = запрашивалось, источников нет.
    # [...]  = список бейджей для иконок на карточке.
    sources: list[SourceBadge] | None = None


class DocumentListPage(BaseModel):
    items: list[DocumentListItem]
    total: int
    limit: int
    offset: int
