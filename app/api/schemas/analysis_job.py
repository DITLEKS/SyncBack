"""
Схемы для analysis-jobs API.

P0-7:  partial_success в ответе.
P0-9:  AnalysisJobCreateRequest с опциональным флагом force=True —
       повторный анализ документа в статусе READY требует force=True.
       Без флага — HTTP 409 с confirmation_required=True, чтобы фронт
       показал диалог подтверждения.
"""
import uuid
from datetime import datetime

from pydantic import BaseModel

from app.infrastructure.db.models.enums import AnalysisJobStatus


class AnalysisJobCreateRequest(BaseModel):
    """Tело POST /analysis-jobs.

    force=True обязателен, если документ в статусе READY.
    Опущен — 410 совместим, флаг просто игнорируется для не-READY документов.
    """

    force: bool = False


class AnalysisJobResponse(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    status: AnalysisJobStatus
    error_code: str | None
    error_message: str | None
    retry_count: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    partial_success: bool = False  # P0-7
    model_config = {"from_attributes": True}


class AnalysisJobConflictResponse(BaseModel):
    """Ответ 409 для повторного анализа ready-документа без force=True (#9).

    Фронт получает этот ответ и должен показать диалог
    "Документ уже Готов. Перезапустить анализ?"
    """

    detail: str
    confirmation_required: bool = True
