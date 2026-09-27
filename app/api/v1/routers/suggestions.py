"""API для работы с правками документа.

FIX-2: /reject-all возвращает BulkRejectResponse с полем rejected_count.
FIX-6: _safe_bulk_log и _safe_single_log с exc_info=True.
FIX-7: review_save логирует предупреждение при расхождении If-Match vs payload.review_version.
RESET: POST /{suggestion_id}/reset — делегирует в patch_suggestions (OPT-S4).
R-3: POST /finalize удалён — дублировал PUT /review с finalize=true.
R-9: GET / принимает ?status= для серверной фильтрации.

REFACTOR (bulk unification):
  PATCH /suggestions — единственный endpoint для изменения статуса правок.

OPT-S4: POST /{id}/reset — alias поверх patch_suggestions;
        возвращает SuggestionResponse для одиночного id (обратная совместимость).
OPT-S5: audit_decisions строится через itertools.chain (без O(N) tuple в памяти).
"""

import itertools
import logging
import uuid
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from pydantic import TypeAdapter

from app.api.deps import get_allowed_project, get_current_user
from app.api.schemas.pagination import Page
from app.api.schemas.review import ReviewSaveRequest, ReviewSaveResponse
from app.api.schemas.suggestion import (
    PatchSuggestionsRequest,
    PatchSuggestionsResponse,
    SuggestionResponse,
)
from app.core.dependencies import get_audit_log_service, get_suggestion_service
from app.domain.exceptions import (
    DocumentNotFoundError,
    InvalidDocumentStatusError,
    OptimisticLockError,
    ReviewNotCompleteError,
    StaleSuggestionJobError,
    SuggestionAlreadyDecidedError,
    SuggestionNotFoundError,
    SuggestionResetNotAllowedError,
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

PageSuggestionResponse = Page[SuggestionResponse]

_SUGGESTION_STATUS_FILTER_MAP: dict[str, SuggestionStatusVO] = {
    vo.value: vo for vo in SuggestionStatusVO
}

router = APIRouter(
    prefix="/projects/{project_id}/documents/{document_id}/suggestions",
    tags=["suggestions"],
)


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
            exc_info=True,
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
            exc_info=True,
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


# ---------------------------------------------------------------------------
# GET /suggestions
# ---------------------------------------------------------------------------

@router.get("", response_model=PageSuggestionResponse)
async def list_suggestions(
    document_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    status_filter: str | None = Query(
        default=None,
        alias="status",
        description=(
            "Фильтр по статусу правки. "
            f"Допустимые значения: {', '.join(_SUGGESTION_STATUS_FILTER_MAP)}"
        ),
    ),
    project: Project = Depends(get_allowed_project),
    suggestion_service: SuggestionService = Depends(get_suggestion_service),
) -> PageSuggestionResponse:
    """Список правок документа с опциональной фильтрацией по статусу."""
    status_vo: SuggestionStatusVO | None = None
    if status_filter is not None:
        status_vo = _SUGGESTION_STATUS_FILTER_MAP.get(status_filter.lower())
        if status_vo is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Неподдерживаемый статус правки: {status_filter!r}. "
                    f"Допустимые значения: {', '.join(_SUGGESTION_STATUS_FILTER_MAP)}"
                ),
            )

    pagination = PaginationParams(limit=limit, offset=offset)
    try:
        suggestions, total = await suggestion_service.list_suggestions_for_document(
            project.id, document_id, pagination, status_filter=status_vo
        )
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return PageSuggestionResponse(
        items=_suggestion_list_adapter.validate_python(suggestions, from_attributes=True),
        total=total,
        limit=limit,
        offset=offset,
    )


# ---------------------------------------------------------------------------
# GET /suggestions/{suggestion_id}
# ---------------------------------------------------------------------------

