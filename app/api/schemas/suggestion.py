import uuid
from datetime import datetime

from pydantic import BaseModel


class SuggestionResponse(BaseModel):
    id: uuid.UUID
    analysis_job_id: uuid.UUID
    section_ref: str
    change_type: str
    old_text: str | None
    new_text: str | None
    status: str
    source_reference: str | None
    confidence_score: float | None
    explanation: str | None
    decided_by: uuid.UUID | None
    decided_at: datetime | None
    created_at: datetime
    model_config = {"from_attributes": True}


class BulkAcceptResponse(BaseModel):
    accepted_count: int
