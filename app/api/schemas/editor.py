import uuid
from pydantic import BaseModel

from app.api.schemas.analysis_job import AnalysisJobResponse
from app.api.schemas.document import DocumentContentResponse, DocumentResponse
from app.api.schemas.pagination import Page
from app.api.schemas.suggestion import SuggestionResponse


class EditorAggregateResponse(BaseModel):
    document: DocumentResponse
    content: DocumentContentResponse
    suggestions: Page[SuggestionResponse]
    current_analysis_job: AnalysisJobResponse | None = None
    review_version: int
