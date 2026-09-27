"""Агрегированный endpoint редактора документа + export + reset.

GET    /projects/{project_id}/documents/{document_id}/editor
POST   /projects/{project_id}/documents/{document_id}/editor/reset

Экспорт вынесен в GET /projects/{project_id}/documents/{document_id}/export
(роутер documents.py) — /editor/export удалён как дублирующий endpoint.

#7: возвращаем view_mode и original_content (исходный plain_text без правок)
#8: возвращаем sources_is_editable=False при in_progress / awaiting_approval

API-1: пагинация правок в агрегате:
  - query-параметры suggestions_limit (default=50, max=200) и suggestions_offset
  - поле suggestions_total — полный счётчик правок документа (для пагинатора фронта)
  - хардкод limit=200 убран; правки > suggestions_limit больше не теряются молча

API-5: POST /editor/reset возвращает 200 + ResetResponse вместо 204:
  - document_status, review_version, suggestions_reset_count
  - фронт обновляет стор без дополнительного GET /editor

C-3 (аудит): counters.total = suggestions_total (полный счётчик, а не длина страницы).
  counters.pending/accepted/rejected берутся из агрегатного запроса O(1),
  а не из O(n) прохода по текущей странице.

REFACTOR: DELETE /editor/suggestions удалён — операция перенесена в
  PATCH /projects/{project_id}/documents/{document_id}/suggestions
  с телом {"filter":"pending","status":"rejected"}.

PERF:
  - get_document_content и get_original_content вызываются через asyncio.gather()
  - reset_analysis: второй SELECT get_document убран — документ берётся из reset_result

FIX-PERM-1: permissions block обновлён:
  - can_analyze включает ERROR и CANCELLED (повторный запуск после сбоя/отмены).
  - can_delete: ERROR и CANCELLED явно unlocked (explicit > implicit).
  - sources_is_editable: ERROR и CANCELLED трактуются как редактируемые
    (анализ не запущен, источники менять разрешено).
"""
import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.deps import get_allowed_project, get_current_user
from app.api.schemas.document import DocumentSectionResponse, SuggestionCounters
from app.api.schemas.editor import (
    EditorAggregateResponse,
    EditorContent,
    EditorDocumentMeta,
    EditorPermissions,
    ResetResponse,
)
from app.api.schemas.suggestion import SuggestionResponse
from app.core.dependencies import (
    get_analysis_job_service,
    get_document_service,
    get_suggestion_service,
)
from app.domain.exceptions import DocumentNotFoundError, InvalidDocumentStatusError
from app.domain.services.analysis_job_service import AnalysisJobService
from app.domain.services.document_service import DocumentService
from app.domain.services.suggestion_service import SuggestionService
from app.domain.value_objects import DocumentStatusVO, PaginationParams
from app.infrastructure.db.models.project import Project
from app.infrastructure.db.models.user import User

logger = logging.getLogger("syncscribe.api.editor")

router = APIRouter(
    prefix="/projects/{project_id}/documents/{document_id}/editor",
    tags=["editor"],
)

_STATUSES_WITH_APPLIED_CHANGES = frozenset({
    DocumentStatusVO.AWAITING_APPROVAL,
    DocumentStatusVO.READY,
})

_STATUS_VIEW_MODE: dict[DocumentStatusVO, str] = {
    DocumentStatusVO.DRAFT: "original",
    DocumentStatusVO.IN_PROGRESS: "original",
    DocumentStatusVO.AWAITING_APPROVAL: "suggested",
    DocumentStatusVO.READY: "clean",
    DocumentStatusVO.ERROR: "original",
    DocumentStatusVO.CANCELLED: "original",
}

# FIX-PERM-1: статусы, из которых можно запустить анализ.
# Включает ERROR и CANCELLED — позволяет повторный запуск без ручного
# изменения статуса через БД. Соответствует _ANALYSIS_ALLOWED_STATUSES
# в analysis_job_service.py (FIX-ANAL-1).
_CAN_ANALYZE_STATUSES = frozenset({
    DocumentStatusVO.DRAFT,
    DocumentStatusVO.READY,
    DocumentStatusVO.ERROR,
    DocumentStatusVO.CANCELLED,
})

# FIX-PERM-1: статусы, при которых документ «заблокирован» (анализ активен).
_LOCKED_STATUSES = frozenset({
    DocumentStatusVO.IN_PROGRESS,
})

# FIX-PERM-1: статусы, при которых источники недоступны для редактирования.
# ERROR и CANCELLED — анализ не запущен, источники менять можно (как DRAFT).
_SOURCES_NOT_EDITABLE_STATUSES = frozenset({
    DocumentStatusVO.IN_PROGRESS,
    DocumentStatusVO.AWAITING_APPROVAL,
})

_SUGGESTIONS_MAX_LIMIT = 200


