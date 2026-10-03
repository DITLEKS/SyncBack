"""
Схемы для analysis-jobs API.

P0-7:  partial_success в ответе.
P0-9:  AnalysisJobCreateRequest с опциональным флагом force=True —
       повторный анализ документа в статусе READY требует force=True.
       Без флага — HTTP 409 с confirmation_required=True, чтобы фронт
       показал диалог подтверждения.
FIX-review-3: status: AnalysisJobStatus (инфра-enum) → AnalysisJobStatusVO (domain).
    API-схемы не должны импортировать из infrastructure.*.
    AnalysisJobStatusVO и AnalysisJobStatus имеют идентичные строковые значения,
    поэтому from_attributes=True продолжает работать без изменений.
"""

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.domain.value_objects import AnalysisJobStatusVO


class AnalysisJobCreateRequest(BaseModel):
    """Тело POST /analysis-jobs.

    force=True обязателен, если документ в статусе READY.
    Опущен — флаг просто игнорируется для не-READY документов.
    """

    force: bool = False


class AnalysisJobResponse(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    # FIX-review-3: AnalysisJobStatus (infra) → AnalysisJobStatusVO (domain).
    # Строковые значения идентичны → from_attributes продолжает работать.
    status: AnalysisJobStatusVO
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
