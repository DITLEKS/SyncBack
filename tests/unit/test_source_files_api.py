"""HTTP-контракт скачивания источников через API вместо внутренней S3-ссылки."""

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from app.api.deps import get_allowed_project
from app.api.v1.routers.source_files import get_source_file_service, router
from app.domain.exceptions import SourceNotFoundError, UnsupportedFormatError
from app.domain.services.source_file_service import SourceFile

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def file_api():
    project_id, source_id = uuid.uuid4(), uuid.uuid4()
    service = SimpleNamespace(
        get_file=AsyncMock(return_value=SourceFile(b"original", "Решение.docx"))
    )
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    app.dependency_overrides[get_allowed_project] = lambda: SimpleNamespace(id=project_id)
    app.dependency_overrides[get_source_file_service] = lambda: service
    path = f"/api/v1/projects/{project_id}/sources/{source_id}/file"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield app, client, service, path, project_id, source_id


async def test_download_returns_bytes_and_attachment_filename(file_api):
    _, client, service, path, project_id, source_id = file_api

    response = await client.get(path)

    assert response.status_code == 200
    assert response.content == b"original"
    assert response.headers["content-type"] == "application/octet-stream"
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "filename*=UTF-8''" in disposition
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "location" not in response.headers
    service.get_file.assert_awaited_once_with(project_id, source_id)


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (SourceNotFoundError("Не найден"), 404),
        (UnsupportedFormatError("Это ссылка"), 422),
        (OSError("storage unavailable"), 502),
    ],
)
async def test_download_errors(file_api, error, status):
    _, client, service, path, _, _ = file_api
    service.get_file.side_effect = error

    response = await client.get(path)

    assert response.status_code == status
    assert "detail" in response.json()
    if status == 502:
        assert "storage unavailable" not in response.text


async def test_project_access_is_checked_before_file_service(file_api):
    app, client, service, path, _, _ = file_api

    def deny_project():
        raise HTTPException(status_code=404, detail="Проект не найден")

    app.dependency_overrides[get_allowed_project] = deny_project
    response = await client.get(path)

    assert response.status_code == 404
    service.get_file.assert_not_awaited()


async def test_file_route_is_registered_in_application():
    from app.main import app

    path = "/api/v1/projects/{project_id}/sources/{source_id}/file"
    assert "get" in app.openapi()["paths"][path]
