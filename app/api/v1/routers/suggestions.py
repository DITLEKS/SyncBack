"""П0-#13: bulk_accept возвращает BulkAcceptResult — роутер заполняет
       document_status и review_version без лишнего GET-запроса.

ОПТИМИЗАЦИЯ (код-ревью):
- #3  N+1 audit_log заменён батчевым bulk_log_suggestion_decisions во всех
      местах: atomic_review_save, bulk_accept, accept, reject.
- #5  TypeAdapter для пакетной валидации в list_suggestions.
- #10 accept_suggestion / reject_suggestion объединены через _decide_suggestion.
"""
import logging
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
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
    StaleReviewVersionError,
    SuggestionAlreadyDecidedError,
    SuggestionNotFoundError,
)
from app.domain.services.audit_log_service import AuditLogService
from app.domain.services.suggestion_service import SuggestionService
from app.infrastructure.db.models.enums import AuditAction, SuggestionStatus
from app.infrastructure.db.models.project import Project
from app.infrastructure.db.models.user import User

logger = logging.getLogger("syncscribe.api.suggestions")

# #5 Создаётся один раз на уровне модуля. validate_python делает один
# проход через весь список вместо N вызовов model_validate.
_suggestion_list_adapter: TypeAdapter[list[SuggestionResponse]] = TypeAdapter(list[SuggestionResponse])

router = APIRouter(
    prefix="/projects/{project_id}/documents/{document_id}/suggestions",
    tags=["suggestions"],
)


