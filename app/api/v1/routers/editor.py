"""Агрегированный endpoint редактора документа + export + reset + cancel-all-suggestions.

GET    /projects/{project_id}/documents/{document_id}/editor
POST   /projects/{project_id}/documents/{document_id}/editor/export?format=docx
POST   /projects/{project_id}/documents/{document_id}/editor/reset
DELETE /projects/{project_id}/documents/{document_id}/editor/suggestions

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

NEW: DELETE /editor/suggestions — отменить все правки документа сразу.
  Переводит все pending-правки в rejected, возвращает число отменённых.
"""
import logging
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response

from app.api.deps import get_allowed_project, get_current_user
from app.api.schemas.document import DocumentSectionResponse, SuggestionCounters
from app.api.schemas.editor import (
    CancelAllSuggestionsResponse,
    EditorAggregateResponse,
    EditorContent,
    EditorDocumentMeta,
    EditorPermissions,
    ResetResponse,
)
from app.api.schemas.suggestion import SuggestionResponse
from app.core.dependencies import (
    get_analysis_job_service,
    get_document_export_service,
    get_document_service,
    get_suggestion_service,
)
from app.domain.exceptions import DocumentNotFoundError, InvalidDocumentStatusError
from app.domain.services.analysis_job_service import AnalysisJobService
from app.domain.services.document_export_service import DocumentExportService
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

# Статусы, при которых контент документа уже содержит применённые правки
_STATUSES_WITH_APPLIED_CHANGES = frozenset({
    DocumentStatusVO.AWAITING_APPROVAL,
    DocumentStatusVO.READY,
})

# Маппинг статуса → view_mode (#7)
_STATUS_VIEW_MODE: dict[DocumentStatusVO, str] = {
    DocumentStatusVO.DRAFT: "original",
    DocumentStatusVO.IN_PROGRESS: "original",
    DocumentStatusVO.AWAITING_APPROVAL: "suggested",
    DocumentStatusVO.READY: "clean",
    DocumentStatusVO.ERROR: "original",
    DocumentStatusVO.CANCELLED: "original",
}

