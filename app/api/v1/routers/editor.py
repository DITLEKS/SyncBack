"""
P0-8: Editor aggregate endpoint — один запрос вместо трёх.
P0-2: PUT /review — атомарное применение правок с оптимистичной блокировкой.

GET  /projects/{project_id}/documents/{document_id}/editor
PUT  /projects/{project_id}/documents/{document_id}/editor/review
"""
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.api.deps import get_allowed_project
from app.api.schemas.analysis_job import AnalysisJobResponse
from app.api.schemas.document import DocumentResponse
from app.api.schemas.editor import AtomicReviewRequest, AtomicReviewResponse
from app.api.schemas.pagination import Page
from app.api.schemas.suggestion import SuggestionResponse
from app.core.dependencies import (
    get_analysis_job_service,
    get_document_service,
    get_suggestion_service,
)
from app.domain.exceptions import DocumentNotFoundError, ReviewVersionConflictError
from app.domain.services.analysis_job_service import AnalysisJobService
from app.domain.services.document_service import DocumentService
from app.domain.services.suggestion_service import SuggestionService
from app.infrastructure.db.models.project import Project

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
    try:
        document = await document_service.get_document(project.id, document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    current_job = None
    if document.current_analysis_job_id is not None:
        try:
            current_job = await job_service.get_job(
                project.id, document_id, document.current_analysis_job_id
            )
        except Exception:  # noqa: BLE001
            current_job = None

    suggestions_list, suggestions_total = await suggestion_service.list_suggestions_for_document(
        project.id,
        document_id,
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


@router.put("/review", response_model=AtomicReviewResponse, status_code=status.HTTP_200_OK)
async def atomic_review(
    document_id: uuid.UUID,
    body: AtomicReviewRequest,
    project: Project = Depends(get_allowed_project),
    document_service: DocumentService = Depends(get_document_service),
    suggestion_service: SuggestionService = Depends(get_suggestion_service),
) -> AtomicReviewResponse:
    """P0-2: Атомарно применяет принятые/отклонённые правки.

    Использует оптимистичную блокировку через review_version:
    - если версия клиента не совпадает с текущей в БД → 409 Conflict
    - если совпадает → применяет все изменения в одной транзакции

    Это устраняет race condition при кнопке «Сохранить всё» во фронте.

    Ошибки:
    - 404 — документ не найден
    - 409 — review_version устарела (другой пользователь уже изменил документ)
    - 422 — один UUID присутствует в обоих списках одновременно
    """
    overlap = set(body.accepted_ids) & set(body.rejected_ids)
    if overlap:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Suggestion IDs не могут быть одновременно accepted и rejected: {overlap}",
        )

    try:
        document = await document_service.get_document(project.id, document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    if document.review_version != body.review_version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"review_version конфликт: ожидалось {body.review_version}, "
                f"текущая версия {document.review_version}. "
                "Перезагрузите документ и повторите."
            ),
        )

    try:
        new_version = await suggestion_service.apply_review(
            project_id=project.id,
            document_id=document_id,
            accepted_ids=body.accepted_ids,
            rejected_ids=body.rejected_ids,
            current_review_version=body.review_version,
        )
    except ReviewVersionConflictError as exc:
        # Сервисный слой поймал race condition на уровне БД
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return AtomicReviewResponse(
        review_version=new_version,
        accepted_count=len(body.accepted_ids),
        rejected_count=len(body.rejected_ids),
    )
