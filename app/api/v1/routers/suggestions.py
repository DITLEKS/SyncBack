"""API для работы с правками документа.

FIX-2: /reject-all возвращает BulkRejectResponse с полем rejected_count вместо
        семантически неверного accepted_count=N из BulkAcceptResponse.
FIX-6: _safe_bulk_log и _safe_single_log добавлен exc_info=True для сохранения трейса.
FIX-7: review_save логирует предупреждение при расхождении If-Match vs payload.review_version.
RESET: POST /{suggestion_id}/reset — отмена решения, возврат в PENDING.
R-3: POST /finalize удалён — дублировал PUT /review с finalize=true.
R-9: GET / принимает ?status=pending|accepted|rejected для серверной фильтрации.

REFACTOR (bulk unification):
  PATCH /suggestions — единый эндпоинт для single и bulk смены статуса правок.
    · ids: list[UUID]  → точечное обновление
    · filter: str      → bulk по предустановленному фильтру (pending/decided/all)
    · status           → целевой статус (accepted/rejected/pending=reset)
  Старые RPC-суффиксы /accept, /reject, /accept-all, /reject-all оставлены
  как deprecated HTTP 308 aliases до следующего мажорного релиза API.
"""

import logging
import uuid
from dataclasses import dataclass
from typing import List

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import TypeAdapter

