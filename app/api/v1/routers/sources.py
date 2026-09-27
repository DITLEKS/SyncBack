"""
Источники истины внутри проекта.

POST /projects/{id}/sources        — создать URL-источник
POST /projects/{id}/sources/note   — создать текстовую заметку
POST /projects/{id}/sources/file   — загрузить файл
GET  /projects/{id}/sources        — список с пагинацией
DEL  /projects/{id}/sources/{sid}  — удалить источник

OPT-S2: delete_source — убран лишний get_source() перед удалением (N+1).
OPT-S3: _guard_no_active_job не вызывается при scope=project (document_id=None).
OPT-S6: scope в upload_file_source — typed SourceScopeVO Form, автовалидация FastAPI.
OPT-S7: source_id — uuid.UUID вместо str (автопарсинг FastAPI).
WARN-2: create_url_source и create_note_source передают document_id=payload.document_id
        в сервис — M2M-вставка при scope=document теперь корректна.
"""
from __future__ import annotations

import uuid as _uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status

from app.api.deps import get_allowed_project
from app.api.schemas.pagination import Page
from app.api.schemas.source import NoteCreateRequest, SourceCreateRequest, SourceResponse
from app.api.upload_utils import read_upload_within_limit
from app.core.config import Settings, get_settings
from app.core.dependencies import get_analysis_job_service, get_source_service
from app.domain.exceptions import FileTooLargeError, SourceNotFoundError
from app.domain.services.analysis_job_service import AnalysisJobService
from app.domain.services.source_service import SourceService
from app.domain.value_objects import SourceScopeVO
from app.infrastructure.db.models.project import Project

router = APIRouter(prefix="/projects/{project_id}/sources", tags=["sources"])


async def _guard_no_active_job(
    project_id: _uuid.UUID,
    document_id: _uuid.UUID | None,
    job_service: AnalysisJobService,
) -> None:
    """423 если у документа есть активный анализ.

    OPT-S3: при document_id=None (scope=project) ранний return;
    Depends(get_analysis_job_service) резолвится только когда нужен.
    """
    if document_id is None:
        return
    active = await job_service.get_active_for_document(project_id, document_id)
    if active is not None:
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail=(
                "Нельзя изменять источники пока идёт анализ документа. "
                "Дождитесь завершения задания или отмените его."
            ),
        )


# ------------------------------------------------------------------
# URL-источник
# ------------------------------------------------------------------

@router.post("", response_model=SourceResponse, status_code=status.HTTP_201_CREATED)
async def create_url_source(
    payload: SourceCreateRequest,
    project: Project = Depends(get_allowed_project),
    source_service: SourceService = Depends(get_source_service),
    job_service: AnalysisJobService = Depends(get_analysis_job_service),
) -> SourceResponse:
    """Создать источник типа URL."""
    doc_id: _uuid.UUID | None = getattr(payload, "document_id", None)
    await _guard_no_active_job(project.id, doc_id, job_service)
    scope = SourceScopeVO(payload.scope)
    source = await source_service.create_url_source(
        project,
        payload.name,
        payload.url,
        scope=scope,
        document_id=doc_id,  # WARN-2: передаём document_id для M2M-вставки
    )
    return SourceResponse.model_validate(source)


# ------------------------------------------------------------------
# Текстовая заметка (P2: сохраняется как .txt в MinIO)
# ------------------------------------------------------------------

@router.post("/note", response_model=SourceResponse, status_code=status.HTTP_201_CREATED)
async def create_note_source(
    payload: NoteCreateRequest,
    project: Project = Depends(get_allowed_project),
    source_service: SourceService = Depends(get_source_service),
    job_service: AnalysisJobService = Depends(get_analysis_job_service),
) -> SourceResponse:
    """Создать текстовую заметку. Текст сохраняется в MinIO как .txt-файл."""
    doc_id: _uuid.UUID | None = getattr(payload, "document_id", None)
    await _guard_no_active_job(project.id, doc_id, job_service)
    scope = SourceScopeVO(payload.scope)
    source = await source_service.create_note_source(
        project,
        payload.name,
        payload.text_content,
        scope=scope,
        document_id=doc_id,  # WARN-2: передаём document_id для M2M-вставки
    )
    return SourceResponse.model_validate(source)


# ------------------------------------------------------------------
# Файловый источник
# ------------------------------------------------------------------

@router.post("/file", response_model=SourceResponse, status_code=status.HTTP_201_CREATED)
async def upload_file_source(
    name: str = Form(..., min_length=1, max_length=255),
    # OPT-S6: FastAPI автоматически валидирует scope через SourceScopeVO enum
    scope: SourceScopeVO = Form(default=SourceScopeVO.PROJECT),
    document_id: str | None = Form(
        default=None,
        description="UUID документа (обязателен при scope=document)",
    ),
    file: UploadFile = File(...),
    project: Project = Depends(get_allowed_project),
    source_service: SourceService = Depends(get_source_service),
    job_service: AnalysisJobService = Depends(get_analysis_job_service),
    settings: Settings = Depends(get_settings),
) -> SourceResponse:
    """Загрузить файловый источник."""
    parsed_doc_id: _uuid.UUID | None = (
        _uuid.UUID(document_id) if document_id else None
    )
    await _guard_no_active_job(project.id, parsed_doc_id, job_service)
    content = await read_upload_within_limit(file, settings.max_upload_size_bytes)
    try:
        source = await source_service.create_file_source(
            project,
            name=name,
            filename=file.filename or "upload",
            content=content,
            content_type=file.content_type or "application/octet-stream",
            scope=scope,
            document_id=parsed_doc_id,  # WARN-2: передаём document_id для M2M-вставки
        )
    except FileTooLargeError as exc:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc))
    return SourceResponse.model_validate(source)


# ------------------------------------------------------------------
# List
# ------------------------------------------------------------------

@router.get("", response_model=Page[SourceResponse])
async def list_sources(
    project: Project = Depends(get_allowed_project),
    source_service: SourceService = Depends(get_source_service),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> Page[SourceResponse]:
    """Список источников проекта с пагинацией."""
    items, total = await source_service.list_sources(project.id, limit=limit, offset=offset)
    return Page(
        items=[SourceResponse.model_validate(s) for s in items],
        total=total,
        limit=limit,
        offset=offset,
    )


# ------------------------------------------------------------------
# Delete
# ------------------------------------------------------------------

@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(
    source_id: _uuid.UUID,
    project: Project = Depends(get_allowed_project),
    source_service: SourceService = Depends(get_source_service),
    job_service: AnalysisJobService = Depends(get_analysis_job_service),
) -> None:
    """Удалить источник.

    OPT-S2: источник не загружается предварительно — delete_source_with_guard
    делает одну атомарную операцию (проверка + DELETE).
    OPT-S3 / FIX-1: guard вызывается с document_id=None → ранний return.
    """
    await _guard_no_active_job(project.id, None, job_service)
    try:
        await source_service.delete_source_with_guard(
            project_id=project.id,
            source_id=source_id,
        )
    except SourceNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
