"""
P0-#6: Загрузка документа с выбором проекта из «Мои документы».

POST /documents — multipart upload с обязательным полем project_id.
Это дополнение к POST /projects/{project_id}/documents (для загрузки
из контекста конкретного проекта). Здесь пользователь выбирает проект
из выпадающего списка на экране «Мои документы».
"""
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.api.deps import get_current_user
from app.api.schemas.document import DocumentResponse
from app.api.upload_utils import read_upload_within_limit
from app.core.config import Settings, get_settings
from app.core.dependencies import get_document_service, get_project_service
from app.domain.exceptions import (
    FileTooLargeError,
    ProjectNotFoundError,
    UnsupportedFileFormatError,
)
from app.domain.services.document_service import DocumentService
from app.domain.services.project_service import ProjectService
from app.infrastructure.db.models.user import User

router = APIRouter(prefix="/documents", tags=["my-documents"])


@router.post("", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def upload_document_global(
    project_id: uuid.UUID = Form(..., description="UUID проекта, в который загружается документ"),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    document_service: DocumentService = Depends(get_document_service),
    project_service: ProjectService = Depends(get_project_service),
    settings: Settings = Depends(get_settings),
) -> DocumentResponse:
    """Загрузка документа из экрана «Мои документы».
    Пользователь выбирает проект явно через поле project_id.

    Ошибки:
    - 400 — имя файла не указано
    - 403 — project_id не принадлежит текущему пользователю
    - 413 — файл превышает допустимый размер
    - 415 — формат файла не поддерживается (.doc, неизвестные расширения)
    """
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Имя файла обязательно",
        )

    # Проверяем, что проект принадлежит текущему пользователю
    try:
        project = await project_service.get_project_for_user(project_id, current_user)
    except ProjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Проект не найден или недоступен: {exc}",
        ) from exc

    try:
        content = await read_upload_within_limit(file, settings.max_upload_size_bytes)
    except FileTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=str(exc),
        ) from exc

    try:
        document = await document_service.upload_document(
            project,
            file.filename,
            content,
            file.content_type or "application/octet-stream",
        )
    except UnsupportedFileFormatError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=str(exc),
        ) from exc
    except FileTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=str(exc),
        ) from exc

    return DocumentResponse.model_validate(document)
