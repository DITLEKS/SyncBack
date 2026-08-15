import uuid
from datetime import datetime
from pydantic import BaseModel


class AnalysisJobResponse(BaseModel):
    id: uuid.UUID
    document_id: uuid.UUID
    status: str
    error_code: str | None
    error_message: str | None
    retry_count: int
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    model_config = {"from_attributes": True}
