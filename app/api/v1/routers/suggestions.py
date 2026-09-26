"""API для работы с правками документа."""

import logging
import uuid
from dataclasses import dataclass
from typing import List

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import TypeAdapter

from app.api.deps import get_allowed_project, get_current_user
from app.api.schemas.document import DocumentResponse
from app.api.schemas.pagination import Page
from app.api.schemas.review import ReviewSaveRequest, ReviewSaveResponse
from app.api.schemas.suggestion import BulkAcceptResponse, SuggestionResponse
from app.core.dependencies import get_audit_log_service, get_suggestion_service
from app.domain.exceptions import (
    DocumentNotFoundError,
    InvalidDocumentStatusError,
    OptimisticLockError,
    ReviewNotCompleteError,
    StaleSuggestionJobError,
    SuggestionAlreadyDecidedError,
    SuggestionNotFoundError,
)
from app.domain.services.audit_log_service import AuditLogService
from app.domain.services.suggestion_service import SuggestionService
from app.domain.value_objects import (
    AuditActionVO,
    PaginationParams,
    SuggestionStatusVO,
)
from app.infrastructure.db.models.project import Project
from app.infrastructure.db.models.user import User

logger = logging.getLogger("syncscribe.api.suggestions")
_suggestion_list_adapter: TypeAdapter[list[SuggestionResponse]] = TypeAdapter(
    list[SuggestionResponse]
)

# L-4: конкретный алиас, разрешённый при определении класса — FastAPI < 0.100
# корректно строит OpenAPI-схему без runtime-introspection generic alias.
PageSuggestionResponse = Page[SuggestionResponse]

router = APIRouter(
    prefix="/projects/{project_id}/documents/{document_id}/suggestions",
    tags=["suggestions"],
)


# ---------------------------------------------------------------------------
# M-5: DecisionContext dataclass вместо 7-аргументной сигнатуры
# ---------------------------------------------------------------------------

@dataclass
class DecisionContext:
    """Контекст принятия решения по одной правке.

    Заменяет 7 позиционных аргументов в _decide_suggestion.
    """
    action: str  # "accept" | "reject"
    document_id: uuid.UUID
    suggestion_id: uuid.UUID
    project: Project
    current_user: User
    suggestion_service: SuggestionService
    audit_log_service: AuditLogService


# ---------------------------------------------------------------------------
# Logging helpers
# ---------------------------------------------------------------------------

async def _safe_bulk_log(
    audit_log_service: AuditLogService,
    user_id: uuid.UUID,
    decisions: list[tuple[uuid.UUID, AuditActionVO]],
) -> None:
    try:
        await audit_log_service.bulk_log_suggestion_decisions(user_id, decisions)
    except Exception:
        logger.warning(
            "Не удалось записать bulk audit_log для решений по правкам",
            extra={"user_id": str(user_id)},
        )


async def _safe_single_log(
    audit_log_service: AuditLogService,
    user_id: uuid.UUID,
    suggestion_id: uuid.UUID,
    action: AuditActionVO,
) -> None:
    try:
        await audit_log_service.log_suggestion_decision(user_id, suggestion_id, action)
    except Exception:
        logger.warning(
            "Не удалось записать audit_log для решения по правке",
            extra={"suggestion_id": str(suggestion_id), "user_id": str(user_id)},
        )


def _parse_if_match(if_match: str | None) -> int | None:
    if if_match is None:
        return None
    try:
        return int(if_match.strip('"').strip())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Заголовок If-Match должен содержать целочисленный "
                f"review_version, получено: {if_match!r}"
            ),
        ) from None


