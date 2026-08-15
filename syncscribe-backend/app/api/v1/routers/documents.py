"""
Загрузка и просмотр документов внутри проекта, привязка источников к документу.
Скачивание исходного файла — через presigned URL; экспорт финального документа (с учётом
принятых правок) — потоково через backend.

Путь в репозитории: app/api/v1/routers/documents.py

ИСПРАВЛЕНО: upload_document не проверял file.filename перед использованием. Если
клиент отправлял multipart без имени файла (или с пустой строкой), FastAPI мог
передать file.filename как None или "", и Path(None).suffix в
DocumentService._resolve_format() падал с TypeError вместо понятного 4xx-ответа.
Теперь отсутствие имени файла возвращает 400 Bad Request на уровне роутера.
"""
import logging
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status

from app.api.deps import get_allowed_project, get_current_user
from app.api.schemas.document import AttachSourcesRequest, DocumentDownloadResponse, DocumentResponse
from app.core.dependencies import (
    get_audit_log_service,
    get_document_export_service,
    get_document_service,
    get_source_service,
)
from app.domain.exceptions import DocumentNotFoundError, FileTooLargeError, SourceNotFoundError, UnsupportedFileFormatError
from app.domain.services.audit_log_service import AuditLogService
from app.domain.services.document_export_service import DocumentExportService
from app.domain.services.document_service import DocumentService
from app.domain.services.source_service import SourceService
from app.infrastructure.db.models.project import Project
from app.infrastructure.db.models.user import User

logger = logging.getLogger("syncscribe.api.documents")

router = APIRouter(prefix="/projects/{project_id}/documents", tags=["documents"])


async def _log_download(audit_log_service: AuditLogService, user_id: uuid.UUID, document_id: uuid.UUID) -> None:
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
) -> DocumentResponse:
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Имя файла обязательно")
    content = await file.read()
    try:
        document = await document_service.upload_document(project, file.filename, content, file.content_type or "application/octet-stream")
    except UnsupportedFileFormatError as exc:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail=str(exc)) from exc
    except FileTooLargeError as exc:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=str(exc)) from exc
    return DocumentResponse.model_validate(document)


@router.get("", response_model=list[DocumentResponse])
async def list_documents(
    project: Project = Depends(get_allowed_project),
    document_service: DocumentService = Depends(get_document_service),
) -> list[DocumentResponse]:
    documents = await document_service.list_documents(project.id)
    return [DocumentResponse.model_validate(d) for d in documents]


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
) -> Response:
    try:
        document = await document_service.get_document(project.id, document_id)
    except DocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    content, filename, media_type = await export_service.export_document(document)
    await _log_download(audit_log_service, current_user.id, document.id)
    return Response(content=content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


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
