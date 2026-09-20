"""
Источники истины внутри проекта: текстовая заметка/ссылка через JSON-эндпоинт,
файл — через отдельный multipart-эндпоинт.

P0-6: scope пробрасывается из запроса в сервисный слой.
P0-6: guard активного анализа — если для документа есть активный job,
      изменение источников запрещено (HTTP 423 Locked).
"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status

from app.api.deps import get_allowed_project
from app.api.schemas.pagination import Page
from app.api.schemas.source import SourceCreateRequest, SourceResponse
from app.api.upload_utils import read_upload_within_limit
from app.core.config import Settings, get_settings
from app.core.dependencies import get_analysis_job_service, get_source_service
from app.domain.exceptions import FileTooLargeError
from app.domain.services.analysis_job_service import AnalysisJobService
from app.domain.services.source_service import SourceService
from app.infrastructure.db.models.enums import SourceType
from app.infrastructure.db.models.project import Project
from app.infrastructure.db.models.source_scope import SourceScope

router = APIRouter(prefix="/projects/{project_id}/sources", tags=["sources"])


async def _guard_no_active_job(
    project_id,
    document_id,
    job_service: AnalysisJobService,
) -> None:
    """P0-6: выбрасывает 423 если у документа есть активный анализ."""
    if document_id is None:
        return
    active = await job_service.get_active_for_document(project_id, document_id)
    if active is not None:
        raise HTTPException(
            status_code=status.HTTP_423_LOCKED,
            detail="Нельзя изменять источники пока идёт анализ документа. "
                   "Дождитесь завершения задания или отмените его.",
        )


@router.post("", response_model=SourceResponse, status_code=status.HTTP_201_CREATED)
async def create_text_source(
    payload: SourceCreateRequest,
    project: Project = Depends(get_allowed_project),
    source_service: SourceService = Depends(get_source_service),
    job_service: AnalysisJobService = Depends(get_analysis_job_service),
) -> SourceResponse:
    # P0-6 guard: scope=document подразумевает конкретный document_id в payload
    await _guard_no_active_job(
        project.id,
        getattr(payload, "document_id", None),
        job_service,
    )
    source_type = SourceType.NOTE if payload.type == "note" else SourceType.LINK
    scope = SourceScope(payload.scope)
    source = await source_service.create_text_source(
        project, payload.name, source_type, payload.text_content, payload.url, scope=scope
    )
    return SourceResponse.model_validate(source)


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

    import uuid as _uuid  # noqa: PLC0415
    parsed_doc_id = _uuid.UUID(document_id) if document_id else None

    # P0-6 guard
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
            scope=SourceScope(scope),
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


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_source(
    source_id: str,
    project: Project = Depends(get_allowed_project),
    source_service: SourceService = Depends(get_source_service),
    job_service: AnalysisJobService = Depends(get_analysis_job_service),
) -> None:
    """P0-6: проверяет активный job перед удалением источника."""
    import uuid as _uuid  # noqa: PLC0415
    # Получаем source, чтобы узнать document_id (для scope=document)
    src = await source_service.get_source(project.id, _uuid.UUID(source_id))
    doc_id = getattr(src, "document_id", None)
    await _guard_no_active_job(project.id, doc_id, job_service)
    await source_service.delete_source(project.id, _uuid.UUID(source_id))