# Поддерживаемые форматы экспорта → (расширение, media_type)
_EXPORT_FORMAT_META: dict[str, tuple[str, str]] = {
    "docx": (
        ".docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ),
    "md": (".md", "text/markdown; charset=utf-8"),
    "txt": (".txt", "text/plain; charset=utf-8"),
}

# API-1: максимальный размер страницы правок в агрегате редактора
_SUGGESTIONS_MAX_LIMIT = 200


@router.get("", response_model=EditorAggregateResponse)
async def get_editor_aggregate(
    document_id: uuid.UUID,
    project: Project = Depends(get_allowed_project),
    current_user: User = Depends(get_current_user),
    document_service: DocumentService = Depends(get_document_service),
    suggestion_service: SuggestionService = Depends(get_suggestion_service),
    # API-1: параметры пагинации правок
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
    """Полный агрегат данных для экрана редактора.

    API-1: поддерживает пагинацию правок через suggestions_limit / suggestions_offset.
    Поле suggestions_total в ответе — полный счётчик всех правок документа;
    counters.total — тоже полный счётчик (= suggestions_total), не длина страницы.

    C-3: counters.pending/accepted/rejected берутся из агрегатного запроса O(1),
    а не из O(n) прохода по текущей странице.
    """
    # --- 1. Документ ---
    try:
        document = await document_service.get_document(project.id, document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    review_version = getattr(document, "review_version", 0) or 0
    view_mode = _STATUS_VIEW_MODE.get(document.status, "original")  # #7

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

    # --- 2. Контент (graceful degradation) ---
    editor_content: EditorContent | None = None
    try:
        parsed = await document_service.get_document_content(document)
        editor_content = EditorContent(
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
    except Exception:  # noqa: BLE001
        logger.warning(
            "Не удалось получить контент документа для редактора",
            extra={"document_id": str(document_id)},
        )

    # --- 2b. original_content (#7) ---
    original_content: EditorContent | None = None
    if document.status in _STATUSES_WITH_APPLIED_CHANGES:
        try:
            orig_parsed = await document_service.get_original_content(document)
            original_content = EditorContent(
                plain_text=orig_parsed.plain_text,
                sections=[
                    DocumentSectionResponse(
                        ref=s.ref,
                        start_offset=s.start_offset,
                        end_offset=s.end_offset,
                    )
                    for s in orig_parsed.sections
                ],
            )
        except (AttributeError, NotImplementedError):
            pass
        except Exception:  # noqa: BLE001
            logger.warning(
                "Не удалось получить original_content документа",
                extra={"document_id": str(document_id)},
            )
    else:
        original_content = editor_content

    # --- 3. Правки (API-1: пагинируемый запрос) ---
    suggestions_raw, suggestions_total = await suggestion_service.list_suggestions_for_document(
        project.id,
        document_id,
        PaginationParams(limit=suggestions_limit, offset=suggestions_offset),
    )
    suggestions = [SuggestionResponse.model_validate(s) for s in suggestions_raw]

    # --- 4. Счётчики (C-3: агрегатный запрос O(1) вместо O(n) по странице) ---
    # counters.total = suggestions_total (полный счётчик документа, не длина страницы).
    # pending/accepted/rejected — из отдельного COUNT-запроса по статусам.
    try:
        status_counts = await suggestion_service.count_by_document_and_status(
            project.id, document_id
        )
        pending = status_counts.get("pending", 0)
        accepted = status_counts.get("accepted", 0)
        rejected = status_counts.get("rejected", 0)
    except (AttributeError, NotImplementedError):
        # Graceful degradation: если метод ещё не реализован в сервисе,
        # считаем по текущей странице (старое поведение).
        pending = accepted = rejected = 0
        for s in suggestions:
            if s.status == "pending":
                pending += 1
            elif s.status == "accepted":
                accepted += 1
            elif s.status == "rejected":
                rejected += 1

    counters = SuggestionCounters(
        total=suggestions_total,  # C-3: полный счётчик документа
        pending=pending,
        accepted=accepted,
        rejected=rejected,
    )

    # --- 5. Права ---
    locked = document.status in (DocumentStatusVO.IN_PROGRESS,)
    sources_is_editable = document.status not in (
        DocumentStatusVO.IN_PROGRESS,
        DocumentStatusVO.AWAITING_APPROVAL,
    )
    permissions = EditorPermissions(
        can_analyze=document.status in (DocumentStatusVO.DRAFT, DocumentStatusVO.READY),
        can_review=document.status == DocumentStatusVO.AWAITING_APPROVAL,
        can_export=document.status == DocumentStatusVO.READY,
        can_delete=not locked,
        sources_is_editable=sources_is_editable,
    )

    return EditorAggregateResponse(
        document=meta,
        content=editor_content,
        original_content=original_content,
        suggestions=suggestions,
        suggestions_total=suggestions_total,  # API-1
        counters=counters,
        permissions=permissions,
    )


# ---------------------------------------------------------------------------
# POST /editor/export?format=docx|md|txt
# ---------------------------------------------------------------------------

@router.post("/export", summary="Экспорт документа с принятыми правками")
async def export_document(
    document_id: uuid.UUID,
    format: Literal["docx", "md", "txt"] = Query(  # noqa: A002
        "docx",
        description="Формат экспорта: docx (по умолчанию), md, txt",
    ),
    project: Project = Depends(get_allowed_project),
    current_user: User = Depends(get_current_user),
    document_service: DocumentService = Depends(get_document_service),
    export_service: DocumentExportService = Depends(get_document_export_service),
) -> Response:
    """Скачать финальный документ с применёнными принятыми правками.

    Поддерживаемые форматы: docx, md, txt.
    Документ должен быть в статусе READY; при других статусах — 422.
    """
    try:
        document = await document_service.get_document(project.id, document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    if document.status != DocumentStatusVO.READY:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Экспорт доступен только для документов в статусе READY. "
                f"Текущий статус: {document.status.value}"
            ),
        )

    ext, media_type = _EXPORT_FORMAT_META[format]
    try:
        file_bytes, filename, resolved_media_type = await export_service.export_document(
            document, target_format=format
        )
    except TypeError:
        file_bytes, filename, resolved_media_type = await export_service.export_document(document)

    stem = document.name.rsplit(".", 1)[0] if "." in document.name else document.name
    download_filename = f"{stem}{ext}"

    return Response(
        content=file_bytes,
        media_type=resolved_media_type or media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{download_filename}"',
        },
    )


# ---------------------------------------------------------------------------
# POST /editor/reset
# API-5: возвращает 200 + ResetResponse вместо 204
# ---------------------------------------------------------------------------

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

    Допустимо только когда документ в статусе AWAITING_APPROVAL или READY.

    API-5: возвращает 200 + ResetResponse вместо 204, чтобы фронт мог
    обновить стор (document_status, review_version, счётчик сброшенных правок)
    без дополнительного GET /editor.
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
    suggestions_reset_count: int = reset_result if isinstance(reset_result, int) else 0

    try:
        updated_document = await document_service.get_document(project.id, document_id)
        new_status = updated_document.status.value
        new_review_version = getattr(updated_document, "review_version", 0) or 0
    except Exception:  # noqa: BLE001
        logger.warning(
            "Не удалось перечитать документ после reset",
            extra={"document_id": str(document_id)},
        )
        new_status = DocumentStatusVO.AWAITING_APPROVAL.value
        new_review_version = 0

    return ResetResponse(
        document_id=document_id,
        document_status=new_status,
        review_version=new_review_version,
        suggestions_reset_count=suggestions_reset_count,
    )


# ---------------------------------------------------------------------------
# DELETE /editor/suggestions — отменить ВСЕ правки документа сразу
# ---------------------------------------------------------------------------

@router.delete(
    "/suggestions",
    response_model=CancelAllSuggestionsResponse,
    status_code=status.HTTP_200_OK,
    responses={
        200: {
            "model": CancelAllSuggestionsResponse,
            "description": "Все pending-правки переведены в rejected",
        },
        404: {"description": "Документ не найден"},
        422: {"description": "Документ не в статусе AWAITING_APPROVAL"},
    },
    summary="Отменить все правки документа",
)
async def cancel_all_suggestions(
    document_id: uuid.UUID,
    project: Project = Depends(get_allowed_project),
    current_user: User = Depends(get_current_user),
    document_service: DocumentService = Depends(get_document_service),
    suggestion_service: SuggestionService = Depends(get_suggestion_service),
) -> CancelAllSuggestionsResponse:
    """Отменить все pending-правки документа одним запросом.

    Переводит все правки со статусом pending в rejected.
    Допустимо только в статусе AWAITING_APPROVAL.

    Используй POST /editor/reset, если хочешь вернуть правки в pending (undoable).
    DELETE /editor/suggestions — деструктивная операция (non-undoable).
    """
    try:
        document = await document_service.get_document(project.id, document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    if document.status != DocumentStatusVO.AWAITING_APPROVAL:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "Отмена всех правок доступна только для документов в статусе "
                f"AWAITING_APPROVAL. Текущий статус: {document.status.value}"
            ),
        )

    cancelled_count = await suggestion_service.cancel_all_pending(
        project.id, document_id, user_id=current_user.id
    )

    return CancelAllSuggestionsResponse(
        document_id=document_id,
        cancelled_count=cancelled_count,
    )
