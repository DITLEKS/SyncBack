"""Просмотр и точечное подтверждение/отклонение правок, bulk-accept.

ИСПРАВЛЕНО: accept/reject теперь ловят SuggestionAlreadyDecidedError и возвращают
409 Conflict, если правка уже была решена другим параллельным запросом (см.
 SuggestionRepository.update_status).
"""
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.deps import get_allowed_project, get_current_user
from app.api.schemas.suggestion import BulkAcceptResponse, SuggestionResponse
from app.core.dependencies import get_audit_log_service, get_suggestion_service
from app.domain.exceptions import DocumentNotFoundError, SuggestionAlreadyDecidedError, SuggestionNotFoundError
from app.domain.services.audit_log_service import AuditLogService
from app.domain.services.suggestion_service import SuggestionService
from app.infrastructure.db.models.enums import AuditAction, SuggestionStatus
from app.infrastructure.db.models.project import Project
from app.infrastructure.db.models.user import User

logger = logging.getLogger("syncscribe.api.suggestions")

router = APIRouter(prefix="/projects/{project_id}/documents/{document_id}/suggestions", tags=["suggestions"])


async def _log_decision(audit_log_service: AuditLogService, user_id: uuid.UUID, suggestion_id: uuid.UUID, action: AuditAction) -> None:
    try:
        await audit_log_service.log_suggestion_decision(user_id, suggestion_id, action)
    except Exception:
        logger.warning("Не удалось записать audit_log для решения по правке", extra={"suggestion_id": str(suggestion_id), "user_id": str(user_id)})


@router.get("", response_model=list[SuggestionResponse])
async def list_suggestions(
    document_id: uuid.UUID,
    project: Project = Depends(get_allowed_project),
    suggestion_service: SuggestionService = Depends(get_suggestion_service),
) -> list[SuggestionResponse]:
    try:
        suggestions = await suggestion_service.list_suggestions_for_document(project.id, document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return [SuggestionResponse.model_validate(s) for s in suggestions]


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
        suggestion = await suggestion_service.get_suggestion_for_document(project.id, document_id, suggestion_id)
    except (DocumentNotFoundError, SuggestionNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    try:
        suggestion = await suggestion_service.decide(suggestion, current_user.id, SuggestionStatus.ACCEPTED)
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
        suggestion = await suggestion_service.get_suggestion_for_document(project.id, document_id, suggestion_id)
    except (DocumentNotFoundError, SuggestionNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    try:
        suggestion = await suggestion_service.decide(suggestion, current_user.id, SuggestionStatus.REJECTED)
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
    for suggestion in accepted:
        await _log_decision(audit_log_service, current_user.id, suggestion.id, AuditAction.ACCEPT)
    return BulkAcceptResponse(accepted_count=len(accepted))
