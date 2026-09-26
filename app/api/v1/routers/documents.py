"""
Загрузка и просмотр документов внутри проекта, привязка источников к документу.

ДОБАВЛЕНО:
- DELETE /{document_id} — удаление документа + MinIO-файл + каскад suggestions/jobs.
- GET /{document_id}/export?format=md|docx|txt — экспорт в конкретный формат (макет).
ОПТИМИЗИРОВАНО (PERF-4):
- list_documents: TypeAdapter для пакетной сериализации вместо N model_validate.
"""
import logging
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile, status
from pydantic import TypeAdapter

from app.api.deps import get_allowed_project, get_current_user
from app.api.schemas.document import (
    AttachSourcesRequest,
    DocumentContentResponse,
    DocumentDownloadResponse,
    DocumentResponse,
    DocumentSectionResponse,
)
from app.api.schemas.pagination import Page
from app.api.upload_utils import read_upload_within_limit
from app.core.config import Settings, get_settings
from app.core.dependencies import (
    get_audit_log_service,
    get_document_export_service,
    get_document_service,
    get_source_service,
)
from app.domain.exceptions import (
    DocumentNotFoundError,
    FileTooLargeError,
    SourceNotFoundError,
    UnsupportedFileFormatError,
)
from app.domain.services.audit_log_service import AuditLogService
from app.domain.services.document_export_service import DocumentExportService
from app.domain.services.document_service import DocumentService
from app.domain.services.source_service import SourceService
from app.domain.value_objects import DocumentFormatVO, PaginationParams
from app.infrastructure.db.models.project import Project
from app.infrastructure.db.models.user import User

logger = logging.getLogger("syncscribe.api.documents")

# PERF-4: один TypeAdapter на уровне модуля — единый проход по списку
# вместо N отдельных model_validate.
_document_list_adapter: TypeAdapter[list[DocumentResponse]] = TypeAdapter(
    list[DocumentResponse]
)

# Маппинг query-параметра ?format= → DocumentFormatVO.
# Только форматы, поддерживаемые экспортёром; doc намеренно исключён —
# legacy .doc нельзя сгенерировать (только читать).
_EXPORT_FORMAT_MAP: dict[str, DocumentFormatVO] = {
    "md":   DocumentFormatVO.MARKDOWN,
    "docx": DocumentFormatVO.DOCX,
    "txt":  DocumentFormatVO.TXT,
}

router = APIRouter(prefix="/projects/{project_id}/documents", tags=["documents"])


async def _log_download(
    audit_log_service: AuditLogService,
    user_id: uuid.UUID,
    document_id: uuid.UUID,
) -> None:
    try:
        await audit_log_service.log_download(user_id, document_id)
    except Exception:
        logger.exception(
            "Failed to write audit_log for document download",
            extra={"document_id": str(document_id), "user_id": str(user_id)},
        )


@router.post("", response_model=DocumentResponse, status_code=status.HTTP_201_CREATED)
async def upload_document(
    file: UploadFile = File(...),
    project: Project = Depends(get_allowed_project),
    document_service: DocumentService = Depends(get_document_service),
    settings: Settings = Depends(get_settings),
) -> DocumentResponse:
    if not file.filename:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Имя файла обязательно"
        )
    try:
        content = await read_upload_within_limit(file, settings.max_upload_size_bytes)
    except FileTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)
        ) from exc
    try:
        document = await document_service.upload_document(
            project, file.filename, content, file.content_type or "application/octet-stream"
        )
    except UnsupportedFileFormatError as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)
        ) from exc
    except FileTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)
        ) from exc
    return DocumentResponse.model_validate(document)


