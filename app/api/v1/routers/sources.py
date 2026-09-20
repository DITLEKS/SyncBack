"""
Источники истины внутри проекта: текстовая заметка/ссылка через JSON-эндпоинт,
файл — через отдельный multipart-эндпоинт.

Путь в репозитории: app/api/v1/routers/sources.py

ИСПРАВЛЕНО: upload_file_source читает файл через read_upload_within_limit()
чанками вместо полной буферизации через file.read(). list_sources теперь принимает
limit/offset и возвращает Page вместо всего списка целиком.

P0-6: scope пробрасывается из запроса в сервисный слой.
"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status

from app.api.deps import get_allowed_project
from app.api.schemas.pagination import Page
from app.api.schemas.source import SourceCreateRequest, SourceResponse
from app.api.upload_utils import read_upload_within_limit
from app.core.config import Settings, get_settings
from app.core.dependencies import get_source_service
from app.domain.exceptions import FileTooLargeError
from app.domain.services.source_service import SourceService
from app.infrastructure.db.models.enums import SourceType
from app.infrastructure.db.models.project import Project
from app.infrastructure.db.models.source_scope import SourceScope

router = APIRouter(prefix="/projects/{project_id}/sources", tags=["sources"])


@router.post("", response_model=SourceResponse, status_code=status.HTTP_201_CREATED)
async def create_text_source(
    payload: SourceCreateRequest,
    project: Project = Depends(get_allowed_project),
    source_service: SourceService = Depends(get_source_service),
) -> SourceResponse:
    source_type = SourceType.NOTE if payload.type == "note" else SourceType.LINK
    scope = SourceScope(payload.scope)  # P0-6
    source = await source_service.create_text_source(
        project, payload.name, source_type, payload.text_content, payload.url, scope=scope
    )
    return SourceResponse.model_validate(source)


@router.post("/file", response_model=SourceResponse, status_code=status.HTTP_201_CREATED)
async def upload_file_source(
    name: str = Form(..., min_length=1, max_length=255),
    scope: str = Form(default="project"),  # P0-6
    file: UploadFile = File(...),
    project: Project = Depends(get_allowed_project),
    source_service: SourceService = Depends(get_source_service),
    settings: Settings = Depends(get_settings),
) -> SourceResponse:
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Имя файла обязательно")
    if scope not in ("project", "document"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="scope должен быть 'project' или 'document'",
        )
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
            scope=SourceScope(scope),  # P0-6
        )
    except FileTooLargeError as exc:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc
    return SourceResponse.model_validate(source)


@router.get("", response_model=Page[SourceResponse])
async def list_sources(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    project: Project = Depends(get_allowed_project),
    source_service: SourceService = Depends(get_source_service),
) -> Page[SourceResponse]:
    sources, total = await source_service.list_sources(project.id, limit=limit, offset=offset)
    return Page[SourceResponse](
        items=[SourceResponse.model_validate(s) for s in sources], total=total, limit=limit, offset=offset
    )
