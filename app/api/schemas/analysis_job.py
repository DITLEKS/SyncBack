# P0-7: добавлен partial_success в ответ
import uuid
from datetime import datetime

from pydantic import BaseModel

from app.infrastructure.db.models.enums import AnalysisJobStatus


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
    partial_success: bool = False  # P0-7: True если job завершился частично
    model_config = {"from_attributes": True}