async def _safe_bulk_log(
    audit_log_service: AuditLogService,
    user_id: uuid.UUID,
    decisions: list[tuple[uuid.UUID, AuditAction]],
) -> None:
    """#3 Батчевая запись аудит-лога. Ошибка не прерывает основной поток."""
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
    action: AuditAction,
) -> None:
    """#3 Единичная запись аудит-лога. Остаётся для accept/reject одиночных правок."""
    try:
        await audit_log_service.log_suggestion_decision(user_id, suggestion_id, action)
    except Exception:
        logger.warning(
            "Не удалось записать audit_log для решения по правке",
            extra={"suggestion_id": str(suggestion_id), "user_id": str(user_id)},
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
    # #5 TypeAdapter: один проход вместо N вызовов model_validate
    return Page[SuggestionResponse](
        items=_suggestion_list_adapter.validate_python(suggestions, from_attributes=True),
        total=total,
        limit=limit,
        offset=offset,
    )


@router.put("/review", response_model=ReviewSaveResponse)
async def atomic_review_save(
    document_id: uuid.UUID,
    payload: ReviewSaveRequest,
    project: Project = Depends(get_allowed_project),
    current_user: User = Depends(get_current_user),
    suggestion_service: SuggestionService = Depends(get_suggestion_service),
    audit_log_service: AuditLogService = Depends(get_audit_log_service),
) -> ReviewSaveResponse:
    decisions = [
        (
            d.suggestion_id,
            SuggestionStatus.ACCEPTED if d.decision == "accepted" else SuggestionStatus.REJECTED,
        )
        for d in payload.decisions
    ]

    try:
        result = await suggestion_service.atomic_review_save(
            project_id=project.id,
            document_id=document_id,
            user_id=current_user.id,
            review_version=payload.review_version,
            decisions=decisions,
            finalize=payload.finalize,
        )
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except OptimisticLockError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (InvalidDocumentStatusError, ReviewNotCompleteError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    # #3 батчевый audit_log вместо N отдельных await
    audit_decisions = [
        (
            d.suggestion_id,
            AuditAction.ACCEPT if d.decision == "accepted" else AuditAction.REJECT,
        )
        for d in payload.decisions
    ]
    await _safe_bulk_log(audit_log_service, current_user.id, audit_decisions)

    doc = result.document
    review_version = getattr(doc, "review_version", 0) or 0
    return ReviewSaveResponse(
        document_id=doc.id,
        document_status=doc.status.value,
        review_version=review_version,
        accepted_count=result.accepted_count,
        rejected_count=result.rejected_count,
        pending_count=result.pending_count,
        finalized=result.finalized,
    )


async def _decide_suggestion(
    action: Literal["accept", "reject"],
    document_id: uuid.UUID,
    suggestion_id: uuid.UUID,
    project: Project,
    current_user: User,
    suggestion_service: SuggestionService,
    audit_log_service: AuditLogService,
) -> SuggestionResponse:
    """#10 Общая логика accept и reject — разбитые эндпоинты сохраняются для
    ясного REST-контракта, но дублирование тела вынесено сюда."""
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

    audit_action = AuditAction.ACCEPT if action == "accept" else AuditAction.REJECT
    await _safe_single_log(audit_log_service, current_user.id, suggestion.id, audit_action)
    return SuggestionResponse.model_validate(suggestion)


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
        "accept", document_id, suggestion_id, project, current_user, suggestion_service, audit_log_service
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
        "reject", document_id, suggestion_id, project, current_user, suggestion_service, audit_log_service
    )


@router.post("/bulk-accept", response_model=BulkAcceptResponse)
async def bulk_accept_suggestions(
    document_id: uuid.UUID,
    project: Project = Depends(get_allowed_project),
    current_user: User = Depends(get_current_user),
    suggestion_service: SuggestionService = Depends(get_suggestion_service),
    audit_log_service: AuditLogService = Depends(get_audit_log_service),
) -> BulkAcceptResponse:
    try:
        result = await suggestion_service.bulk_accept(project.id, document_id, current_user.id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidDocumentStatusError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    # #3 батчевый audit_log вместо N отдельных await
    audit_decisions = [(s.id, AuditAction.ACCEPT) for s in result.suggestions]
    await _safe_bulk_log(audit_log_service, current_user.id, audit_decisions)

    doc = result.document
    return BulkAcceptResponse(
        accepted_count=len(result.suggestions),
        document_status=doc.status.value if doc else None,
        review_version=getattr(doc, "review_version", None),
    )


@router.post("/finalize", response_model=DocumentResponse)
async def finalize_review(
    document_id: uuid.UUID,
    project: Project = Depends(get_allowed_project),
    suggestion_service: SuggestionService = Depends(get_suggestion_service),
) -> DocumentResponse:
    try:
        document = await suggestion_service.finalize_review(project.id, document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (InvalidDocumentStatusError, ReviewNotCompleteError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return DocumentResponse.model_validate(document)


# ---------------------------------------------------------------------------
# P0-2: PUT /review — финализация с оптимистической блокировкой
# ---------------------------------------------------------------------------

@router.put("/review", response_model=DocumentResponse)
async def put_review(
    document_id: uuid.UUID,
    project: Project = Depends(get_allowed_project),
    suggestion_service: SuggestionService = Depends(get_suggestion_service),
    if_match: str | None = Header(default=None, alias="If-Match"),
) -> DocumentResponse:
    """Финализировать review с оптимистической блокировкой.

    Клиент обязан передать заголовок ``If-Match: <review_version>``.
    Значение должно совпадать с текущим ``review_version`` документа.
    При несовпадении → **412 Precondition Failed**.

    Эндпоинт идемпотентен при повторном вызове с тем же версионным
    значением (если документ уже в READY — возвращает 200 без ошибки).
    """
    if if_match is None:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail="Заголовок If-Match обязателен для PUT /review",
        )
    try:
        client_version = int(if_match.strip('"').strip())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Заголовок If-Match должен содержать целочисленный review_version, получено: {if_match!r}",
        )
    try:
        document = await suggestion_service.finalize_review_versioned(
            project.id, document_id, client_version
        )
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except StaleReviewVersionError as exc:
        raise HTTPException(status_code=status.HTTP_412_PRECONDITION_FAILED, detail=str(exc)) from exc
    except (InvalidDocumentStatusError, ReviewNotCompleteError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return DocumentResponse.model_validate(document)
