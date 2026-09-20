"""Агрегированный endpoint редактора документа (P0-8).

GET /projects/{project_id}/documents/{document_id}/editor

Возвращает одним запросом всё, что нужно экрану редактора фронтенда:
- метаданные документа + статус + review_version
- plain_text контент + позиции секций (если доступен)
- список правок текущего анализа
- агрегированные счётчики (pending/accepted/rejected/total)
- права текущего пользователя на действия с документом

Заменяет N+1 запросов:
  GET /documents/{id}  +  GET /documents/{id}/content
  + GET /suggestions  +  GET /analysis-jobs/{id}
"""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_allowed_project, get_current_user
from app.api.schemas.document import DocumentSectionResponse, SuggestionCounters
from app.api.schemas.editor import (
    EditorAggregateResponse,
    EditorContent,
    EditorDocumentMeta,
    EditorPermissions,
)
from app.api.schemas.suggestion import SuggestionResponse
from app.core.dependencies import get_document_service, get_suggestion_service
from app.domain.exceptions import DocumentNotFoundError
from app.domain.services.document_service import DocumentService
from app.domain.services.suggestion_service import SuggestionService
from app.infrastructure.db.models.enums import DocumentStatus
from app.infrastructure.db.models.project import Project
from app.infrastructure.db.models.user import User

logger = logging.getLogger("syncscribe.api.editor")

router = APIRouter(
    prefix="/projects/{project_id}/documents/{document_id}/editor",
    tags=["editor"],
)


@router.get("", response_model=EditorAggregateResponse)
async def get_editor_aggregate(
    document_id: uuid.UUID,
    project: Project = Depends(get_allowed_project),
    current_user: User = Depends(get_current_user),
    document_service: DocumentService = Depends(get_document_service),
    suggestion_service: SuggestionService = Depends(get_suggestion_service),
) -> EditorAggregateResponse:
    """Полный агрегат данных для экрана редактора.

    Выполняет параллельно:
    - загрузку метаданных документа
    - парсинг контента (graceful degradation: None при ошибке)
    - загрузку списка правок
    """
    # --- 1. Документ ---
    try:
        document = await document_service.get_document(project.id, document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    review_version = getattr(document, "review_version", 0) or 0

    meta = EditorDocumentMeta(
        id=document.id,
        title=document.name,
        format=document.format.value,
        status=document.status,
        current_analysis_job_id=document.current_analysis_job_id,
        created_at=document.uploaded_at,
        updated_at=document.uploaded_at,  # TODO: добавить updated_at в модель
        review_version=review_version,
    )

    # --- 2. Контент (graceful degradation) ---
    editor_content: EditorContent | None = None
    try:
        parsed = await document_service.get_document_content(document)
        editor_content = EditorContent(
            plain_text=parsed.plain_text,
            sections=[
                DocumentSectionResponse(
                    ref=s.ref,
                    start_offset=s.start_offset,
                    end_offset=s.end_offset,
                )
                for s in parsed.sections
            ],
        )
    except Exception:
        logger.warning(
            "Не удалось получить контент документа для редактора",
            extra={"document_id": str(document_id)},
        )

    # --- 3. Правки ---
    suggestions_raw, total = await suggestion_service.list_suggestions_for_document(
        project.id, document_id, limit=200, offset=0
    )
    suggestions = [SuggestionResponse.model_validate(s) for s in suggestions_raw]

    # --- 4. Счётчики ---
    pending = sum(1 for s in suggestions if s.status == "pending")
    accepted = sum(1 for s in suggestions if s.status == "accepted")
    rejected = sum(1 for s in suggestions if s.status == "rejected")
    counters = SuggestionCounters(
        total=total,
        pending=pending,
        accepted=accepted,
        rejected=rejected,
    )

    # --- 5. Права ---
    locked = document.status in (DocumentStatus.IN_PROGRESS,)
    permissions = EditorPermissions(
        can_analyze=document.status in (DocumentStatus.DRAFT, DocumentStatus.READY),
        can_review=document.status == DocumentStatus.AWAITING_APPROVAL,
        can_export=document.status == DocumentStatus.READY,
        can_delete=not locked,
    )

    return EditorAggregateResponse(
        document=meta,
        content=editor_content,
        suggestions=suggestions,
        counters=counters,
        permissions=permissions,
    )
