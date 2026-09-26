"""API для работы с правками документа."""

import logging
import uuid
from typing import Literal

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

router = APIRouter(
    prefix="/projects/{project_id}/documents/{document_id}/suggestions",
    tags=["suggestions"],
)


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


async def _decide_suggestion(
    action: Literal["accept", "reject"],
    document_id: uuid.UUID,
    suggestion_id: uuid.UUID,
    project: Project,
    current_user: User,
    suggestion_service: SuggestionService,
    audit_log_service: AuditLogService,
) -> SuggestionResponse:
    service_method = (
        suggestion_service.accept_suggestion
        if action == "accept"
        else suggestion_service.reject_suggestion
    )
    try:
        suggestion = await service_method(
            project.id, document_id, suggestion_id, current_user.id
        )
    except (DocumentNotFoundError, SuggestionNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (InvalidDocumentStatusError, SuggestionAlreadyDecidedError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    audit_action = AuditActionVO.ACCEPT if action == "accept" else AuditActionVO.REJECT
    await _safe_single_log(audit_log_service, current_user.id, suggestion.id, audit_action)
    return SuggestionResponse.model_validate(suggestion)


@router.get("", response_model=Page[SuggestionResponse])
async def list_suggestions(
    document_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    project: Project = Depends(get_allowed_project),
    suggestion_service: SuggestionService = Depends(get_suggestion_service),
) -> Page[SuggestionResponse]:
    pagination = PaginationParams(limit=limit, offset=offset)
    try:
        suggestions, total = await suggestion_service.list_suggestions_for_document(
            project.id, document_id, pagination
        )
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return Page[SuggestionResponse](
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

    # Разбиваем решения за один проход: O(N), O(1) поиск в accepted_set.
    accepted_ids: list[uuid.UUID] = []
    rejected_ids: list[uuid.UUID] = []
    accepted_set: set[uuid.UUID] = set()

    for d in payload.decisions:
        if d.decision == "accepted":
            accepted_ids.append(d.suggestion_id)
            accepted_set.add(d.suggestion_id)
        else:
            rejected_ids.append(d.suggestion_id)

    # Audit-список строится в O(N) без повторного обхода
    audit_decisions: list[tuple[uuid.UUID, AuditActionVO]] = [
        (sid, AuditActionVO.ACCEPT if sid in accepted_set else AuditActionVO.REJECT)
        for sid in (*accepted_ids, *rejected_ids)
    ]

    try:
        # Роутер передаёт плоские параметры —
        # сервис сам создаёт ReviewDecisions после разрешения job_id (M-6).
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
    review_version_out = getattr(doc, "review_version", 0) or 0
    return ReviewSaveResponse(
        document_id=doc.id,
        document_status=doc.status.value,
        review_version=review_version_out,
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
    return await _decide_suggestion(
        "accept",
        document_id,
        suggestion_id,
        project,
        current_user,
        suggestion_service,
        audit_log_service,
    )


@router.post("/{suggestion_id}/reject", response_model=SuggestionResponse)
async def reject_suggestion(
    document_id: uuid.UUID,
    suggestion_id: uuid.UUID,
    project: Project = Depends(get_allowed_project),
    current_user: User = Depends(get_current_user),
    suggestion_service: SuggestionService = Depends(get_suggestion_service),
    audit_log_service: AuditLogService = Depends(get_audit_log_service),
) -> SuggestionResponse:
    return await _decide_suggestion(
        "reject",
        document_id,
        suggestion_id,
        project,
        current_user,
        suggestion_service,
        audit_log_service,
    )
