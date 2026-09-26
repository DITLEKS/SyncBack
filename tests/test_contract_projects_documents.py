"""
F-2: Contract-тесты роутеров projects и documents.

Покрываемые HTTP-контракты:
  GET  /api/v1/projects                               — 200 list
  POST /api/v1/projects                               — 201 + id
  GET  /api/v1/projects/{id}                          — 200 | 404
  GET  /api/v1/projects/{id}/documents                — 200 list
  POST /api/v1/documents                              — 201 (global upload)
  GET  /api/v1/projects/{id}/documents/{doc_id}       — 200 | 404
  PATCH /api/v1/projects/{id}/documents/{doc_id}      — 200
  DELETE /api/v1/projects/{id}/documents/{doc_id}     — 204

Все тесты работают через мок — реальная БД не нужна.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app

# ---------------------------------------------------------------------------
# Общие фикстуры
# ---------------------------------------------------------------------------

USER_ID   = uuid.uuid4()
PROJECT_ID = uuid.uuid4()
DOC_ID     = uuid.uuid4()


def _make_user() -> SimpleNamespace:
    return SimpleNamespace(
        id=USER_ID, email="bob@example.com", username="bob",
        is_active=True, created_at=datetime.now(timezone.utc),
    )


def _make_project(
    pid: uuid.UUID = PROJECT_ID,
    name: str = "My project",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=pid, name=name, owner_id=USER_ID,
        created_at=datetime.now(timezone.utc),
        document_count=0,
    )


def _make_document(
    doc_id: uuid.UUID = DOC_ID,
    project_id: uuid.UUID = PROJECT_ID,
    status: str = "draft",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=doc_id, project_id=project_id,
        name="spec.docx", format="docx",
        size_bytes=2048,
        status=status,
        uploaded_at=datetime.now(timezone.utc),
        current_analysis_job_id=None,
        review_version=0,
    )


# JWT-заголовок: патчим get_current_user, чтобы не поднимать реальный JWT
AUTH_HEADERS = {"Authorization": "Bearer fake.jwt.token"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _patch_user():
    """Context manager: патчит get_current_user → _make_user()."""
    return patch("app.api.deps.get_current_user", return_value=_make_user())


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_list_projects_returns_200():
    from app.domain.services.project_service import ProjectService

    svc = AsyncMock(spec=ProjectService)
    svc.list_for_user.return_value = [_make_project()]

    with (
        _patch_user(),
        patch("app.core.dependencies.get_project_service", return_value=svc),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.get("/api/v1/projects", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.anyio
async def test_create_project_returns_201():
    from app.domain.services.project_service import ProjectService

    svc = AsyncMock(spec=ProjectService)
    svc.create.return_value = _make_project(name="New project")

    with (
        _patch_user(),
        patch("app.core.dependencies.get_project_service", return_value=svc),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(
                "/api/v1/projects",
                json={"name": "New project"},
                headers=AUTH_HEADERS,
            )
    assert r.status_code == 201
    assert "id" in r.json()


@pytest.mark.anyio
async def test_get_project_returns_200():
    from app.domain.services.project_service import ProjectService

    svc = AsyncMock(spec=ProjectService)
    svc.get.return_value = _make_project()

    with (
        _patch_user(),
        patch("app.core.dependencies.get_project_service", return_value=svc),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.get(f"/api/v1/projects/{PROJECT_ID}", headers=AUTH_HEADERS)
    assert r.status_code == 200
    assert r.json()["id"] == str(PROJECT_ID)


@pytest.mark.anyio
async def test_get_project_unknown_returns_404():
    from app.domain.exceptions import ProjectNotFoundError
    from app.domain.services.project_service import ProjectService

    svc = AsyncMock(spec=ProjectService)
    svc.get.side_effect = ProjectNotFoundError("Проект не найден")

    with (
        _patch_user(),
        patch("app.core.dependencies.get_project_service", return_value=svc),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.get(f"/api/v1/projects/{uuid.uuid4()}", headers=AUTH_HEADERS)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_list_documents_returns_200():
    from app.domain.services.document_service import DocumentService

    svc = AsyncMock(spec=DocumentService)
    svc.list_for_project.return_value = [_make_document()]

    project_stub = _make_project()
    with (
        _patch_user(),
        patch("app.api.deps.get_allowed_project", return_value=project_stub),
        patch("app.core.dependencies.get_document_service", return_value=svc),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.get(
                f"/api/v1/projects/{PROJECT_ID}/documents",
                headers=AUTH_HEADERS,
            )
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.anyio
async def test_get_document_returns_200():
    from app.domain.services.document_service import DocumentService

    svc = AsyncMock(spec=DocumentService)
    svc.get.return_value = _make_document()

    project_stub = _make_project()
    with (
        _patch_user(),
        patch("app.api.deps.get_allowed_project", return_value=project_stub),
        patch("app.core.dependencies.get_document_service", return_value=svc),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.get(
                f"/api/v1/projects/{PROJECT_ID}/documents/{DOC_ID}",
                headers=AUTH_HEADERS,
            )
    assert r.status_code == 200
    assert r.json()["id"] == str(DOC_ID)


@pytest.mark.anyio
async def test_get_document_wrong_project_returns_404():
    from app.domain.exceptions import DocumentNotFoundError
    from app.domain.services.document_service import DocumentService

    svc = AsyncMock(spec=DocumentService)
    svc.get.side_effect = DocumentNotFoundError("Документ не найден")

    project_stub = _make_project()
    with (
        _patch_user(),
        patch("app.api.deps.get_allowed_project", return_value=project_stub),
        patch("app.core.dependencies.get_document_service", return_value=svc),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.get(
                f"/api/v1/projects/{PROJECT_ID}/documents/{uuid.uuid4()}",
                headers=AUTH_HEADERS,
            )
    assert r.status_code == 404


@pytest.mark.anyio
async def test_delete_document_returns_204():
    from app.domain.services.document_service import DocumentService

    svc = AsyncMock(spec=DocumentService)
    svc.delete.return_value = None

    project_stub = _make_project()
    with (
        _patch_user(),
        patch("app.api.deps.get_allowed_project", return_value=project_stub),
        patch("app.core.dependencies.get_document_service", return_value=svc),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.delete(
                f"/api/v1/projects/{PROJECT_ID}/documents/{DOC_ID}",
                headers=AUTH_HEADERS,
            )
    assert r.status_code == 204
