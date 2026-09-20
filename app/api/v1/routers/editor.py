"""
P0-8: Editor aggregate endpoint.

GET /projects/{project_id}/documents/{document_id}/editor

Возвращает агрегированный ответ для редактора:
- полные данные документа (включая review_version)
- последний активный analysis job (или None)
- список правок с пагинацией (первые 200)

Цель — один HTTP-запрос вместо трёх (document + job + suggestions)
при открытии экрана редактора во фронтенде.
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app.api.deps import get_allowed_project
from app.api.schemas.analysis_job import AnalysisJobResponse
from app.api.schemas.document import DocumentResponse
from app.api.schemas.pagination import Page
from app.api.schemas.suggestion import SuggestionResponse
from app.core.dependencies import get_analysis_job_service, get_document_service, get_suggestion_service
from app.domain.exceptions import DocumentNotFoundError
from app.domain.services.analysis_job_service import AnalysisJobService
from app.domain.services.document_service import DocumentService
from app.domain.services.suggestion_service import SuggestionService
from app.infrastructure.db.models.project import Project
from fastapi import HTTPException, status

router = APIRouter(
    prefix="/projects/{project_id}/documents/{document_id}/editor",
    tags=["editor"],
)


class EditorResponse(BaseModel):
    """Агрегированный ответ для экрана редактора."""
    document: DocumentResponse
    current_job: Optional[AnalysisJobResponse] = None
    suggestions: Page[SuggestionResponse]


@router.get("", response_model=EditorResponse)
async def get_editor_state(
    document_id: uuid.UUID,
    suggestions_limit: int = Query(200, ge=1, le=200, description="Макс. правок в ответе"),
    suggestions_offset: int = Query(0, ge=0),
    project: Project = Depends(get_allowed_project),
    document_service: DocumentService = Depends(get_document_service),
    job_service: AnalysisJobService = Depends(get_analysis_job_service),
    suggestion_service: SuggestionService = Depends(get_suggestion_service),
) -> EditorResponse:
    """Один запрос — всё состояние редактора.

    Ошибки:
    - 404 если документ не найден или не принадлежит проекту.
    """
    # 1. Документ
    try:
        document = await document_service.get_document(project.id, document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    # 2. Последний job (через current_analysis_job_id)
    current_job = None
    if document.current_analysis_job_id is not None:
        try:
            current_job = await job_service.get_job(
                project.id, document_id, document.current_analysis_job_id
            )
        except Exception:
            # Если job не найден (удалён / гонка) — не блокируем ответ
            current_job = None

    # 3. Правки с пагинацией
    suggestions_list, suggestions_total = await suggestion_service.list_suggestions_for_document(
        project.id, document_id,
        limit=suggestions_limit,
        offset=suggestions_offset,
    )

    return EditorResponse(
        document=DocumentResponse.model_validate(document),
        current_job=AnalysisJobResponse.model_validate(current_job) if current_job else None,
        suggestions=Page[SuggestionResponse](
            items=[SuggestionResponse.model_validate(s) for s in suggestions_list],
            total=suggestions_total,
            limit=suggestions_limit,
            offset=suggestions_offset,
        ),
    )