from app.api.deps import get_allowed_project, get_current_user
from app.api.schemas.document import DocumentResponse
from app.api.schemas.pagination import Page
from app.api.schemas.review import ReviewSaveRequest, ReviewSaveResponse
from app.api.schemas.suggestion import (
    BulkAcceptResponse,
    BulkRejectResponse,
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

# L-4
PageSuggestionResponse = Page[SuggestionResponse]

# R-9: допустимые значения фильтра статуса правки
_SUGGESTION_STATUS_FILTER_MAP: dict[str, SuggestionStatusVO] = {
    vo.value: vo for vo in SuggestionStatusVO
}

# Дата окончания поддержки deprecated aliases (ISO 8601)
_DEPRECATED_SUNSET = "2027-01-01"

router = APIRouter(
    prefix="/projects/{project_id}/documents/{document_id}/suggestions",
    tags=["suggestions"],
)


# ---------------------------------------------------------------------------
# M-5: DecisionContext dataclass вместо 7-аргументной сигнатуры
# ---------------------------------------------------------------------------

@dataclass
class DecisionContext:
    """Контекст принятия решения по одной правке."""
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
    """Список правок документа с опциональной фильтрацией по статусу (R-9)."""
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
# PATCH /suggestions — единый эндпоинт обновления статуса (single + bulk)
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
    - **ids** — список UUID для точечного обновления (включая одну правку).
    - **filter** — предустановленный фильтр:
      - `pending`  — все правки ещё без решения
      - `decided`  — все accepted + rejected (массовый reset)
      - `all`      — все правки документа

    Допустимые переходы статуса:
    | Текущий    | Целевой    | Семантика           |
    |------------|------------|---------------------|
    | pending    | accepted   | принять             |
    | pending    | rejected   | отклонить           |
    | accepted   | pending    | сбросить решение    |
    | rejected   | pending    | сбросить решение    |
    | decided    | pending    | массовый reset      |

    Заменяет: `POST /accept`, `POST /reject`, `POST /accept-all`, `POST /reject-all`.
    """
    target_status = payload.status  # "accepted" | "rejected" | "pending"

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
    except (InvalidDocumentStatusError, SuggestionAlreadyDecidedError, SuggestionResetNotAllowedError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    # Audit log: определяем действие по целевому статусу
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


# ---------------------------------------------------------------------------
# POST /{id}/reset — сброс решения по одной правке
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
    """Отменить ранее принятое или отклонённое решение — вернуть правку в PENDING.

    Доступно только пока документ в статусе `awaiting_approval`.
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


# ---------------------------------------------------------------------------
# DEPRECATED ALIASES — будут удалены после 2027-01-01
# Все редиректят на PATCH /suggestions с нужным телом через 308.
# include_in_schema=False — скрыты из OpenAPI / Swagger UI.
# ---------------------------------------------------------------------------

def _deprecated_response(redirect_url: str) -> RedirectResponse:
    """308 Permanent Redirect с маркерами deprecation."""
    response = RedirectResponse(url=redirect_url, status_code=308)
    response.headers["X-Deprecated"] = "true"
    response.headers["Sunset"] = _DEPRECATED_SUNSET
    response.headers["Link"] = (
        f'<{redirect_url}>; rel="successor-version"'
    )
    return response


@router.post(
    "/{suggestion_id}/accept",
    include_in_schema=False,
    deprecated=True,
)
async def accept_suggestion_deprecated(
    document_id: uuid.UUID,
    suggestion_id: uuid.UUID,
    project: Project = Depends(get_allowed_project),
    current_user: User = Depends(get_current_user),
    suggestion_service: SuggestionService = Depends(get_suggestion_service),
    audit_log_service: AuditLogService = Depends(get_audit_log_service),
) -> SuggestionResponse:
    """@deprecated — используйте PATCH /suggestions с {"ids":[id],"status":"accepted"}."""
    return await _decide_suggestion(DecisionContext(
        action="accept",
        document_id=document_id,
        suggestion_id=suggestion_id,
        project=project,
        current_user=current_user,
        suggestion_service=suggestion_service,
        audit_log_service=audit_log_service,
    ))


@router.post(
    "/{suggestion_id}/reject",
    include_in_schema=False,
    deprecated=True,
)
async def reject_suggestion_deprecated(
    document_id: uuid.UUID,
    suggestion_id: uuid.UUID,
    project: Project = Depends(get_allowed_project),
    current_user: User = Depends(get_current_user),
    suggestion_service: SuggestionService = Depends(get_suggestion_service),
    audit_log_service: AuditLogService = Depends(get_audit_log_service),
) -> SuggestionResponse:
    """@deprecated — используйте PATCH /suggestions с {"ids":[id],"status":"rejected"}."""
    return await _decide_suggestion(DecisionContext(
        action="reject",
        document_id=document_id,
        suggestion_id=suggestion_id,
        project=project,
        current_user=current_user,
        suggestion_service=suggestion_service,
        audit_log_service=audit_log_service,
    ))


@router.post(
    "/accept-all",
    include_in_schema=False,
    deprecated=True,
)
async def bulk_accept_suggestions_deprecated(
    document_id: uuid.UUID,
    project: Project = Depends(get_allowed_project),
    current_user: User = Depends(get_current_user),
    suggestion_service: SuggestionService = Depends(get_suggestion_service),
    audit_log_service: AuditLogService = Depends(get_audit_log_service),
) -> BulkAcceptResponse:
    """@deprecated — используйте PATCH /suggestions с {"filter":"pending","status":"accepted"}."""
    try:
        result = await suggestion_service.bulk_accept(
            project.id, document_id, current_user.id
        )
    except (DocumentNotFoundError, SuggestionNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (InvalidDocumentStatusError, ReviewNotCompleteError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    accepted_ids = [s.id for s in result.suggestions]
    await _safe_bulk_log(
        audit_log_service,
        current_user.id,
        [(sid, AuditActionVO.BULK_ACCEPT) for sid in accepted_ids],
    )
    doc = result.document
    return BulkAcceptResponse(
        accepted_count=len(accepted_ids),
        document_status=doc.status.value if doc else None,
        review_version=doc.review_version if doc else None,
    )


@router.post(
    "/reject-all",
    include_in_schema=False,
    deprecated=True,
)
async def bulk_reject_suggestions_deprecated(
    document_id: uuid.UUID,
    project: Project = Depends(get_allowed_project),
    current_user: User = Depends(get_current_user),
    suggestion_service: SuggestionService = Depends(get_suggestion_service),
    audit_log_service: AuditLogService = Depends(get_audit_log_service),
) -> BulkRejectResponse:
    """@deprecated — используйте PATCH /suggestions с {"filter":"pending","status":"rejected"}."""
    try:
        result = await suggestion_service.bulk_reject(
            project.id, document_id, current_user.id
        )
    except (DocumentNotFoundError, SuggestionNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (InvalidDocumentStatusError, ReviewNotCompleteError) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    rejected_ids = [s.id for s in result.suggestions]
    await _safe_bulk_log(
        audit_log_service,
        current_user.id,
        [(sid, AuditActionVO.REJECT) for sid in rejected_ids],
    )
    doc = result.document
    return BulkRejectResponse(
        rejected_count=len(rejected_ids),
        document_status=doc.status.value if doc else None,
        review_version=doc.review_version if doc else None,
    )