@router.get("", response_model=Page[DocumentResponse])
async def list_documents(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    project: Project = Depends(get_allowed_project),
    document_service: DocumentService = Depends(get_document_service),
) -> Page[DocumentResponse]:
    pagination = PaginationParams(limit=limit, offset=offset)
    documents, total = await document_service.list_documents(project.id, pagination)
    return Page[DocumentResponse](
        items=_document_list_adapter.validate_python(documents, from_attributes=True),
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: uuid.UUID,
    project: Project = Depends(get_allowed_project),
    document_service: DocumentService = Depends(get_document_service),
) -> DocumentResponse:
    try:
        document = await document_service.get_document(project.id, document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return DocumentResponse.model_validate(document)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: uuid.UUID,
    project: Project = Depends(get_allowed_project),
    document_service: DocumentService = Depends(get_document_service),
) -> None:
    """Удаление документа: MinIO-файл + каскад БД (suggestions, analysis_jobs, document_sources)."""
    try:
        document = await document_service.get_document(project.id, document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await document_service.delete_document(document)


@router.get("/{document_id}/content", response_model=DocumentContentResponse)
async def get_document_content(
    document_id: uuid.UUID,
    project: Project = Depends(get_allowed_project),
    document_service: DocumentService = Depends(get_document_service),
) -> DocumentContentResponse:
    try:
        document = await document_service.get_document(project.id, document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    try:
        parsed = await document_service.get_document_content(document)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Не удалось распарсить документ: {exc}",
        ) from exc
    return DocumentContentResponse(
        plain_text=parsed.plain_text,
        sections=[
            DocumentSectionResponse(
                ref=s.ref, start_offset=s.start_offset, end_offset=s.end_offset
            )
            for s in parsed.sections
        ],
    )


@router.get("/{document_id}/download", response_model=DocumentDownloadResponse)
async def get_download_url(
    document_id: uuid.UUID,
    project: Project = Depends(get_allowed_project),
    current_user: User = Depends(get_current_user),
    document_service: DocumentService = Depends(get_document_service),
    audit_log_service: AuditLogService = Depends(get_audit_log_service),
) -> DocumentDownloadResponse:
    try:
        document = await document_service.get_document(project.id, document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    url, expires_in = await document_service.get_download_url(document)
    await _log_download(audit_log_service, current_user.id, document.id)
    return DocumentDownloadResponse(download_url=url, expires_in=expires_in)


@router.get("/{document_id}/export")
async def export_document(
    document_id: uuid.UUID,
    project: Project = Depends(get_allowed_project),
    current_user: User = Depends(get_current_user),
    document_service: DocumentService = Depends(get_document_service),
    export_service: DocumentExportService = Depends(get_document_export_service),
    audit_log_service: AuditLogService = Depends(get_audit_log_service),
    format: str | None = Query(
        default=None,
        description="Целевой формат экспорта: md, docx, txt. "
                    "По умолчанию используется исходный формат документа.",
    ),
) -> Response:
    """Экспорт документа с применёнными правками.

    ?format=md|docx|txt — переопределяет формат вывода.
    Если format не указан, экспорт возвращается в исходном формате документа.
    Неизвестный format → 400 Bad Request.
    """
    target_format: DocumentFormatVO | None = None
    if format is not None:
        target_format = _EXPORT_FORMAT_MAP.get(format.lower())
        if target_format is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Неподдерживаемый формат экспорта: {format!r}. "
                    f"Допустимые значения: {', '.join(_EXPORT_FORMAT_MAP)}"
                ),
            )

    try:
        document = await document_service.get_document(project.id, document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    content, filename, media_type = await export_service.export_document(
        document, target_format=target_format
    )
    await _log_download(audit_log_service, current_user.id, document.id)
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{document_id}/sources", response_model=DocumentResponse)
async def attach_sources(
    document_id: uuid.UUID,
    payload: AttachSourcesRequest,
    project: Project = Depends(get_allowed_project),
    document_service: DocumentService = Depends(get_document_service),
    source_service: SourceService = Depends(get_source_service),
) -> DocumentResponse:
    try:
        document = await document_service.get_document(project.id, document_id)
        sources = await source_service.get_sources_for_project(project.id, payload.source_ids)
    except (DocumentNotFoundError, SourceNotFoundError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    document = await document_service.attach_sources(document, sources)
    return DocumentResponse.model_validate(document)
