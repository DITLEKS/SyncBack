"""Откат файла из хранилища, если запись в БД при загрузке не удалась."""

import uuid
from unittest.mock import AsyncMock

import pytest

from app.domain.interfaces.file_storage import UploadContent
from app.domain.policies import UploadLimits
from app.domain.services.document_service import DocumentService
from app.domain.services.source_service import SourceService
from app.infrastructure.db.models.project import Project
from app.infrastructure.db.unit_of_work import SqlAlchemyUnitOfWork
from app.infrastructure.parsers.parser_registry import DocumentParserRegistry
from app.infrastructure.storage.minio_storage import MinioStorage

_LIMITS = UploadLimits.from_megabytes(50)


@pytest.mark.asyncio
async def test_document_upload_deletes_orphan_file_on_db_failure(
    db_session, minio_storage: MinioStorage, monkeypatch
):
    uow = SqlAlchemyUnitOfWork(db_session)
    service = DocumentService(
        uow,
        minio_storage,
        parser_registry=DocumentParserRegistry(),
        upload_limits=_LIMITS,
        download_url_ttl_seconds=300,
    )
    project = Project(
        id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        name="rollback-project",
        owner_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
    )
    expected_document_id = uuid.UUID("33333333-3333-3333-3333-333333333333")
    monkeypatch.setattr(
        "app.domain.services.document_service.uuid.uuid4", lambda: expected_document_id
    )
    uow.documents.create = AsyncMock(side_effect=Exception("DB create failed"))

    with pytest.raises(Exception, match="DB create failed"):
        await service.upload_document(
            project.id, "document.txt", UploadContent.from_bytes(b"hello world", "text/plain")
        )

    storage_key = f"projects/{project.id}/documents/{expected_document_id}/document.txt"
    assert not await minio_storage.exists(storage_key)


@pytest.mark.asyncio
async def test_source_upload_deletes_orphan_file_on_db_failure(
    db_session, minio_storage: MinioStorage, monkeypatch
):
    uow = SqlAlchemyUnitOfWork(db_session)
    service = SourceService(uow, minio_storage, upload_limits=_LIMITS)
    project = Project(
        id=uuid.UUID("44444444-4444-4444-4444-444444444444"),
        name="rollback-source-project",
        owner_id=uuid.UUID("55555555-5555-5555-5555-555555555555"),
    )
    expected_source_id = uuid.UUID("66666666-6666-6666-6666-666666666666")
    monkeypatch.setattr("app.domain.services.source_service.uuid.uuid4", lambda: expected_source_id)
    uow.sources.create_with_id = AsyncMock(side_effect=Exception("DB create failed"))

    with pytest.raises(Exception, match="DB create failed"):
        await service.create_file_source(
            project,
            "Test source",
            "upload.txt",
            UploadContent.from_bytes(b"hello world", "text/plain"),
        )

    storage_key = f"projects/{project.id}/sources/{expected_source_id}/upload.txt"
    assert not await minio_storage.exists(storage_key)
