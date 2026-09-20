"""Editor aggregate endpoint: документ + контент + правки + текущий job."""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_allowed_project
from app.api.schemas.analysis_job import AnalysisJobResponse
from app.api.schemas.document import DocumentContentResponse, DocumentResponse, DocumentSectionResponse
from app.api.schemas.editor import EditorAggregateResponse
from app.api.schemas.pagination import Page
from app.api.schemas.suggestion import SuggestionResponse
from app.core.dependencies import get_document_service, get_suggestion_service
from app.domain.exceptions import DocumentNotFoundError
from app.domain.services.document_service import DocumentService
from app.domain.services.suggestion_service import SuggestionService
from app.infrastructure.db.models.project import Project

router = APIRouter(prefix="/projects/{project_id}/documents/{document_id}/editor", tags=["editor"])


@router.get("", response_model=EditorAggregateResponse)
async def get_editor_aggregate(
    document_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    project: Project = Depends(get_allowed_project),
    document_service: DocumentService = Depends(get_document_service),
    suggestion_service: SuggestionService = Depends(get_suggestion_service),
) -> EditorAggregateResponse:
    try:
        document = await document_service.get_document(project.id, document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    parsed = await document_service.get_document_content(document)
    suggestions, total = await suggestion_service.list_suggestions_for_document(project.id, document_id, limit=limit, offset=offset)

    content = DocumentContentResponse(
        plain_text=parsed.plain_text,
        sections=[DocumentSectionResponse(ref=s.ref, start_offset=s.start_offset, end_offset=s.end_offset) for s in parsed.sections],
    )

    current_job = None
    if getattr(document, "current_analysis_job", None) is not None:
        current_job = AnalysisJobResponse.model_validate(document.current_analysis_job)

    return EditorAggregateResponse(
        document=DocumentResponse.model_validate(document),
        content=content,
        suggestions=Page[SuggestionResponse](
            items=[SuggestionResponse.model_validate(s) for s in suggestions],
            total=total,
            limit=limit,
            offset=offset,
        ),
        current_analysis_job=current_job,
        review_version=getattr(document, "review_version", 1),
    )
