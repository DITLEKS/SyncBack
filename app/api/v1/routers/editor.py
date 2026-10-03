"""Агрегированный endpoint редактора документа + export + reset.

GET    /projects/{project_id}/documents/{document_id}/editor
POST   /projects/{project_id}/documents/{document_id}/editor/reset

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

FIX-P0: убраны ссылки на DocumentStatusVO.ERROR и DocumentStatusVO.CANCELLED
  (удалены в коммите fe39c67, 4STATUS).

PR4-FIX:
  - _safe_count_by_status теперь работает корректно — SuggestionService.count_by_document_and_status
    добавлен в PR4. Fallback по странице убран (был маскировкой ошибки).
  - reset_analysis(): reset_result — ResetResult dataclass; читаем reset_count и document напрямую.
  - EditorDocumentMeta.updated_at: использует document.updated_at (с fallback на uploaded_at),
    а не всегда uploaded_at.
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
from app.domain.lifecycle import DocumentLifecycle
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

# Режим отображения содержимого: до результатов анализа показываем исходник,
# во время ревью — текст с предложенными правками, после — чистовик.
_STATUS_VIEW_MODE: dict[DocumentStatusVO, str] = {
    DocumentStatusVO.DRAFT: "original",
    DocumentStatusVO.IN_PROGRESS: "original",
    DocumentStatusVO.AWAITING_APPROVAL: "suggested",
    DocumentStatusVO.READY: "clean",
}

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

    # PR4-FIX: updated_at берётся из document.updated_at (если есть),
    # иначе fallback на uploaded_at. Ранее всегда использовался uploaded_at.
    updated_at = getattr(document, "updated_at", None) or document.uploaded_at

    meta = EditorDocumentMeta(
        id=document.id,
        title=document.name,
        format=document.format.value,
        status=document.status,
        current_analysis_job_id=document.current_analysis_job_id,
        created_at=document.uploaded_at,
        updated_at=updated_at,
        review_version=review_version,
        view_mode=view_mode,
    )

    needs_original = DocumentLifecycle.has_review_results(document.status)

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

    original_content = original_content_raw if needs_original else editor_content

    suggestions = [SuggestionResponse.model_validate(s) for s in suggestions_raw]

    # PR4-FIX: счётчики всегда из агрегатного запроса O(1).
    # Fallback по текущей странице убран — он маскировал отсутствие метода
    # и давал некорректные значения при пагинации.
    pending = status_counts.get("pending", 0)
    accepted = status_counts.get("accepted", 0)
    rejected = status_counts.get("rejected", 0)

    counters = SuggestionCounters(
        total=suggestions_total,
        pending=pending,
        accepted=accepted,
        rejected=rejected,
    )

    permissions = EditorPermissions(
        can_analyze=DocumentLifecycle.can_start_analysis(document.status),
        can_review=DocumentLifecycle.can_review(document.status),
        can_export=DocumentLifecycle.can_export(document.status),
        can_delete=DocumentLifecycle.can_delete(document.status),
        sources_is_editable=DocumentLifecycle.can_edit_sources(document.status),
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
    """PR4-FIX: SuggestionService.count_by_document_and_status() теперь существует.
    AttributeError больше не возникает; except-ветка оставлена как защитный барьер
    на случай неожиданных исключений при запросе к БД.
    """
    try:
        return await suggestion_service.count_by_document_and_status(project_id, document_id)
    except Exception:  # noqa: BLE001
        logger.warning(
            "Не удалось получить агрегатные счётчики правок",
            extra={"document_id": str(document_id)},
        )
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
    job_service: AnalysisJobService = Depends(get_analysis_job_service),
) -> ResetResponse:
    """Сбросить все правки текущего анализа: статус suggestions → pending,
    документ → AWAITING_APPROVAL.
    """
    try:
        reset_result = await job_service.reset_analysis(project.id, document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except InvalidDocumentStatusError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    suggestions_reset_count = reset_result.reset_count
    updated_doc = reset_result.document
    new_status = updated_doc.status.value
    new_review_version = getattr(updated_doc, "review_version", 0) or 0

    return ResetResponse(
        document_id=document_id,
        document_status=new_status,
        review_version=new_review_version,
        suggestions_reset_count=suggestions_reset_count,
    )
