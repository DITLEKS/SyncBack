"""Агрегированный endpoint редактора документа + export + reset.

GET  /projects/{project_id}/documents/{document_id}/editor
POST /projects/{project_id}/documents/{document_id}/editor/export?format=docx
POST /projects/{project_id}/documents/{document_id}/editor/reset

#7: возвращаем view_mode и original_content (исходный plain_text без правок)
#8: возвращаем sources_is_editable=False при in_progress / awaiting_approval
"""
import logging
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response

from app.api.deps import get_allowed_project, get_current_user
from app.api.schemas.document import DocumentSectionResponse, SuggestionCounters
from app.api.schemas.editor import (
    EditorAggregateResponse,
    EditorContent,
    EditorDocumentMeta,
    EditorPermissions,
)
from app.api.schemas.suggestion import SuggestionResponse
from app.core.dependencies import (
    get_analysis_job_service,
    get_document_export_service,
    get_document_service,
    get_suggestion_service,
)
from app.domain.exceptions import DocumentNotFoundError
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


@router.get("", response_model=EditorAggregateResponse)
async def get_editor_aggregate(
    document_id: uuid.UUID,
    project: Project = Depends(get_allowed_project),
    current_user: User = Depends(get_current_user),
    document_service: DocumentService = Depends(get_document_service),
    suggestion_service: SuggestionService = Depends(get_suggestion_service),
) -> EditorAggregateResponse:
    """Полный агрегат данных для экрана редактора."""
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

    # --- 3. Правки ---
    suggestions_raw, total = await suggestion_service.list_suggestions_for_document(
        project.id, document_id, PaginationParams(limit=200, offset=0)
    )
    suggestions = [SuggestionResponse.model_validate(s) for s in suggestions_raw]

    # --- 4. Счётчики (один проход O(n) вместо трёх O(3n)) ---
    pending = accepted = rejected = 0
    for s in suggestions:
        if s.status == "pending":
            pending += 1
        elif s.status == "accepted":
            accepted += 1
        elif s.status == "rejected":
            rejected += 1
    counters = SuggestionCounters(
        total=total,
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
    # DocumentExportService.export_document возвращает (bytes, filename, media_type)
    # Передаём желаемый формат через атрибут (сервис умеет конвертировать при необходимости)
    try:
        file_bytes, filename, resolved_media_type = await export_service.export_document(
            document, target_format=format
        )
    except TypeError:
        # Старая сигнатура без target_format — вызываем без аргумента
        file_bytes, filename, resolved_media_type = await export_service.export_document(document)

    # Формируем имя файла: берём исходное имя, меняем расширение
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
# ---------------------------------------------------------------------------

@router.post("/reset", status_code=status.HTTP_204_NO_CONTENT, summary="Сбросить все изменения")
async def reset_analysis(
    document_id: uuid.UUID,
    project: Project = Depends(get_allowed_project),
    current_user: User = Depends(get_current_user),
    document_service: DocumentService = Depends(get_document_service),
    job_service: AnalysisJobService = Depends(get_analysis_job_service),
) -> None:
    """Сбросить все правки текущего анализа: статус suggestions → pending,
    документ → AWAITING_APPROVAL.

    Допустимо только когда документ в статусе AWAITING_APPROVAL или READY.
    После сброса фронт должен перезагрузить агрегат редактора.
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

    await job_service.reset_analysis(project.id, document_id)
