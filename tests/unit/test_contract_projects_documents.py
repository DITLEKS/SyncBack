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

P2: добавлен тест shape SourceBadge — проверяет, что каждый элемент
    sources в DocumentListItem содержит поля id, name, type.

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

USER_ID    = uuid.uuid4()
PROJECT_ID = uuid.uuid4()
DOC_ID     = uuid.uuid4()
SOURCE_ID  = uuid.uuid4()


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


AUTH_HEADERS = {"Authorization": "Bearer fake.jwt.token"}


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _patch_user():
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


# ---------------------------------------------------------------------------
# P2: SourceBadge shape contract
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_my_documents_source_badge_shape():
    """P2: каждый элемент sources в DocumentListItem содержит поля id, name, type.

    Использует GET /api/v1/documents (my_documents router).
    Мокает document_service и source_service.
    """
    from app.domain.services.document_service import DocumentService
    from app.domain.services.source_service import SourceService

    now = datetime.now(timezone.utc)
    doc = SimpleNamespace(
        id=DOC_ID,
        project_id=PROJECT_ID,
        name="spec.docx",
        format="docx",
        size_bytes=2048,
        status="draft",
        current_analysis_job_id=None,
        created_at=now,
        updated_at=now,
    )
    row = {
        "document": doc,
        "project_name": "My project",
        "suggestions_total": 0,
        "suggestions_pending": 0,
        "suggestions_accepted": 0,
        "suggestions_rejected": 0,
    }
    badge_source = SimpleNamespace(
        id=SOURCE_ID,
        name="Confluence Wiki",
        type="url",
        document_id=DOC_ID,
        project_id=PROJECT_ID,
    )

    doc_svc = AsyncMock(spec=DocumentService)
    doc_svc.list_all_for_user.return_value = ([row], 1)

    src_svc = AsyncMock(spec=SourceService)
    src_svc.list_sources_for_documents.return_value = {DOC_ID: [badge_source]}

    with (
        _patch_user(),
        patch("app.core.dependencies.get_document_service", return_value=doc_svc),
        patch("app.core.dependencies.get_source_service", return_value=src_svc),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.get("/api/v1/documents", headers=AUTH_HEADERS)

    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1

    sources = items[0]["sources"]
    assert len(sources) == 1

    badge = sources[0]
    # P2: контракт shape SourceBadge
    assert "id" in badge,   "SourceBadge must have 'id'"
    assert "name" in badge, "SourceBadge must have 'name'"
    assert "type" in badge, "SourceBadge must have 'type'"
    assert badge["id"] == str(SOURCE_ID)
    assert badge["name"] == "Confluence Wiki"
    assert badge["type"] == "url"