def _build_editor_content(parsed) -> EditorContent:
    return EditorContent(
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


@router.get("", response_model=EditorAggregateResponse)
async def get_editor_aggregate(
    document_id: uuid.UUID,
    project: Project = Depends(get_allowed_project),
    current_user: User = Depends(get_current_user),
    document_service: DocumentService = Depends(get_document_service),
    suggestion_service: SuggestionService = Depends(get_suggestion_service),
    suggestions_limit: int = Query(
        default=50,
        ge=1,
        le=_SUGGESTIONS_MAX_LIMIT,
        description=(
            "Максимальное число правок на странице (1–200). "
            "Используйте suggestions_total из ответа для построения пагинатора."
        ),
    ),
    suggestions_offset: int = Query(
        default=0,
        ge=0,
        description="Смещение для пагинации правок.",
    ),
) -> EditorAggregateResponse:
    """Полный агрегат данных для экрана редактора."""
    try:
        document = await document_service.get_document(project.id, document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    review_version = getattr(document, "review_version", 0) or 0
    view_mode = _STATUS_VIEW_MODE.get(document.status, "original")

    meta = EditorDocumentMeta(
        id=document.id,
        title=document.name,
        format=document.format.value,
        status=document.status,
        current_analysis_job_id=document.current_analysis_job_id,
        created_at=document.uploaded_at,
        updated_at=document.uploaded_at,
        review_version=review_version,
        view_mode=view_mode,
    )

    # PERF: content и original_content независимы — запускаем параллельно
    needs_original = document.status in _STATUSES_WITH_APPLIED_CHANGES

    async def _fetch_content():
        try:
            parsed = await document_service.get_document_content(document)
            return _build_editor_content(parsed)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Не удалось получить контент документа для редактора",
                extra={"document_id": str(document_id)},
            )
            return None

    async def _fetch_original():
        if not needs_original:
            return None
        try:
            orig_parsed = await document_service.get_original_content(document)
            return _build_editor_content(orig_parsed)
        except (AttributeError, NotImplementedError):
            return None
        except Exception:  # noqa: BLE001
            logger.warning(
                "Не удалось получить original_content документа",
                extra={"document_id": str(document_id)},
            )
            return None

    (
        (editor_content, original_content_raw),
        (suggestions_raw, suggestions_total),
        status_counts,
    ) = await asyncio.gather(
        asyncio.gather(_fetch_content(), _fetch_original()),
        suggestion_service.list_suggestions_for_document(
            project.id,
            document_id,
            PaginationParams(limit=suggestions_limit, offset=suggestions_offset),
        ),
        _safe_count_by_status(suggestion_service, project.id, document_id),
    )

    # Если оригинал не нужен — подставляем текущий контент (статусы DRAFT/IN_PROGRESS/ERROR)
    original_content = original_content_raw if needs_original else editor_content

    suggestions = [SuggestionResponse.model_validate(s) for s in suggestions_raw]

    pending = status_counts.get("pending", 0)
    accepted = status_counts.get("accepted", 0)
    rejected = status_counts.get("rejected", 0)
    # Fallback: считаем по текущей странице если агрегат вернул пустой словарь
    if not status_counts:
        for s in suggestions:
            if s.status == "pending":
                pending += 1
            elif s.status == "accepted":
                accepted += 1
            elif s.status == "rejected":
                rejected += 1

    counters = SuggestionCounters(
        total=suggestions_total,
        pending=pending,
        accepted=accepted,
        rejected=rejected,
    )

    # FIX-PERM-1: явные frozenset-константы вместо inline-выражений.
    permissions = EditorPermissions(
        can_analyze=document.status in _CAN_ANALYZE_STATUSES,
        can_review=document.status == DocumentStatusVO.AWAITING_APPROVAL,
        can_export=document.status == DocumentStatusVO.READY,
        can_delete=document.status not in _LOCKED_STATUSES,
        sources_is_editable=document.status not in _SOURCES_NOT_EDITABLE_STATUSES,
    )

    return EditorAggregateResponse(
        document=meta,
        content=editor_content,
        original_content=original_content,
        suggestions=suggestions,
        suggestions_total=suggestions_total,
        counters=counters,
        permissions=permissions,
    )


async def _safe_count_by_status(
    suggestion_service: SuggestionService,
    project_id: uuid.UUID,
    document_id: uuid.UUID,
) -> dict:
    try:
        return await suggestion_service.count_by_document_and_status(project_id, document_id)
    except (AttributeError, NotImplementedError):
        return {}


@router.post(
    "/reset",
    response_model=ResetResponse,
    status_code=status.HTTP_200_OK,
    summary="Сбросить все изменения",
)
async def reset_analysis(
    document_id: uuid.UUID,
    project: Project = Depends(get_allowed_project),
    current_user: User = Depends(get_current_user),
    document_service: DocumentService = Depends(get_document_service),
    job_service: AnalysisJobService = Depends(get_analysis_job_service),
) -> ResetResponse:
    """Сбросить все правки текущего анализа: статус suggestions → pending,
    документ → AWAITING_APPROVAL.

    PERF: повторный get_document после reset убран — статус берётся из reset_result.
    """
    try:
        document = await document_service.get_document(project.id, document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    if document.status not in (
        DocumentStatusVO.AWAITING_APPROVAL,
        DocumentStatusVO.READY,
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Сброс возможен только для документов в статусе "
                "AWAITING_APPROVAL или READY. "
                f"Текущий статус: {document.status.value}"
            ),
        )

    reset_result = await job_service.reset_analysis(project.id, document_id)

    # reset_analysis может вернуть int (кол-во правок) или объект с документом.
    # Если возвращает объект — берём статус из него, избегая второго SELECT.
    if isinstance(reset_result, int):
        suggestions_reset_count = reset_result
        new_status = DocumentStatusVO.AWAITING_APPROVAL.value
        new_review_version = 0
    else:
        suggestions_reset_count = getattr(reset_result, "reset_count", 0) or 0
        updated_doc = getattr(reset_result, "document", None)
        if updated_doc is not None:
            new_status = updated_doc.status.value
            new_review_version = getattr(updated_doc, "review_version", 0) or 0
        else:
            new_status = DocumentStatusVO.AWAITING_APPROVAL.value
            new_review_version = 0

    return ResetResponse(
        document_id=document_id,
        document_status=new_status,
        review_version=new_review_version,
        suggestions_reset_count=suggestions_reset_count,
    )
