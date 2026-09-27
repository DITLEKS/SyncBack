"""
Источники истины внутри проекта.

POST /projects/{id}/sources        — создать URL-источник (SourceCreateRequest)
POST /projects/{id}/sources/note   — создать текстовую заметку (NoteCreateRequest, P2)
POST /projects/{id}/sources/file   — загрузить файл (multipart)
GET  /projects/{id}/sources        — список с пагинацией
DEL  /projects/{id}/sources/{sid}  — удалить источник
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
from app.domain.exceptions import FileTooLargeError
from app.domain.services.analysis_job_service import AnalysisJobService
from app.domain.services.source_service import SourceService
from app.domain.value_objects import SourceScopeVO
from app.infrastructure.db.models.project import Project

router = APIRouter(prefix="/projects/{project_id}/sources", tags=["sources"])


async def _guard_no_active_job(
    project_id,
    document_id,
    job_service: AnalysisJobService,
) -> None:
    """423 если у документа есть активный анализ."""
    if document_id is None:
        return
    active = await job_service.get_active_for_document(project_id, document_id)
    if active is not None:
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="Нельзя изменять источники пока идёт анализ документа. "
                   "Дождитесь завершения задания или отмените его.",
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
    await _guard_no_active_job(
        project.id,
        getattr(payload, "document_id", None),
        job_service,
    )
    scope = SourceScopeVO(payload.scope)
    source = await source_service.create_url_source(
        project, payload.name, payload.url, scope=scope
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
    await _guard_no_active_job(
        project.id,
        getattr(payload, "document_id", None),
        job_service,
    )
    scope = SourceScopeVO(payload.scope)
    source = await source_service.create_note_source(
        project, payload.name, payload.text_content, scope=scope
    )
    return SourceResponse.model_validate(source)


# ------------------------------------------------------------------
# Файловый источник
# ------------------------------------------------------------------

@router.post("/file", response_model=SourceResponse, status_code=status.HTTP_201_CREATED)
async def upload_file_source(
    name: str = Form(..., min_length=1, max_length=255),
    scope: str = Form(default="project"),
    document_id: str = Form(default=None, description="UUID документа (обязателен при scope=document)"),
    file: UploadFile = File(...),
    project: Project = Depends(get_allowed_project),
    source_service: SourceService = Depends(get_source_service),
    job_service: AnalysisJobService = Depends(get_analysis_job_service),
    settings: Settings = Depends(get_settings),
) -> SourceResponse:
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Имя файла обязательно")
    if scope not in ("project", "document"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="scope должен быть 'project' или 'document'",
        )
    if scope == "document" and not document_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="document_id обязателен при scope=document",
        )

    parsed_doc_id = _uuid.UUID(document_id) if document_id else None
    await _guard_no_active_job(project.id, parsed_doc_id, job_service)

    try:
        content = await read_upload_within_limit(file, settings.max_upload_size_bytes)
    except FileTooLargeError as exc:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc
    try:
        source = await source_service.create_file_source(
            project,
            name,
            file.filename,
            content,
            file.content_type or "application/octet-stream",
            scope=SourceScopeVO(scope),
        )
    except FileTooLargeError as exc:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc
    return SourceResponse.model_validate(source)


# ------------------------------------------------------------------
# List
# ------------------------------------------------------------------

@router.get("", response_model=Page[SourceResponse])
async def list_sources(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    project: Project = Depends(get_allowed_project),
    source_service: SourceService = Depends(get_source_service),
) -> Page[SourceResponse]:
    sources, total = await source_service.list_sources(project.id, limit=limit, offset=offset)
    return Page[SourceResponse](
        items=[SourceResponse.model_validate(s) for s in sources],
        total=total, limit=limit, offset=offset,
    )


# ------------------------------------------------------------------
# Delete
# ------------------------------------------------------------------

@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(
    source_id: str,
    project: Project = Depends(get_allowed_project),
    source_service: SourceService = Depends(get_source_service),
    job_service: AnalysisJobService = Depends(get_analysis_job_service),
) -> None:
    src = await source_service.get_source(project.id, _uuid.UUID(source_id))
    doc_id = getattr(src, "document_id", None)
    await _guard_no_active_job(project.id, doc_id, job_service)
    await source_service.delete_source(project.id, _uuid.UUID(source_id))
