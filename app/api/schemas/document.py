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
4STATUS: Публичный document.status ограничен 4 значениями (draft/in_progress/
     awaiting_approval/ready). Техническое состояние анализа (pending/processing/
     failed/cancelled/completed) вынесено в отдельное поле analysis: AnalysisStateResponse.
     Хелпер resolve_document_public_status_and_analysis выполняет маппинг.
"""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.api.schemas.source import SourceBadge, SourceResponse

# ---------------------------------------------------------------------------
# Техническое состояние анализа (не путать с публичным статусом документа)
# ---------------------------------------------------------------------------


class AnalysisStateResponse(BaseModel):
    """Объект, описывающий состояние последнего analysis job.

    Отдаётся в поле ``analysis`` объектов DocumentResponse и DocumentListItem.
    Если анализ ни разу не запускался — поле равно null.

    Поля:
        job_id       — UUID последнего AnalysisJob (null при отсутствии job).
        state        — техническое состояние: pending | processing |
                       completed | failed | cancelled.
        error_code   — машинный код ошибки (только при failed).
        error_message — человекочитаемое описание (при failed / cancelled).
        can_retry    — True, если фронт может показать кнопку «Анализировать».
    """

    job_id: uuid.UUID | None = None
    state: Literal["pending", "processing", "completed", "failed", "cancelled"]
    error_code: str | None = None
    error_message: str | None = None
    can_retry: bool = True

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Хелпер маппинга (domain → API-контракт)
# ---------------------------------------------------------------------------


def resolve_document_public_status_and_analysis(
    doc_status: str,
    latest_job,
) -> tuple[str, AnalysisStateResponse | None]:
    """Преобразует внутреннее состояние документа + последний job → публичный контракт.

    Аргументы:
        doc_status  — строковое значение DocumentStatusVO (из ORM-модели).
        latest_job  — ORM-объект AnalysisJob или None.

    Возвращает:
        (public_status, analysis_state)
        public_status  — одно из: draft | in_progress | awaiting_approval | ready.
        analysis_state — AnalysisStateResponse или None.
    """
    if latest_job is None:
        return doc_status, None

    job_status = str(latest_job.status)

    if job_status == "failed":
        return "draft", AnalysisStateResponse(
            job_id=latest_job.id,
            state="failed",
            error_code=getattr(latest_job, "error_code", None) or "ANALYSIS_FAILED",
            error_message=getattr(latest_job, "error_message", None) or "Ошибка выполнения анализа",
            can_retry=True,
        )

    if job_status == "cancelled":
        return "draft", AnalysisStateResponse(
            job_id=latest_job.id,
            state="cancelled",
            error_code="ANALYSIS_CANCELLED",
            error_message="Анализ отменён пользователем",
            can_retry=True,
        )

    if job_status == "pending":
        return "in_progress", AnalysisStateResponse(
            job_id=latest_job.id,
            state="pending",
            can_retry=False,
        )

    if job_status in ("processing", "dispatched"):
        return "in_progress", AnalysisStateResponse(
            job_id=latest_job.id,
            state="processing",
            can_retry=False,
        )

    # success / partial_success / completed
    return doc_status, AnalysisStateResponse(
        job_id=latest_job.id,
        state="completed",
        can_retry=True,
    )


# ---------------------------------------------------------------------------
# Схемы ответов
# ---------------------------------------------------------------------------


class DocumentResponse(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    format: str
    size_bytes: int
    uploaded_at: datetime
    # Публичный статус: только 4 значения (техническое состояние → поле analysis)
    status: Literal["draft", "in_progress", "awaiting_approval", "ready"]
    current_analysis_job_id: uuid.UUID | None = None
    # P0-2: версия для If-Match / ETag
    review_version: int = 0
    # Техническое состояние последнего analysis job (null — анализ не запускался)
    analysis: AnalysisStateResponse | None = None
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
    sources: list[SourceResponse]

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
    # Публичный статус: только 4 значения
    status: Literal["draft", "in_progress", "awaiting_approval", "ready"]
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
    # Техническое состояние последнего analysis job
    analysis: AnalysisStateResponse | None = None


class DocumentListPage(BaseModel):
    items: list[DocumentListItem]
    total: int
    limit: int
    offset: int
