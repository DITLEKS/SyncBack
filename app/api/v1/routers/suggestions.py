"""Просмотр и точечное подтверждение/отклонение правок, bulk-accept, finalize.

ИСПРАВЛЕНО:
- accept/reject ловят SuggestionAlreadyDecidedError → 409 Conflict.
- list_suggestions принимает limit/offset → Page.
- accept/reject явно проверяют статус документа AWAITING_APPROVAL перед решением
  (get_suggestion_for_document больше не делает эту проверку — см. suggestion_service.py).
"""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_allowed_project, get_current_user
from app.api.schemas.document import DocumentResponse
from app.api.schemas.pagination import Page
from app.api.schemas.suggestion import BulkAcceptResponse, SuggestionResponse
from app.core.dependencies import get_audit_log_service, get_suggestion_service
from app.domain.exceptions import (
    DocumentNotFoundError,
    InvalidDocumentStatusError,
    ReviewNotCompleteError,
    SuggestionAlreadyDecidedError,
    SuggestionNotFoundError,
)
from app.domain.services.audit_log_service import AuditLogService
from app.domain.services.suggestion_service import SuggestionService
from app.infrastructure.db.models.enums import AuditAction, DocumentStatus, SuggestionStatus
from app.infrastructure.db.models.project import Project
from app.infrastructure.db.models.user import User

logger = logging.getLogger("syncscribe.api.suggestions")

router = APIRouter(
    prefix="/projects/{project_id}/documents/{document_id}/suggestions",
    tags=["suggestions"],
)


async def _log_decision(
    audit_log_service: AuditLogService,
    user_id: uuid.UUID,
    suggestion_id: uuid.UUID,
    action: AuditAction,
) -> None:
    try:
        await audit_log_service.log_suggestion_decision(user_id, suggestion_id, action)
    except Exception:
        logger.warning(
            "Не удалось записать audit_log для решения по правке",
            extra={"suggestion_id": str(suggestion_id), "user_id": str(user_id)},
        )


def _require_awaiting_approval(document_id: uuid.UUID, document_status: DocumentStatus) -> None:
    """Бросить 409, если документ не в статусе AWAITING_APPROVAL."""
    if document_status != DocumentStatus.AWAITING_APPROVAL:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Решения по правкам доступны только в статусе 'awaiting_approval'",
        )


@router.get("", response_model=Page[SuggestionResponse])
async def list_suggestions(
    document_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    project: Project = Depends(get_allowed_project),
    suggestion_service: SuggestionService = Depends(get_suggestion_service),
) -> Page[SuggestionResponse]:
    try:
        suggestions, total = await suggestion_service.list_suggestions_for_document(
            project.id, document_id, limit=limit, offset=offset
        )
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Page[SuggestionResponse](
        items=[SuggestionResponse.model_validate(s) for s in suggestions],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/{suggestion_id}/accept", response_model=SuggestionResponse)
async def accept_suggestion(
    document_id: uuid.UUID,
    suggestion_id: uuid.UUID,
    project: Project = Depends(get_allowed_project),
    current_user: User = Depends(get_current_user),
    suggestion_service: SuggestionService = Depends(get_suggestion_service),
    audit_log_service: AuditLogService = Depends(get_audit_log_service),
) -> SuggestionResponse:
    try:
        suggestion = await suggestion_service.get_suggestion_for_document(
            project.id, document_id, suggestion_id
        )
    except (DocumentNotFoundError, SuggestionNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    # get_suggestion_for_document больше не проверяет статус — делаем это здесь явно
    from app.infrastructure.db.repositories.document_repository import DocumentRepository
    from app.core.dependencies import get_document_service  # noqa: F401 — только для аннотации
    # Получаем документ через отдельный запрос для проверки статуса
    # Используем project из Depends — он уже загружен
    # Статус документа нужен только для write-операций; читаем через suggestion.analysis_job_id
    # Примечание: в реальном приложении лучше передавать document в сервис явно.
    # Здесь используем факт, что get_suggestion_for_document уже сделал get_document —
    # дублируем минимальную проверку через отдельный helper.
    await _assert_document_awaiting_approval(suggestion_service, project.id, document_id)
    try:
        suggestion = await suggestion_service.decide(
            suggestion, current_user.id, SuggestionStatus.ACCEPTED
        )
    except SuggestionAlreadyDecidedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await _log_decision(audit_log_service, current_user.id, suggestion.id, AuditAction.ACCEPT)
    return SuggestionResponse.model_validate(suggestion)


@router.post("/{suggestion_id}/reject", response_model=SuggestionResponse)
async def reject_suggestion(
    document_id: uuid.UUID,
    suggestion_id: uuid.UUID,
    project: Project = Depends(get_allowed_project),
    current_user: User = Depends(get_current_user),
    suggestion_service: SuggestionService = Depends(get_suggestion_service),
    audit_log_service: AuditLogService = Depends(get_audit_log_service),
) -> SuggestionResponse:
    try:
        suggestion = await suggestion_service.get_suggestion_for_document(
            project.id, document_id, suggestion_id
        )
    except (DocumentNotFoundError, SuggestionNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await _assert_document_awaiting_approval(suggestion_service, project.id, document_id)
    try:
        suggestion = await suggestion_service.decide(
            suggestion, current_user.id, SuggestionStatus.REJECTED
        )
    except SuggestionAlreadyDecidedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await _log_decision(audit_log_service, current_user.id, suggestion.id, AuditAction.REJECT)
    return SuggestionResponse.model_validate(suggestion)


@router.post("/bulk-accept", response_model=BulkAcceptResponse)
async def bulk_accept_suggestions(
    document_id: uuid.UUID,
    project: Project = Depends(get_allowed_project),
    current_user: User = Depends(get_current_user),
    suggestion_service: SuggestionService = Depends(get_suggestion_service),
    audit_log_service: AuditLogService = Depends(get_audit_log_service),
) -> BulkAcceptResponse:
    try:
        accepted = await suggestion_service.bulk_accept(project.id, document_id, current_user.id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidDocumentStatusError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    for suggestion in accepted:
        await _log_decision(audit_log_service, current_user.id, suggestion.id, AuditAction.ACCEPT)
    return BulkAcceptResponse(accepted_count=len(accepted))


@router.post("/finalize", response_model=DocumentResponse)
async def finalize_review(
    document_id: uuid.UUID,
    project: Project = Depends(get_allowed_project),
    suggestion_service: SuggestionService = Depends(get_suggestion_service),
) -> DocumentResponse:
    """Перевести документ в READY (переход №8).

    Условие: статус AWAITING_APPROVAL и ни одной правки в PENDING.
    Если все правки отклонены — документ всё равно переходит в READY.
    """
    try:
        document = await suggestion_service.finalize_review(project.id, document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (InvalidDocumentStatusError, ReviewNotCompleteError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return DocumentResponse.model_validate(document)


async def _assert_document_awaiting_approval(
    suggestion_service: SuggestionService,
    project_id: uuid.UUID,
    document_id: uuid.UUID,
) -> None:
    """Вспомогательная проверка: документ должен быть в AWAITING_APPROVAL для write-операций."""
    doc = await suggestion_service._documents.get_by_id(document_id)
    if doc is None or doc.project_id != project_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Документ не найден")
    _require_awaiting_approval(document_id, doc.status)
