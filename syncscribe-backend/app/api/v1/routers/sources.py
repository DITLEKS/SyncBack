"""
Источники истины внутри проекта: текстовая заметка/ссылка через JSON-эндпоинт,
файл — через отдельный multipart-эндпоинт (разные типы контента запроса не смешиваем в одном роутере).
"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.api.deps import get_allowed_project
from app.api.schemas.source import SourceCreateRequest, SourceResponse
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
) -> SourceResponse:
    content = await file.read()
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
