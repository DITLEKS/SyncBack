"""Источники истины внутри проекта.

POST /projects/{id}/sources        — создать URL-источник
POST /projects/{id}/sources/note   — создать текстовую заметку (deprecated)
POST /projects/{id}/sources/file   — загрузить файл
GET  /projects/{id}/sources        — список с пагинацией
DEL  /projects/{id}/sources/{sid}  — удалить источник

Правила «можно ли менять источники документа» живут в SourceService:
роутер только переводит доменные исключения в HTTP-статусы.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status

from app.api.deps import get_allowed_project
from app.api.schemas.pagination import Page
from app.api.schemas.source import NoteCreateRequest, SourceCreateRequest, SourceResponse
from app.api.upload_utils import read_upload_within_limit
from app.core.config import Settings, get_settings
from app.core.dependencies import get_source_service
from app.domain.exceptions import (
    DocumentNotFoundError,
    FileTooLargeError,
    InvalidSourceScopeError,
    InvalidSourceUrlError,
    SourceLockError,
    SourceNotFoundError,
)
from app.domain.services.source_service import SourceService
from app.domain.value_objects import SourceScopeVO
from app.infrastructure.db.models.project import Project

router = APIRouter(prefix="/projects/{project_id}/sources", tags=["sources"])


def _to_http(exc: Exception) -> HTTPException:
    if isinstance(exc, DocumentNotFoundError | SourceNotFoundError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, SourceLockError):
        return HTTPException(status_code=status.HTTP_423_LOCKED, detail=str(exc))
    if isinstance(exc, FileTooLargeError):
        return HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc))
    if isinstance(exc, InvalidSourceScopeError | InvalidSourceUrlError):
        return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    raise exc


_SOURCE_ERRORS = (
    DocumentNotFoundError,
    SourceNotFoundError,
    SourceLockError,
    FileTooLargeError,
    InvalidSourceScopeError,
    InvalidSourceUrlError,
)


@router.post("", response_model=SourceResponse, status_code=status.HTTP_201_CREATED)
async def create_url_source(
    payload: SourceCreateRequest,
    project: Project = Depends(get_allowed_project),
    source_service: SourceService = Depends(get_source_service),
) -> SourceResponse:
    """Создать источник типа URL."""
    try:
        source = await source_service.create_url_source(
            project,
            payload.name,
            payload.url,
            scope=SourceScopeVO(payload.scope),
            document_id=payload.document_id,
        )
    except _SOURCE_ERRORS as exc:
        raise _to_http(exc) from exc
    return SourceResponse.model_validate(source)


@router.post(
    "/note",
    response_model=SourceResponse,
    status_code=status.HTTP_201_CREATED,
    deprecated=True,
)
async def create_note_source(
    payload: NoteCreateRequest,
    project: Project = Depends(get_allowed_project),
    source_service: SourceService = Depends(get_source_service),
) -> SourceResponse:
    """Создать текстовую заметку. Текст сохраняется как .txt-файл, type в ответе — file.

    Эндпоинт внутренний; для внешних клиентов — POST /sources/file.
    """
    try:
        source = await source_service.create_note_source(
            project,
            payload.name,
            payload.text_content,
            scope=SourceScopeVO(payload.scope),
            document_id=payload.document_id,
        )
    except _SOURCE_ERRORS as exc:
        raise _to_http(exc) from exc
    return SourceResponse.model_validate(source)


@router.post("/file", response_model=SourceResponse, status_code=status.HTTP_201_CREATED)
async def upload_file_source(
    name: str = Form(..., min_length=1, max_length=255),
    scope: SourceScopeVO = Form(default=SourceScopeVO.PROJECT),
    document_id: uuid.UUID | None = Form(
        default=None, description="UUID документа (обязателен при scope=document)"
    ),
    file: UploadFile = File(...),
    project: Project = Depends(get_allowed_project),
    source_service: SourceService = Depends(get_source_service),
    settings: Settings = Depends(get_settings),
) -> SourceResponse:
    """Загрузить файловый источник."""
    try:
        content = await read_upload_within_limit(file, settings.max_upload_size_bytes)
        source = await source_service.create_file_source(
            project,
            name=name,
            filename=file.filename or "upload",
            content=content,
            content_type=file.content_type or "application/octet-stream",
            scope=scope,
            document_id=document_id,
        )
    except _SOURCE_ERRORS as exc:
        raise _to_http(exc) from exc
    return SourceResponse.model_validate(source)


@router.get("", response_model=Page[SourceResponse])
async def list_sources(
    project: Project = Depends(get_allowed_project),
    source_service: SourceService = Depends(get_source_service),
    scope: SourceScopeVO | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> Page[SourceResponse]:
    """Список источников проекта с пагинацией; ?scope= фильтрует по области."""
    items, total = await source_service.list_sources(
        project.id, limit=limit, offset=offset, scope=scope
    )
    return Page(
        items=[SourceResponse.model_validate(s) for s in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(
    source_id: uuid.UUID,
    project: Project = Depends(get_allowed_project),
    source_service: SourceService = Depends(get_source_service),
) -> None:
    """Удалить источник. 423, если он прикреплён к документу на анализе или ревью."""
    try:
        await source_service.delete_source(project.id, source_id)
    except _SOURCE_ERRORS as exc:
        raise _to_http(exc) from exc