async def _decide_suggestion(ctx: DecisionContext) -> SuggestionResponse:
    """M-5: единая точка принятия решения по правке через DecisionContext."""
    service_method = (
        ctx.suggestion_service.accept_suggestion
        if ctx.action == "accept"
        else ctx.suggestion_service.reject_suggestion
    )
    try:
        suggestion = await service_method(
            ctx.project.id, ctx.document_id, ctx.suggestion_id, ctx.current_user.id
        )
    except (DocumentNotFoundError, SuggestionNotFoundError, StaleSuggestionJobError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (InvalidDocumentStatusError, SuggestionAlreadyDecidedError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    audit_action = AuditActionVO.ACCEPT if ctx.action == "accept" else AuditActionVO.REJECT
    await _safe_single_log(
        ctx.audit_log_service, ctx.current_user.id, suggestion.id, audit_action
    )
    return SuggestionResponse.model_validate(suggestion)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("", response_model=PageSuggestionResponse)
async def list_suggestions(
    document_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    project: Project = Depends(get_allowed_project),
    suggestion_service: SuggestionService = Depends(get_suggestion_service),
) -> PageSuggestionResponse:
    """L-4: response_model использует конкретный алиас PageSuggestionResponse."""
    pagination = PaginationParams(limit=limit, offset=offset)
    try:
        suggestions, total = await suggestion_service.list_suggestions_for_document(
            project.id, document_id, pagination
        )
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return PageSuggestionResponse(
        items=_suggestion_list_adapter.validate_python(suggestions, from_attributes=True),
        total=total,
        limit=limit,
        offset=offset,
    )


@router.put("/review", response_model=ReviewSaveResponse)
async def review_save(
    document_id: uuid.UUID,
    payload: ReviewSaveRequest,
    project: Project = Depends(get_allowed_project),
    current_user: User = Depends(get_current_user),
    suggestion_service: SuggestionService = Depends(get_suggestion_service),
    audit_log_service: AuditLogService = Depends(get_audit_log_service),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> ReviewSaveResponse:
    client_version = _parse_if_match(if_match)
    review_version = client_version if client_version is not None else payload.review_version

    accepted_ids: list[uuid.UUID] = []
    rejected_ids: list[uuid.UUID] = []
    accepted_set: set[uuid.UUID] = set()

    for d in payload.decisions:
        if d.decision == "accepted":
            accepted_ids.append(d.suggestion_id)
            accepted_set.add(d.suggestion_id)
        else:
            rejected_ids.append(d.suggestion_id)

    audit_decisions: list[tuple[uuid.UUID, AuditActionVO]] = [
        (sid, AuditActionVO.ACCEPT if sid in accepted_set else AuditActionVO.REJECT)
        for sid in (*accepted_ids, *rejected_ids)
    ]

    try:
        result = await suggestion_service.atomic_review_save(
            project_id=project.id,
            document_id=document_id,
            user_id=current_user.id,
            review_version=review_version,
            accepted_ids=tuple(accepted_ids),
            rejected_ids=tuple(rejected_ids),
            finalize=payload.finalize,
        )
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except OptimisticLockError as exc:
        conflict_status = (
            status.HTTP_412_PRECONDITION_FAILED
            if client_version is not None
            else status.HTTP_409_CONFLICT
        )
        raise HTTPException(status_code=conflict_status, detail=str(exc)) from exc
    except SuggestionAlreadyDecidedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (InvalidDocumentStatusError, ReviewNotCompleteError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    await _safe_bulk_log(audit_log_service, current_user.id, audit_decisions)

    doc = result.document
    return ReviewSaveResponse(
        document_id=doc.id,
        document_status=doc.status.value,
        review_version=doc.review_version,
        accepted_count=result.accepted_count,
        rejected_count=result.rejected_count,
        pending_count=result.pending_count,
        finalized=result.finalized,
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
    return await _decide_suggestion(DecisionContext(
        action="accept",
        document_id=document_id,
        suggestion_id=suggestion_id,
        project=project,
        current_user=current_user,
        suggestion_service=suggestion_service,
        audit_log_service=audit_log_service,
    ))


@router.post("/{suggestion_id}/reject", response_model=SuggestionResponse)
async def reject_suggestion(
    document_id: uuid.UUID,
    suggestion_id: uuid.UUID,
    project: Project = Depends(get_allowed_project),
    current_user: User = Depends(get_current_user),
    suggestion_service: SuggestionService = Depends(get_suggestion_service),
    audit_log_service: AuditLogService = Depends(get_audit_log_service),
) -> SuggestionResponse:
    return await _decide_suggestion(DecisionContext(
        action="reject",
        document_id=document_id,
        suggestion_id=suggestion_id,
        project=project,
        current_user=current_user,
        suggestion_service=suggestion_service,
        audit_log_service=audit_log_service,
    ))


@router.post("/finalize", response_model=DocumentResponse)
async def finalize_review(
    document_id: uuid.UUID,
    project: Project = Depends(get_allowed_project),
    current_user: User = Depends(get_current_user),
    suggestion_service: SuggestionService = Depends(get_suggestion_service),
    audit_log_service: AuditLogService = Depends(get_audit_log_service),
) -> DocumentResponse:
    try:
        document = await suggestion_service.finalize_review(
            project_id=project.id,
            document_id=document_id,
            user_id=current_user.id,
        )
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (InvalidDocumentStatusError, ReviewNotCompleteError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    await _safe_single_log(
        audit_log_service,
        current_user.id,
        document.id,
        AuditActionVO.FINALIZE_REVIEW,
    )
    return DocumentResponse.model_validate(document)
