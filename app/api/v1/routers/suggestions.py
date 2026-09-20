"""
Просмотр и точечное подтверждение/отклонение правок, bulk-accept, finalize.

ИСПРАВЛЕНО (rev-3): accept/reject → публичные методы сервиса;
убрана _assert_document_awaiting_approval; убраны мёртвые импорты.

P0-2 (rev-4): добавлен PUT /review с оптимистической блокировкой через
заголовок If-Match. Клиент присылает текущий review_version в виде
'If-Match: <version>'. При несовпадении → 412 Precondition Failed.
finalize_review в сервисе инкрементирует review_version атомарно.
"""
import logging
import uuid

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from app.api.deps import get_allowed_project, get_current_user
from app.api.schemas.document import DocumentResponse
from app.api.schemas.pagination import Page
from app.api.schemas.suggestion import BulkAcceptResponse, SuggestionResponse
from app.core.dependencies import get_audit_log_service, get_suggestion_service
from app.domain.exceptions import (
    DocumentNotFoundError,
    InvalidDocumentStatusError,
    ReviewNotCompleteError,
    StaleReviewVersionError,
    SuggestionAlreadyDecidedError,
    SuggestionNotFoundError,
)
from app.domain.services.audit_log_service import AuditLogService
from app.domain.services.suggestion_service import SuggestionService
from app.infrastructure.db.models.enums import AuditAction
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
        suggestion = await suggestion_service.accept_suggestion(
            project.id, document_id, suggestion_id, current_user.id
        )
    except (DocumentNotFoundError, SuggestionNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidDocumentStatusError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
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
        suggestion = await suggestion_service.reject_suggestion(
            project.id, document_id, suggestion_id, current_user.id
        )
    except (DocumentNotFoundError, SuggestionNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidDocumentStatusError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
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
    """Перевести документ в READY (переход №8 статусной модели).

    MVP: export_service не передаётся → ленивый экспорт при /export.
    """
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