@router.get("/{suggestion_id}", response_model=SuggestionResponse)
async def get_suggestion(
    document_id: uuid.UUID,
    suggestion_id: uuid.UUID,
    project: Project = Depends(get_allowed_project),
    suggestion_service: SuggestionService = Depends(get_suggestion_service),
) -> SuggestionResponse:
    """Получить одну правку по ID."""
    try:
        suggestion = await suggestion_service.get_suggestion_for_document(
            project.id, document_id, suggestion_id
        )
    except (DocumentNotFoundError, SuggestionNotFoundError, StaleSuggestionJobError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return SuggestionResponse.model_validate(suggestion)


# ---------------------------------------------------------------------------
# PATCH /suggestions — единственный endpoint изменения статуса (single + bulk)
# ---------------------------------------------------------------------------

@router.patch(
    "",
    response_model=PatchSuggestionsResponse,
    status_code=status.HTTP_200_OK,
    summary="Обновить статус правок (single или bulk)",
    responses={
        200: {"description": "Статус обновлён, возвращает кол-во изменённых правок"},
        400: {"description": "Невалидный запрос (оба или ни один selector)"},
        404: {"description": "Документ или правки не найдены"},
        409: {"description": "Конфликт статуса документа или правки"},
    },
)
async def patch_suggestions(
    document_id: uuid.UUID,
    payload: PatchSuggestionsRequest,
    project: Project = Depends(get_allowed_project),
    current_user: User = Depends(get_current_user),
    suggestion_service: SuggestionService = Depends(get_suggestion_service),
    audit_log_service: AuditLogService = Depends(get_audit_log_service),
) -> PatchSuggestionsResponse:
    """Обновить статус правок одним запросом.

    Ровно одно из полей обязательно:
    - **ids** — список UUID для точечного обновления.
    - **filter** — предустановленный фильтр: `pending` / `decided` / `all`.

    Допустимые переходы:
    | Текущий | Целевой  | Семантика        |
    |---------|----------|------------------|
    | pending | accepted | принять          |
    | pending | rejected | отклонить        |
    | decided | pending  | сбросить решение |
    """
    target_status = payload.status

    try:
        result = await suggestion_service.patch_suggestions(
            project_id=project.id,
            document_id=document_id,
            user_id=current_user.id,
            ids=payload.ids,
            filter=payload.filter,
            target_status=target_status,
        )
    except (DocumentNotFoundError, SuggestionNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (
        InvalidDocumentStatusError,
        SuggestionAlreadyDecidedError,
        SuggestionResetNotAllowedError,
    ) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    action_map = {
        "accepted": AuditActionVO.ACCEPT,
        "rejected": AuditActionVO.REJECT,
        "pending": AuditActionVO.RESET,
    }
    audit_action = action_map[target_status]
    updated_ids = getattr(result, "updated_ids", None) or []
    if updated_ids:
        await _safe_bulk_log(
            audit_log_service,
            current_user.id,
            [(sid, audit_action) for sid in updated_ids],
        )

    doc = getattr(result, "document", None)
    return PatchSuggestionsResponse(
        updated_count=result.updated_count,
        document_status=doc.status.value if doc else None,
        review_version=getattr(doc, "review_version", None) if doc else None,
    )


# ---------------------------------------------------------------------------
# PUT /review — батч accept/reject с optimistic locking
# ---------------------------------------------------------------------------

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

    if (
        client_version is not None
        and payload.review_version is not None
        and client_version != payload.review_version
    ):
        logger.warning(
            "If-Match (%s) расходится с payload.review_version (%s) — используется If-Match",
            client_version,
            payload.review_version,
            extra={"document_id": str(document_id), "user_id": str(current_user.id)},
        )

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

    # OPT-S5: itertools.chain вместо tuple unpack — без O(N) аллокации в памяти
    audit_decisions: list[tuple[uuid.UUID, AuditActionVO]] = [
        (sid, AuditActionVO.ACCEPT if sid in accepted_set else AuditActionVO.REJECT)
        for sid in itertools.chain(accepted_ids, rejected_ids)
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


# ---------------------------------------------------------------------------
# POST /{suggestion_id}/reset — alias поверх PATCH (OPT-S4)
# ---------------------------------------------------------------------------

@router.post("/{suggestion_id}/reset", response_model=SuggestionResponse)
async def reset_suggestion(
    document_id: uuid.UUID,
    suggestion_id: uuid.UUID,
    project: Project = Depends(get_allowed_project),
    current_user: User = Depends(get_current_user),
    suggestion_service: SuggestionService = Depends(get_suggestion_service),
    audit_log_service: AuditLogService = Depends(get_audit_log_service),
) -> SuggestionResponse:
    """Отменить ранее принятое/отклонённое решение — вернуть правку в PENDING.

    OPT-S4: делегирует в reset_suggestion сервиса (единая бизнес-логика).
    Возвращает полный SuggestionResponse для обратной совместимости.
    Для bulk-reset используй PATCH /suggestions с {"ids": [...], "status": "pending"}.
    """
    try:
        suggestion = await suggestion_service.reset_suggestion(
            project.id, document_id, suggestion_id, current_user.id
        )
    except (DocumentNotFoundError, SuggestionNotFoundError, StaleSuggestionJobError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (InvalidDocumentStatusError, SuggestionResetNotAllowedError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    await _safe_single_log(
        audit_log_service, current_user.id, suggestion.id, AuditActionVO.RESET
    )
    return SuggestionResponse.model_validate(suggestion)
