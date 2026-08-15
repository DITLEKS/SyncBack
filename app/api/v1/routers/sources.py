"""
Источники истины внутри проекта: текстовая заметка/ссылка через JSON-эндпоинт,
файл — через отдельный multipart-эндпоинт.

Путь в репозитории: app/api/v1/routers/sources.py

ИСПРАВЛЕНО: upload_file_source теперь читает файл через read_upload_within_limit()
чанками вместо полной буферизации через file.read().
"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.api.deps import get_allowed_project
from app.api.schemas.source import SourceCreateRequest, SourceResponse
from app.api.upload_utils import read_upload_within_limit
from app.core.config import Settings, get_settings
from app.core.dependencies import get_source_service
from app.domain.exceptions import FileTooLargeError
from app.domain.services.source_service import SourceService
from app.infrastructure.db.models.enums import SourceType
from app.infrastructure.db.models.project import Project

router = APIRouter(prefix="/projects/{project_id}/sources", tags=["sources"])


@router.post("", response_model=SourceResponse, status_code=status.HTTP_201_CREATED)
async def create_text_source(
    payload: SourceCreateRequest,
    project: Project = Depends(get_allowed_project),
    source_service: SourceService = Depends(get_source_service),
) -> SourceResponse:
    source_type = SourceType.NOTE if payload.type == "note" else SourceType.LINK
    source = await source_service.create_text_source(
        project, payload.name, source_type, payload.text_content, payload.url
    )
    return SourceResponse.model_validate(source)


@router.post("/file", response_model=SourceResponse, status_code=status.HTTP_201_CREATED)
async def upload_file_source(
    name: str = Form(..., min_length=1, max_length=255),
    file: UploadFile = File(...),
    project: Project = Depends(get_allowed_project),
    source_service: SourceService = Depends(get_source_service),
    settings: Settings = Depends(get_settings),
) -> SourceResponse:
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Имя файла обязательно")
    try:
        content = await read_upload_within_limit(file, settings.max_upload_size_bytes)
    except FileTooLargeError as exc:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc
    try:
        source = await source_service.create_file_source(
            project, name, file.filename, content, file.content_type or "application/octet-stream"
        )
    except FileTooLargeError as exc:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc
    return SourceResponse.model_validate(source)


@router.get("", response_model=list[SourceResponse])
async def list_sources(
    project: Project = Depends(get_allowed_project),
    source_service: SourceService = Depends(get_source_service),
) -> list[SourceResponse]:
    sources = await source_service.list_sources(project.id)
    return [SourceResponse.model_validate(s) for s in sources]
