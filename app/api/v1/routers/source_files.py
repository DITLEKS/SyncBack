"""Выдача исходного файла источника через API, без публичного адреса хранилища."""

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response

from app.api.deps import get_allowed_project
from app.core.content_disposition import content_disposition
from app.core.dependencies import _get_file_storage, get_source_service
from app.domain.exceptions import SourceNotFoundError, UnsupportedFormatError
from app.domain.services.source_file_service import SourceFileService
from app.domain.services.source_service import SourceService
from app.infrastructure.db.models.project import Project

logger = logging.getLogger("syncscribe.api.source_files")

router = APIRouter(prefix="/projects/{project_id}/sources", tags=["sources"])


def get_source_file_service(
    sources: SourceService = Depends(get_source_service),
) -> SourceFileService:
    return SourceFileService(sources, _get_file_storage())


@router.get(
    "/{source_id}/file",
    summary="Скачать исходный файл источника через API",
    response_class=Response,
    responses={200: {"content": {"application/octet-stream": {}}}},
)
async def download_source_file(
    source_id: uuid.UUID,
    project: Project = Depends(get_allowed_project),
    service: SourceFileService = Depends(get_source_file_service),
) -> Response:
    try:
        source_file = await service.get_file(project.id, source_id)
    except SourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except UnsupportedFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("Не удалось скачать файл источника %s", source_id)
        raise HTTPException(
            status_code=502, detail="Не удалось получить файл источника из хранилища"
        ) from exc
    return Response(
        content=source_file.content,
        media_type="application/octet-stream",
        headers={
            "Content-Disposition": content_disposition(source_file.filename),
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
